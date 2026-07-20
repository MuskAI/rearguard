import os
import secrets
import time
from datetime import timedelta
from urllib.parse import urlsplit

import click
from flask import Flask, abort, jsonify, render_template, request, send_from_directory, session, redirect
from .views import detection
from .views import login
from .views import historical_record
from .views import profile
from .views import api
from .views import admin
from .views import admin_state
from .views import developer_platform
from .views import utils


def _session_secret():
    value = (os.environ.get('SECRET_KEY') or os.environ.get('REALGUARD_SECRET_KEY') or '').strip()
    if value:
        lowered = value.lower()
        if (
            len(value.encode('utf-8')) < 32
            or lowered in {'change-me', 'changeme', 'secret', 'test', 'development'}
            or lowered.startswith(('change-', 'replace-', 'example-', 'your-'))
        ):
            raise RuntimeError('Flask session signing key is weak or uses a template value')
        return value
    allow_ephemeral = os.environ.get('REALGUARD_ALLOW_EPHEMERAL_SECRET', '0').strip().lower()
    if allow_ephemeral in {'1', 'true', 'yes', 'on'}:
        return secrets.token_urlsafe(48)
    raise RuntimeError('Flask session signing key is required')


def creat_app():
    app = Flask(__name__)
    app.secret_key = _session_secret()
    app.config.update(
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=str(os.environ.get('REALGUARD_SESSION_COOKIE_SECURE', '1')).lower() in ('1', 'true', 'yes'),
        SESSION_REFRESH_EACH_REQUEST=True,
    )

    app.register_blueprint(detection.image_upload_blueprint)
    app.register_blueprint(login.login_blueprint)
    app.register_blueprint(historical_record.historical_record_blueprint)
    app.register_blueprint(profile.profile_blueprint)
    app.register_blueprint(api.api_blueprint)
    app.register_blueprint(admin.admin_blueprint)
    app.register_blueprint(developer_platform.developer_platform_blueprint)
    app.register_blueprint(developer_platform.openapi_blueprint)
    app.register_blueprint(developer_platform.developer_admin_blueprint)

    @app.before_request
    def validate_account_session():
        if request.path.startswith('/static/'):
            return None
        login.validate_current_user_session(allow_legacy=bool(app.testing))
        return None

    @app.before_request
    def reject_cross_site_browser_writes():
        """Reject CSRF-shaped browser writes outside token-authenticated APIs."""
        if request.method in ('GET', 'HEAD', 'OPTIONS', 'TRACE'):
            return None
        if request.path.startswith('/api/openapi/') or request.path == '/api/developer/keys/verify':
            return None

        fetch_site = str(request.headers.get('Sec-Fetch-Site') or '').strip().lower()
        if fetch_site in ('cross-site', 'none'):
            return jsonify({'status': 'error', 'message': '拒绝跨站请求'}), 403

        allowed_origins = {
            item.strip().rstrip('/')
            for item in os.environ.get('REALGUARD_ALLOWED_ORIGINS', '').split(',')
            if item.strip()
        }
        allowed_origins.add(request.host_url.rstrip('/'))

        source = request.headers.get('Origin') or request.headers.get('Referer') or ''
        if source:
            parsed = urlsplit(source)
            source_origin = f'{parsed.scheme}://{parsed.netloc}'.rstrip('/')
            if not parsed.scheme or not parsed.netloc or source_origin not in allowed_origins:
                return jsonify({'status': 'error', 'message': '拒绝跨站请求'}), 403
        return None

    @app.before_request
    def protect_all_admin_api_writes():
        """Cover admin APIs registered on blueprints other than admin_blueprint."""
        admin.ensure_alert_worker(app)
        if not request.path.startswith('/api/admin/'):
            return None
        if request.method in ('GET', 'HEAD', 'OPTIONS', 'TRACE'):
            return None
        if not admin._csrf_valid():
            return admin._csrf_error_response()
        return None

    @app.after_request
    def prevent_sensitive_response_caching(response):
        if request.path.startswith(('/api/', '/image_upload/', '/video_upload/', '/sms/', '/admin/')):
            response.headers['Cache-Control'] = 'private, no-store, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.vary.add('Cookie')
            response.vary.add('Authorization')
        return response

    @app.cli.command("admin-db-upgrade")
    def admin_db_upgrade():
        """Create or update RealGuard admin tables."""
        ok, messages = admin.apply_admin_schema()
        for message in messages:
            click.echo(message)
        if not ok:
            raise click.ClickException("admin schema upgrade failed")
        click.echo("admin schema ready")

    @app.cli.command("identity-db-upgrade")
    def identity_db_upgrade():
        """Create immutable account ownership without guessing legacy owners."""
        try:
            changes = utils.apply_account_identity_schema()
        except Exception as exc:
            raise click.ClickException(f"identity schema upgrade failed: {exc}") from exc
        for label, count in changes.items():
            click.echo(f"{label}: {count}")
        click.echo("identity schema ready")

    @app.cli.command("developer-db-upgrade")
    def developer_db_upgrade():
        """Create or update developer API and billing tables."""
        checks = (
            ("api keys", api._ensure_developer_api_key_table),
            ("usage events", api._ensure_developer_usage_table),
            ("billing", developer_platform._ensure_developer_platform_tables),
        )
        for label, upgrade in checks:
            if not upgrade():
                raise click.ClickException(f"developer schema upgrade failed: {label}")
            click.echo(f"developer schema ready: {label}")

    @app.cli.command("reconcile-detection-jobs")
    def reconcile_detection_jobs():
        """Fail legacy in-process jobs while preserving durable queued work."""
        try:
            preserved = developer_platform._active_web_task_ids()
        except Exception as exc:
            raise click.ClickException(f"durable Web task lookup failed: {exc}") from exc
        changed = admin_state.reconcile_interrupted_detection_jobs(preserve_ids=preserved)
        click.echo(f"durable detection jobs preserved: {len(preserved)}")
        click.echo(f"interrupted detection jobs reconciled: {changed}")

    @app.cli.command("alert-worker")
    @click.option("--once", is_flag=True, help="Run one alert cycle and exit.")
    def alert_worker(once):
        """Run alert delivery independently from Web request workers."""
        interval = max(15, int(os.environ.get("REALGUARD_ALERT_INTERVAL_SECONDS", "30")))
        while True:
            try:
                admin.write_alert_worker_heartbeat("running")
                deliveries = admin.run_alert_cycle()
                admin.write_alert_worker_heartbeat("running")
                click.echo(f"alert cycle complete: {len(deliveries)} deliveries")
            except Exception as exc:
                try:
                    admin.write_alert_worker_heartbeat("error", str(exc))
                except Exception:
                    pass
                if once:
                    raise click.ClickException(f"alert cycle failed: {exc}") from exc
                click.echo(f"alert cycle failed: {exc}", err=True)
            if once:
                return
            time.sleep(interval)

    @app.cli.command("alert-watchdog")
    def alert_watchdog():
        """Check the independent alert worker's heartbeat once."""
        try:
            deliveries = admin.run_alert_watchdog_cycle()
        except Exception as exc:
            raise click.ClickException(f"alert watchdog failed: {exc}") from exc
        click.echo(f"alert watchdog complete: {len(deliveries)} deliveries")

    @app.cli.command("security-audit-verify")
    @click.option("--bootstrap", is_flag=True, help="Create the first monotonic checkpoint.")
    def security_audit_verify(bootstrap):
        """Verify the HMAC audit chain and monotonic checkpoint."""
        result = api.verify_security_audit_chain(allow_bootstrap=bootstrap)
        click.echo(f"security audit verification: {result.get('state')}")
        if result.get("state") != "passed":
            raise click.ClickException(result.get("lastError") or "security audit verification failed")

    @app.cli.command("repair-detection-owners")
    def repair_detection_owners():
        """Repair cross-database history owner IDs using verified identities."""
        try:
            changes = utils.repair_detection_history_owners()
        except Exception as exc:
            raise click.ClickException(f"detection owner repair failed: {exc}") from exc
        for table, count in changes.items():
            click.echo(f"{table}: repaired {count} owner rows")

    @app.cli.command("create-admin")
    @click.option("--username", prompt=True, help="Admin username.")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True, help="Admin password.")
    @click.option("--phone", default="", help="Optional phone number.")
    @click.option(
        "--role",
        default="super_admin",
        type=click.Choice(["super_admin", "admin", "operator", "reviewer", "readonly"]),
        help="Admin role.",
    )
    @click.option("--migrate/--no-migrate", default=False, help="Run admin-db-upgrade before creating the account.")
    def create_admin(username, password, phone, role, migrate):
        """Create an administrator account from the server CLI."""
        if migrate:
            ok, messages = admin.apply_admin_schema()
            for message in messages:
                click.echo(message)
            if not ok:
                raise click.ClickException("admin schema upgrade failed")
        ok, message = admin._create_admin_account(username, phone, password, role=role)
        if not ok:
            raise click.ClickException(message)
        click.echo(f"created admin {username} ({role})")

    @app.route('/')
    def root():
        if 'user_info' not in session or session['user_info'] is None:
            return redirect('/login')
        return redirect('/index')

    @app.route('/index')
    def index():
        if 'user_info' not in session or session['user_info'] is None:
            return render_template('login.html')
        return render_template('index.html')

    @app.route('/introduce')
    def introduce():
        if 'user_info' not in session or session['user_info'] is None:
            return render_template('login.html')
        return render_template('introduce.html')

    @app.route('/legal/<path:filename>')
    def legal_file(filename):
        if filename not in ('terms.html', 'privacy.html'):
            abort(404)
        legal_dirs = [
            os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'frontend', 'public', 'legal')),
            '/var/www/realguard-frontend/legal',
        ]
        for legal_dir in legal_dirs:
            if os.path.exists(os.path.join(legal_dir, filename)):
                return send_from_directory(legal_dir, filename)
        abort(404)

    return app

import os
from datetime import timedelta

import click
from flask import Flask, abort, render_template, send_from_directory, session, redirect
from .views import detection
from .views import login
from .views import historical_record
from .views import profile
from .views import api
from .views import admin


def creat_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get('SECRET_KEY') or os.environ.get('REALGUARD_SECRET_KEY') or 'gdq821821'
    app.config.update(
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=str(os.environ.get('REALGUARD_SESSION_COOKIE_SECURE', '0')).lower() in ('1', 'true', 'yes'),
        SESSION_REFRESH_EACH_REQUEST=True,
    )

    app.register_blueprint(detection.image_upload_blueprint)
    app.register_blueprint(login.login_blueprint)
    app.register_blueprint(historical_record.historical_record_blueprint)
    app.register_blueprint(profile.profile_blueprint)
    app.register_blueprint(api.api_blueprint)
    app.register_blueprint(admin.admin_blueprint)

    @app.cli.command("admin-db-upgrade")
    def admin_db_upgrade():
        """Create or update RealGuard admin tables."""
        ok, messages = admin.apply_admin_schema()
        for message in messages:
            click.echo(message)
        if not ok:
            raise click.ClickException("admin schema upgrade failed")
        click.echo("admin schema ready")

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

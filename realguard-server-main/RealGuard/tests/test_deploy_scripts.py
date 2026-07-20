from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[3]
DEPLOY_COMMON = ROOT / "scripts" / "deploy_common.sh"


def _run_bash(body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f'source "{DEPLOY_COMMON}"\nset +e\n{body}'],
        check=False,
        capture_output=True,
        text=True,
    )


def test_remote_command_failure_is_not_retried_as_transport_failure():
    completed = _run_bash(
        """
calls=0
ssh() { calls=$((calls + 1)); return 42; }
run_ssh_transport ssh
status=$?
printf '%s %s\n' "$status" "$calls"
"""
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "42 1"
    assert "retrying" not in completed.stderr


def test_ssh_activation_transport_failure_is_not_retried():
    completed = _run_bash(
        """
calls=0
sleep() { :; }
ssh() {
  calls=$((calls + 1))
  if [[ "$calls" = "1" ]]; then return 255; fi
  return 0
}
run_ssh_transport ssh
status=$?
printf '%s %s\n' "$status" "$calls"
"""
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "255 1"
    assert "retrying" not in completed.stderr


def test_nginx_rate_limit_response_is_machine_readable_and_retryable():
    configs = (
        ROOT / "deploy" / "nginx" / "realguard.conf",
        ROOT / "realguard-server-main" / "deploy" / "nginx-realguard-frontend.conf",
    )

    for config in configs:
        body = config.read_text(encoding="utf-8")
        assert "error_page 429 = @realguard_rate_limited;" in body
        assert 'add_header Retry-After "2" always;' in body
        assert '"code":"rate_limited"' in body


def test_public_default_nginx_rejects_ip_host_and_internal_preview_is_loopback_only():
    body = (
        ROOT / "realguard-server-main" / "deploy" / "nginx-realguard-frontend.conf"
    ).read_text(encoding="utf-8")

    default_server = body.split("server {", 2)[1]
    assert "listen 80 default_server;" in default_server
    assert "server_name _;" in default_server
    assert "return 444;" in default_server
    assert "listen 127.0.0.1:8081;" in body
    assert "listen 8081;" not in body


def test_big_screen_page_disables_access_log_and_never_uses_query_token_auth():
    for config in (
        ROOT / "deploy" / "nginx" / "realguard.conf",
        ROOT / "realguard-server-main" / "deploy" / "nginx-realguard-frontend.conf",
    ):
        body = config.read_text(encoding="utf-8")
        location = body.split("location = /admin/screen", 1)[1].split("}", 1)[0]
        assert "access_log off;" in location
    admin_body = (
        ROOT / "realguard-server-main" / "RealGuard" / "imagedetection" / "views" / "admin.py"
    ).read_text(encoding="utf-8")
    token_reader = admin_body.split("def _screen_token_from_request", 1)[1].split("def _configured_screen_token_digest", 1)[0]
    assert "request.args" not in token_reader


def test_deploy_converge_consumes_current_gpu_status_contract():
    body = (ROOT / "scripts" / "deploy_converge.sh").read_text(encoding="utf-8")

    for field in (
        "gpu_model_service",
        "gpu_watermark_service",
        "gpu_yolo_service",
        "gpu_model_tunnel",
        "gpu_precheck_tunnel",
        "gpu_model_runtime",
        "gpu_public_detector_service",
        "gpu_public_runtime",
    ):
        assert f"s/^{field}=//p" in body
    assert "s/^gpu_service=//p" not in body
    assert "s/^gpu_internal_http=//p" not in body
    assert "s/^gpu_external_http=//p" not in body


def test_production_services_require_critical_environment_files():
    units = (
        ROOT / "deploy" / "systemd" / "realguard-backend.service",
        ROOT / "deploy" / "systemd" / "realguard-detector-backend.service",
        ROOT / "deploy" / "systemd" / "realguard-developer-worker.service",
        ROOT / "deploy" / "systemd" / "realguard-alert-worker.service",
        ROOT / "deploy" / "systemd" / "realguard-alert-watchdog.service",
        ROOT / "deploy" / "systemd" / "realguard-security-audit-verify.service",
        ROOT / "deploy" / "systemd" / "realguard-backup.service",
        ROOT / "deploy" / "systemd" / "realguard-restore-drill.service",
        ROOT / "deploy" / "systemd" / "jianzhen-v2-backend.service",
    )

    for unit in units:
        body = unit.read_text(encoding="utf-8")
        critical_lines = [
            line for line in body.splitlines()
            if line.startswith("EnvironmentFile=") and "sms.env" not in line
        ]
        assert critical_lines
        assert all("EnvironmentFile=-" not in line for line in critical_lines)


def test_alert_delivery_and_restore_drills_are_independent_services():
    web = (ROOT / "deploy" / "systemd" / "realguard-backend.service").read_text(encoding="utf-8")
    alert = (ROOT / "deploy" / "systemd" / "realguard-alert-worker.service").read_text(encoding="utf-8")
    restore_timer = (ROOT / "deploy" / "systemd" / "realguard-restore-drill.timer").read_text(encoding="utf-8")
    watchdog_timer = (ROOT / "deploy" / "systemd" / "realguard-alert-watchdog.timer").read_text(encoding="utf-8")
    audit_timer = (ROOT / "deploy" / "systemd" / "realguard-security-audit-verify.timer").read_text(encoding="utf-8")
    deploy = (ROOT / "scripts" / "deploy_v1.sh").read_text(encoding="utf-8")
    activate = (ROOT / "scripts" / "remote" / "activate_v1.sh").read_text(encoding="utf-8")

    assert "Environment=REALGUARD_ALERT_WORKER_ENABLED=0" in web
    assert "--app run:app alert-worker" in alert
    assert "Restart=always" in alert
    assert "Unit=realguard-restore-drill.service" in restore_timer
    assert "Unit=realguard-alert-watchdog.service" in watchdog_timer
    assert "Unit=realguard-security-audit-verify.service" in audit_timer
    assert "realguard-alert-worker.service" in deploy
    assert "realguard-restore-drill.timer" in deploy
    assert "realguard-alert-worker.service" in activate
    assert "realguard-restore-drill.timer" in activate
    assert "realguard-alert-watchdog.timer" in activate
    assert "realguard-security-audit-verify.timer" in activate


def test_release_directories_are_unique_for_repeated_commit_deployments():
    scripts = (
        ROOT / "scripts" / "remote" / "activate_v1.sh",
        ROOT / "scripts" / "remote" / "activate_v2.sh",
    )

    for script in scripts:
        body = script.read_text(encoding="utf-8")
        assert 'release_id="${commit_sha}-$(date -u +%Y%m%dT%H%M%SZ)-$$"' in body
        assert 'releases/$release_id' in body
        assert 'releases/$commit_sha"' not in body

    gpu_activate = (
        ROOT / "scripts" / "remote" / "activate_detection_service.sh"
    ).read_text(encoding="utf-8")
    assert 'rollback_unit="realguard-gpu-deploy-rollback-${commit_sha}-$$"' in gpu_activate


def test_public_report_share_credentials_are_not_written_to_access_log():
    configs = (
        ROOT / "deploy" / "nginx" / "realguard.conf",
        ROOT / "realguard-server-main" / "deploy" / "nginx-realguard-frontend.conf",
    )

    for config in configs:
        body = config.read_text(encoding="utf-8")
        location = body.split("location ~ ^/v2-api/report/[^/]+/public$", 1)[1].split("}", 1)[0]
        assert "access_log off;" in location
        assert "realguard-public-report-headers.conf" in location


def test_gpu_deploy_contract_checks_response_signing_key_id():
    deploy = (ROOT / "scripts" / "deploy_detection_service.sh").read_text(encoding="utf-8")
    gpu_activate = (
        ROOT / "scripts" / "remote" / "activate_detection_service.sh"
    ).read_text(encoding="utf-8")
    public_activate = (
        ROOT / "scripts" / "remote" / "activate_public_gpu_deploy.sh"
    ).read_text(encoding="utf-8")

    assert 'test "$public_response_key_id" = "$gpu_response_key_id"' in deploy
    assert 'responseIntegrityKeyId") == sys.argv[4]' in gpu_activate
    assert "REALGUARD_MODEL_RESPONSE_HMAC_KEY_ID" in public_activate


def test_first_gpu_deploy_rollback_handles_missing_public_marker():
    rollback = (
        ROOT / "scripts" / "remote" / "rollback_public_gpu_deploy.sh"
    ).read_text(encoding="utf-8")
    gpu_activate = (
        ROOT / "scripts" / "remote" / "activate_detection_service.sh"
    ).read_text(encoding="utf-8")

    assert 'if [[ -f "$marker_target" ]]; then' in rollback
    assert "Port 5000 is still owned by a process outside" in gpu_activate


def test_gpu_rollout_watchdogs_validate_the_tunnel_without_new_web_code():
    deploy = (ROOT / "scripts" / "deploy_detection_service.sh").read_text(encoding="utf-8")
    gpu_rollback = (
        ROOT / "scripts" / "remote" / "rollback_detection_service.sh"
    ).read_text(encoding="utf-8")
    public_rollback = (
        ROOT / "scripts" / "remote" / "rollback_public_gpu_deploy.sh"
    ).read_text(encoding="utf-8")

    assert "http://127.0.0.1:15000/internal/model/health" in deploy
    assert "http://127.0.0.1:5000/internal/model/health" in gpu_rollback
    assert "REALGUARD_PUBLIC_READY_URL" not in gpu_rollback
    assert "http://127.0.0.1:15000/internal/model/health" in public_rollback
    assert "http://127.0.0.1:15001/health" not in public_rollback


def test_gpu_status_does_not_require_persistent_sudo_access():
    status = (ROOT / "scripts" / "check_deploy_status.sh").read_text(encoding="utf-8")

    assert "systemctl show realguard-detection.service -p MainPID" in status
    assert '"/proc/$model_pid/environ"' in status
    gpu_block = status.split("gpu_output=", 1)[1].split("public_gpu_output=", 1)[0]
    assert "sudo awk" not in gpu_block


def test_v1_deployment_identity_tracks_release_inputs_not_tests():
    deploy = (ROOT / "scripts" / "deploy_v1.sh").read_text(encoding="utf-8")
    status = (ROOT / "scripts" / "check_deploy_status.sh").read_text(encoding="utf-8")

    for path in (
        "realguard-server-main/RealGuard/model_decision_contract.py",
        "realguard-server-main/RealGuard/requirements.lock",
        "realguard-server-main/RealGuard/imagedetection",
        "scripts/remote/activate_v1.sh",
        "deploy/systemd",
    ):
        assert path in deploy
        assert path in status
    assert "realguard-server-main/RealGuard\n" not in deploy


def test_v1_release_can_build_a_pinned_runtime_as_the_service_user():
    deploy = (ROOT / "scripts" / "deploy_v1.sh").read_text(encoding="utf-8")
    activate = (ROOT / "scripts" / "remote" / "activate_v1.sh").read_text(
        encoding="utf-8"
    )

    assert "run.py detector_backend.py model_decision_contract.py" in deploy
    assert "requirements.txt requirements.lock imagedetection" in deploy
    assert "--retry-all-errors" in deploy
    assert "--connect-timeout 15" in deploy
    assert "Nginx must not listen on the internal V1 application port 5000" in activate
    assert "nginx -T" in activate
    assert (
        'sudo install -d -m 755 -o ubuntu -g ubuntu "$release_root" '
        '"$release_root/RealGuard"'
    ) in activate
    assert '.venv/bin/python -c "import run, detector_backend"' in activate
    assert activate.index('.venv/bin/python -c "import run, detector_backend"') < activate.index(
        "systemctl stop realguard-developer-worker.service"
    )
    assert 'Image.new("RGB", (64, 64)' in activate
    assert "iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB" not in activate

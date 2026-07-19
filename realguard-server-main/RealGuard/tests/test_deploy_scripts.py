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


def test_ssh_transport_failure_is_retried():
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
    assert completed.stdout.strip() == "0 2"
    assert "retrying (1/5)" in completed.stderr


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

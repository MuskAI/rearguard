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

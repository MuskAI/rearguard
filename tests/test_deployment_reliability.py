from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_COMMON = ROOT / "scripts" / "deploy_common.sh"
SERVICE_NAMES = (
    "realguard-backend.service",
    "realguard-detector-backend.service",
    "jianzhen-v2-backend.service",
)


def _unit_dependencies(service_name: str) -> set[str]:
    dependencies: set[str] = set()
    body = (ROOT / "deploy" / "systemd" / service_name).read_text(encoding="utf-8")
    for line in body.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"After", "Requires", "Wants"}:
            dependencies.update(value.split())
    return dependencies


def _run_deploy_common(body: str, *, known_hosts: Path) -> subprocess.CompletedProcess[str]:
    script = f"""
export DEPLOY_SSH_KEY=/tmp/test-deploy-key
export DEPLOY_KNOWN_HOSTS_FILE={known_hosts!s}
source {DEPLOY_COMMON!s}
{body}
"""
    return subprocess.run(
        ["bash", "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )


def test_core_services_have_no_systemd_dependency_cycle() -> None:
    graph = {
        service: _unit_dependencies(service).intersection(SERVICE_NAMES)
        for service in SERVICE_NAMES
    }

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(service: str) -> None:
        assert service not in visiting, f"systemd dependency cycle includes {service}"
        if service in visited:
            return
        visiting.add(service)
        for dependency in graph[service]:
            visit(dependency)
        visiting.remove(service)
        visited.add(service)

    for service in SERVICE_NAMES:
        visit(service)

    assert all(not dependencies for dependencies in graph.values())


def test_all_ssh_entrypoints_enforce_custom_known_hosts_file(tmp_path: Path) -> None:
    known_hosts = tmp_path / "production_known_hosts"
    known_hosts.write_text("example.invalid ssh-ed25519 AAAATEST\n", encoding="utf-8")
    completed = _run_deploy_common(
        r'''
ssh() { printf 'ssh:%s\n' "$*"; }
scp() { printf 'scp:%s\n' "$*"; }
run_scp artifact.tar ubuntu@example.invalid:/tmp/artifact.tar
run_remote 'systemctl is-active realguard-backend'
run_remote_capture 'systemctl is-active jianzhen-v2-backend'
''',
        known_hosts=known_hosts,
    )

    assert completed.returncode == 0, completed.stderr
    invocations = [line for line in completed.stdout.splitlines() if line]
    assert len(invocations) == 3
    for invocation in invocations:
        assert "StrictHostKeyChecking=yes" in invocation
        assert f"UserKnownHostsFile={known_hosts}" in invocation
        assert "StrictHostKeyChecking=no" not in invocation


def test_missing_known_hosts_file_fails_closed_before_transport(tmp_path: Path) -> None:
    missing_known_hosts = tmp_path / "missing_known_hosts"
    completed = _run_deploy_common(
        r'''
ssh() { printf 'UNSAFE_TRANSPORT_CALLED\n'; }
run_remote 'true'
''',
        known_hosts=missing_known_hosts,
    )

    assert completed.returncode != 0
    assert "UNSAFE_TRANSPORT_CALLED" not in completed.stdout
    assert "must be a readable file" in completed.stderr


def test_deploy_script_contains_no_insecure_host_key_fallback() -> None:
    body = DEPLOY_COMMON.read_text(encoding="utf-8")

    assert "StrictHostKeyChecking=no" not in body
    assert "StrictHostKeyChecking=accept-new" not in body


def test_v2_status_manifest_covers_unit_and_activation_script() -> None:
    source = (ROOT / "scripts" / "check_deploy_status.sh").read_text(encoding="utf-8")
    v2_block = source.split('if [[ "$TARGET" == "all" || "$TARGET" == "v2" ]]', 1)[1].split(
        'if [[ "$TARGET" == "all" || "$TARGET" == "gpu" ]]', 1
    )[0]

    assert 'deploy/systemd/jianzhen-v2-backend.service' in v2_block
    assert 'scripts/remote/activate_v2.sh' in v2_block


def test_activation_scripts_lock_and_v2_runtime_rolls_back_atomically() -> None:
    v1 = (ROOT / "scripts" / "remote" / "activate_v1.sh").read_text(encoding="utf-8")
    v2 = (ROOT / "scripts" / "remote" / "activate_v2.sh").read_text(encoding="utf-8")

    assert "flock -n 9" in v1
    assert "flock -n 9" in v2
    assert 'python3 -m venv "$release_root/.venv"' in v2
    assert '/opt/jianzhen-v2/.venv.next' in v2
    assert 'runtime_switched=1' in v2


def test_gpu_deploy_and_status_do_not_use_tofu_host_keys() -> None:
    deploy = (ROOT / "scripts" / "deploy_detection_service.sh").read_text(encoding="utf-8")
    status = (ROOT / "scripts" / "check_deploy_status.sh").read_text(encoding="utf-8")

    assert "StrictHostKeyChecking=accept-new" not in deploy
    assert "StrictHostKeyChecking=accept-new" not in status
    assert "StrictHostKeyChecking=yes" in deploy
    assert "StrictHostKeyChecking=yes" in status

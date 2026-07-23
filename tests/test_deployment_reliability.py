from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_COMMON = ROOT / "scripts" / "deploy_common.sh"
SERVICE_NAMES = (
    "realguard-backend.service",
    "realguard-detector-backend.service",
    "jianzhen-v2-backend.service",
)
BACKUP_SCRIPT = ROOT / "scripts" / "remote" / "backup_realguard.sh"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _backup_test_environment(
    tmp_path: Path, *, flock_exit: int = 0
) -> tuple[dict[str, str], Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "flock",
        f"#!/bin/sh\nexit {flock_exit}\n",
    )
    _write_executable(
        fake_bin / "date",
        """#!/bin/sh
if [ "${1:-}" = "-u" ] && [ "${2:-}" = "-d" ]; then
  printf '%s\\n' 20250101T000000Z
  exit 0
fi
exec /bin/date "$@"
""",
    )
    _write_executable(
        fake_bin / "mysqldump",
        "#!/bin/sh\nprintf '%s\\n' '-- deterministic test dump'\n",
    )
    backup_root = tmp_path / "backups"
    status_file = tmp_path / "backup-status.json"
    privacy_ledger = tmp_path / "privacy-erasure.sqlite3"
    with sqlite3.connect(privacy_ledger) as connection:
        connection.execute("CREATE TABLE tombstones (id TEXT PRIMARY KEY)")
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "PYTHON_BIN": sys.executable,
            "REALGUARD_BACKUP_ROOT": str(backup_root),
            "REALGUARD_BACKUP_STATUS_FILE": str(status_file),
            "REALGUARD_BACKUP_RETENTION_DAYS": "14",
            "REALGUARD_BACKUP_MAX_SNAPSHOTS": "3",
            "REALGUARD_BACKUP_MAX_ROOT_BYTES": "1099511627776",
            "REALGUARD_BACKUP_MIN_FREE_BYTES": "0",
            "REALGUARD_BACKUP_MIN_FREE_PERCENT": "0",
            "REALGUARD_BACKUP_MIN_STAGING_BYTES": "0",
            "REALGUARD_DB_USER": "backup-test",
            "REALGUARD_DETECTION_DB_USER": "backup-test",
            "JIANZHEN_DB_PATH": str(tmp_path / "missing-v2.sqlite3"),
            "REALGUARD_TRAFFIC_CUMULATIVE_DB": str(
                tmp_path / "missing-traffic.sqlite3"
            ),
            "REALGUARD_PRIVACY_ERASURE_LEDGER_PATH": str(privacy_ledger),
            "REALGUARD_UPLOADS_DIR": str(tmp_path / "missing-uploads"),
            "REALGUARD_EVIDENCE_SNAPSHOT_ROOT": str(
                tmp_path / "missing-evidence"
            ),
            "REALGUARD_LEGACY_EVIDENCE_ROOT": str(
                tmp_path / "missing-legacy-evidence"
            ),
        }
    )
    return environment, backup_root, status_file


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


def test_backup_capacity_gate_precedes_staging_and_manifest_covers_admin_state() -> None:
    body = BACKUP_SCRIPT.read_text(encoding="utf-8")

    assert body.index('enforce_capacity "$estimated_staging_bytes" 1') < body.index(
        'mktemp -d "$BACKUP_ROOT/.staging-${timestamp}.XXXXXX"'
    )
    assert "REALGUARD_BACKUP_MAX_SNAPSHOTS" in body
    assert "REALGUARD_BACKUP_MAX_ROOT_BYTES" in body
    assert "REALGUARD_BACKUP_MIN_FREE_BYTES" in body
    assert "REALGUARD_BACKUP_MIN_FREE_PERCENT" in body
    assert 'cp -p "$ADMIN_STATE_FILE" "$staging/admin_state.json"' in body
    assert "admin_state_backed_up=$admin_state_backed_up" in body


def test_backup_lock_conflict_is_nonzero_and_preserves_running_status(
    tmp_path: Path,
) -> None:
    environment, _, status_file = _backup_test_environment(tmp_path, flock_exit=1)
    running_status = '{"state":"running","runStartedAt":"previous"}\n'
    status_file.write_text(running_status, encoding="utf-8")

    completed = subprocess.run(
        ["bash", str(BACKUP_SCRIPT)],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 75
    assert "backup lock is busy" in completed.stderr
    assert status_file.read_text(encoding="utf-8") == running_status


def test_backup_preflight_cleans_only_expired_canonical_snapshots_and_never_stages(
    tmp_path: Path,
) -> None:
    environment, backup_root, status_file = _backup_test_environment(tmp_path)
    backup_root.mkdir()
    expired = backup_root / "20200101T000000Z"
    protected_latest = backup_root / "20210101T000000Z"
    manual_directory = backup_root / "manual-copy"
    active_staging = backup_root / ".staging-inflight"
    for directory in (expired, protected_latest, manual_directory, active_staging):
        directory.mkdir()
    (backup_root / "latest").symlink_to(protected_latest.name)
    environment["REALGUARD_BACKUP_MAX_ROOT_BYTES"] = "1"
    environment["REALGUARD_BACKUP_MIN_STAGING_BYTES"] = "2"

    completed = subprocess.run(
        ["bash", str(BACKUP_SCRIPT)],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "Backup capacity preflight failed" in completed.stderr
    assert not expired.exists()
    assert protected_latest.is_dir()
    assert manual_directory.is_dir()
    assert active_staging.is_dir()
    assert [path.name for path in backup_root.glob(".staging-*")] == [
        active_staging.name
    ]
    assert json.loads(status_file.read_text(encoding="utf-8"))["state"] == "failed"


def test_backup_rejects_invalid_capacity_values_before_creating_backup_root(
    tmp_path: Path,
) -> None:
    environment, backup_root, status_file = _backup_test_environment(tmp_path)
    environment["REALGUARD_BACKUP_MAX_ROOT_BYTES"] = "16GiB"

    completed = subprocess.run(
        ["bash", str(BACKUP_SCRIPT)],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 64
    assert "must be an integer" in completed.stderr
    assert not backup_root.exists()
    assert not status_file.exists()


def test_backup_rejects_oversized_numeric_capacity_without_arithmetic_wrap(
    tmp_path: Path,
) -> None:
    environment, backup_root, status_file = _backup_test_environment(tmp_path)
    environment["REALGUARD_BACKUP_MIN_FREE_BYTES"] = "999999999999999999999999"

    completed = subprocess.run(
        ["bash", str(BACKUP_SCRIPT)],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 64
    assert "must be an integer" in completed.stderr
    assert not backup_root.exists()
    assert not status_file.exists()


def test_backup_includes_admin_state_in_manifest_and_checksums(tmp_path: Path) -> None:
    environment, backup_root, status_file = _backup_test_environment(tmp_path)
    admin_state = tmp_path / "admin_state.json"
    admin_state.write_text('{"version":1}\n', encoding="utf-8")
    environment["REALGUARD_ADMIN_STATE_PATH"] = str(admin_state)
    backup_root.mkdir()
    previous_latest = backup_root / "20210101T000000Z"
    previous_latest.mkdir()
    (backup_root / "latest").symlink_to(previous_latest.name)
    environment["REALGUARD_BACKUP_MAX_SNAPSHOTS"] = "1"

    completed = subprocess.run(
        ["bash", str(BACKUP_SCRIPT)],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    snapshots = [
        path
        for path in backup_root.iterdir()
        if path.is_dir() and path.name.startswith("20")
    ]
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot != previous_latest
    assert (backup_root / "latest").resolve() == snapshot.resolve()
    assert (snapshot / "admin_state.json").read_text(encoding="utf-8") == (
        admin_state.read_text(encoding="utf-8")
    )
    manifest = (snapshot / "MANIFEST").read_text(encoding="utf-8")
    assert f"admin_state_file={admin_state}" in manifest
    assert "admin_state_backed_up=true" in manifest
    checksums = (snapshot / "SHA256SUMS").read_text(encoding="utf-8")
    assert "./admin_state.json" in checksums
    assert json.loads(status_file.read_text(encoding="utf-8"))["state"] == "success"


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


def test_v2_preflight_uses_the_project_python_runtime() -> None:
    source = (ROOT / "scripts" / "deploy_v2.sh").read_text(encoding="utf-8")

    assert '"$BACKEND_DIR/.venv/bin/python" -m compileall' in source
    assert "run_local python3 -m compileall" not in source


def test_activation_scripts_lock_and_v2_runtime_rolls_back_atomically() -> None:
    v1 = (ROOT / "scripts" / "remote" / "activate_v1.sh").read_text(encoding="utf-8")
    v2 = (ROOT / "scripts" / "remote" / "activate_v2.sh").read_text(encoding="utf-8")
    public_gpu = (ROOT / "scripts" / "remote" / "activate_public_gpu_deploy.sh").read_text(
        encoding="utf-8"
    )

    assert "flock -n 9" in v1
    assert "flock -n 9" in v2
    for body in (v1, v2, public_gpu):
        assert "/var/lock/realguard-public-release.lock" in body
        assert "flock -w 900 8" in body
    assert "set +e" in v1.split("rollback()", 1)[1]
    assert "set +e" in v2.split("rollback()", 1)[1]
    assert 'python3 -m venv "$release_root/.venv"' in v2
    assert '/opt/jianzhen-v2/.venv.next' in v2
    assert 'runtime_switched=1' in v2


def test_v1_and_v2_uploads_are_isolated_before_serialized_promotion() -> None:
    cases = (
        (
            ROOT / "scripts" / "deploy_v1.sh",
            "mktemp -d '/tmp/realguard-v1-${COMMIT_SHA}.XXXXXXXXXX'",
            "/opt/realguard-data/deploy-locks/v1-promotion.lock",
            "bash /tmp/realguard-activate-v1.sh",
        ),
        (
            ROOT / "scripts" / "deploy_v2.sh",
            "mktemp -d '/tmp/jianzhen-v2-${COMMIT_SHA}.XXXXXXXXXX'",
            "/opt/realguard-data/deploy-locks/v2-promotion.lock",
            "bash /tmp/jianzhen-activate-v2.sh",
        ),
    )

    for path, unique_stage, promotion_lock, activation in cases:
        body = path.read_text(encoding="utf-8")
        assert unique_stage in body
        assert '"$REMOTE:$REMOTE_STAGE/' in body
        assert '"$REMOTE:/tmp/' not in body
        assert "REMOTE_STAGE_ACTIVE=1" in body
        assert 'run_remote "rm -rf -- \'$REMOTE_STAGE\'"' in body
        assert "trap cleanup_stage EXIT" in body
        assert promotion_lock in body
        assert "deploy_user=\\$(id -un)" in body
        assert "deploy_group=\\$(id -gn)" in body
        assert "-o ubuntu -g ubuntu" not in body
        assert "promoted=0" in body
        assert 'if [[ "\\$promoted" == "1" ]]' in body
        assert body.index("flock -w 7200 8") < body.index("promoted=1") < body.index(
            'cp -f --remove-destination -- "\\$stage"/* /tmp/'
        ) < body.index(activation)


def test_gpu_uploads_and_activation_use_a_unique_remote_stage() -> None:
    body = (ROOT / "scripts" / "deploy_detection_service.sh").read_text(
        encoding="utf-8"
    )

    assert "mktemp -d '/tmp/realguard-detection-${commit_sha}.XXXXXXXXXX'" in body
    assert '"$GPU_USER@$GPU_HOST:$gpu_remote_stage/realguard-detection-release.tgz"' in body
    assert '"$GPU_USER@$GPU_HOST:$gpu_remote_stage/realguard-detection.DEPLOYED_COMMIT"' in body
    assert '"$GPU_USER@$GPU_HOST:/tmp/realguard-detection-release.tgz"' not in body
    assert '"$GPU_USER@$GPU_HOST:/tmp/realguard-detection.DEPLOYED_COMMIT"' not in body
    assert "GPU_RELEASE_ARCHIVE=" in body
    assert "GPU_RELEASE_MARKER=" in body
    assert "trap cleanup_stage EXIT" in body
    assert "gpu_activation_started=1" in body


def test_converge_final_status_pins_every_target_commit() -> None:
    body = (ROOT / "scripts" / "deploy_converge.sh").read_text(encoding="utf-8")
    final_block = body.split("final_status_check()", 1)[1]

    for target in ("v1", "v2", "gpu"):
        assert f's/^{target}_expected=//p' in body
        assert f'final_status_check "{target.upper()}" "${target}_expected" {target}' in final_block
    assert 'STRICT=1 EXPECT_COMMIT="$expected"' in final_block
    assert 'bash "$STATUS_SCRIPT" "$target"' in final_block
    assert 'bash "$STATUS_SCRIPT" "$TARGET"' not in final_block


def test_core_units_have_conservative_resource_and_runtime_boundaries() -> None:
    expected = {
        "realguard-backend.service": {
            "CPUQuota": "150%",
            "MemoryHigh": "512M",
            "MemoryMax": "640M",
            "MemorySwapMax": "256M",
            "TasksMax": "256",
            "OOMPolicy": "stop",
        },
        "jianzhen-v2-backend.service": {
            "CPUQuota": "150%",
            "MemoryHigh": "768M",
            "MemoryMax": "1G",
            "MemorySwapMax": "256M",
            "TasksMax": "256",
            "OOMPolicy": "stop",
        },
        "realguard-backup.service": {
            "TimeoutStartSec": "3h",
            "TimeoutStopSec": "5m",
            "RuntimeMaxSec": "3h",
            "CPUQuota": "100%",
            "MemoryHigh": "384M",
            "MemoryMax": "512M",
            "MemorySwapMax": "128M",
            "TasksMax": "128",
            "OOMPolicy": "stop",
        },
    }

    for service_name, required in expected.items():
        body = (ROOT / "deploy" / "systemd" / service_name).read_text(
            encoding="utf-8"
        )
        settings = dict(
            line.split("=", 1)
            for line in body.splitlines()
            if "=" in line and not line.startswith("Environment=")
        )
        for key, value in required.items():
            assert settings.get(key) == value, f"{service_name} missing {key}={value}"


def test_gpu_deploy_and_status_do_not_use_tofu_host_keys() -> None:
    deploy = (ROOT / "scripts" / "deploy_detection_service.sh").read_text(encoding="utf-8")
    status = (ROOT / "scripts" / "check_deploy_status.sh").read_text(encoding="utf-8")

    assert "StrictHostKeyChecking=accept-new" not in deploy
    assert "StrictHostKeyChecking=accept-new" not in status
    assert "StrictHostKeyChecking=yes" in deploy
    assert "StrictHostKeyChecking=yes" in status


def test_restore_drill_never_pauses_production_mysql_event_scheduler() -> None:
    script = (ROOT / "scripts" / "remote" / "verify_restore_realguard.sh").read_text(
        encoding="utf-8"
    )

    assert "SET GLOBAL event_scheduler" not in script
    assert "@@GLOBAL.event_scheduler" not in script
    assert "skipped a scheduled event definition" in script
    assert "creates_event" in script


def test_backup_rejects_an_oversized_new_snapshot_before_repointing_latest() -> None:
    script = (ROOT / "scripts" / "remote" / "backup_realguard.sh").read_text(
        encoding="utf-8"
    )

    viability = script.index('snapshot_is_independently_viable "$final"')
    repoint = script.index('ln -sfn "$timestamp" "$BACKUP_ROOT/latest"')
    assert viability < repoint
    assert 'rm -rf -- "$final"' in script[viability:repoint]

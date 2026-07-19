from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def test_gpu_release_preflights_before_stopping_services():
    activate = (ROOT / "scripts/remote/activate_detection_service.sh").read_text(encoding="utf-8")

    dependency_check = activate.index("verify_pinned_requirements \\")
    switch_started = activate.index("switched=1")

    assert dependency_check < switch_started
    assert "model_decision_policy.py" in activate
    assert '"$release_root/model/runtime.lock"' in activate
    assert '"$release_root/watermark/runtime.lock"' in activate
    assert '"$release_root/yolo/runtime.lock"' in activate
    assert "sudo install -m 644" in activate
    assert 'sudo systemctl enable "$managed_service"' in activate
    assert 'sudo systemctl is-enabled --quiet "$managed_service"' in activate
    assert "duplicate runtime requirement" in activate
    assert '"$rollback_script_target" "$release_root" watchdog' in activate


def test_prediction_runtime_exposes_model_identity_required_by_activation_probe():
    inference = (ROOT / "services/realguard-detection/inference_onnx.py").read_text(
        encoding="utf-8"
    )

    assert '"modelRevision": _model_state.get("modelRevision"' in inference
    assert '"modelSha256": _model_state.get("modelSha256")' in inference
    assert '"deploymentCommit": _deployment_commit()' in inference


def test_gpu_deploy_recovers_public_worker_after_ambiguous_ssh_stop():
    deploy = (ROOT / "scripts/deploy_detection_service.sh").read_text(encoding="utf-8")

    attempt = deploy.index("web_worker_drain_attempted=1")
    activation = deploy.index("sudo bash '$PUBLIC_ACTIVATE_TMP'")

    assert attempt < activation
    assert "realguard-developer-worker.service.active" in deploy
    assert "model_decision_policy.py" in deploy
    assert "runtime.lock" in deploy
    assert "realguard-web-tunnel.service" in deploy
    assert "finalize_gpu_release" not in deploy
    assert "finalize_public_release" not in deploy
    assert "commit_visible=0" in deploy
    assert "public_response_key_hash" in deploy
    assert "gpu_response_key_hash" in deploy
    assert 'test "$public_response_key_hash" = "$gpu_response_key_hash"' in deploy


def test_convergence_status_is_parsed_without_eval():
    converge = (ROOT / "scripts/deploy_converge.sh").read_text(encoding="utf-8")
    status = (ROOT / "scripts/check_deploy_status.sh").read_text(encoding="utf-8")

    assert 'eval "$status_output"' not in converge
    assert "v1_repo_state=" in converge
    assert 'if [[ "$FORMAT" == "env" ]]' in status
    assert "gpu_yolo_runtime" in status


def test_remote_watchdogs_restore_both_hosts_after_deployer_loss():
    deploy = (ROOT / "scripts/deploy_detection_service.sh").read_text(encoding="utf-8")
    activate = (ROOT / "scripts/remote/activate_detection_service.sh").read_text(
        encoding="utf-8"
    )
    public_rollback = (
        ROOT / "scripts/remote/rollback_public_gpu_deploy.sh"
    ).read_text(encoding="utf-8")
    gpu_rollback = (
        ROOT / "scripts/remote/rollback_detection_service.sh"
    ).read_text(encoding="utf-8")

    public_activate = (
        ROOT / "scripts/remote/activate_public_gpu_deploy.sh"
    ).read_text(encoding="utf-8")

    assert "rollback_gpu_release" in deploy
    assert "rollback_public_gpu_deploy.sh" in deploy
    assert "activate_public_gpu_deploy.sh" in deploy
    assert activate.index("systemd-run --quiet") < activate.index("switched=1")
    assert activate.index('mv -Tf "$release_base/current.next"') < activate.index(
        'sudo systemctl stop "$service_name"'
    )
    assert public_activate.index("systemd-run --quiet") < public_activate.index(
        'install -D -o root -g root -m 0644 "$config_tmp"'
    )
    assert public_activate.index('install -D -o root -g root -m 0644 "$backup_root/marker.next"') < public_activate.index(
        'install -D -o root -g root -m 0644 "$config_tmp"'
    )
    assert "remote.conf.previous" in public_rollback
    assert "marker.previous" in public_rollback
    assert 'current_commit" != "$expected_commit' in public_rollback
    assert 'current_release" != "$release_root' in gpu_rollback
    assert "deploymentCommit" in public_rollback
    assert "deploymentCommit" in gpu_rollback
    assert "restore_unit_state" in public_rollback
    assert "restore_unit_state" in gpu_rollback
    assert "previous_current" in gpu_rollback
    assert "realguard-detection.service" in gpu_rollback
    assert "/var/lock/realguard-public-gpu-deploy.lock" in public_activate
    assert "/var/lock/realguard-public-gpu-deploy.lock" in public_rollback
    assert '"$release_base/.deploy.lock"' in activate
    assert '"$release_base/.deploy.lock"' in gpu_rollback
    assert "cleanup_remote_rollback_artifacts" not in deploy


def test_runtime_locks_are_complete_pinned_and_have_no_duplicate_packages():
    lock_paths = (
        ROOT / "services/realguard-detection/runtime.lock",
        ROOT / "services/watermark-precheck/runtime.lock",
        ROOT / "services/yolo-watermark/runtime.lock",
    )

    for lock_path in lock_paths:
        requirements = [
            line.strip()
            for line in lock_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert requirements
        canonical_names = []
        for requirement in requirements:
            assert requirement.count("==") == 1
            package, version = requirement.split("==", 1)
            assert package and version
            canonical_names.append(re.sub(r"[-_.]+", "-", package).lower())
        assert len(canonical_names) == len(set(canonical_names))


def test_gpu_status_manifest_matches_deployment_transaction_paths():
    deploy = (ROOT / "scripts/deploy_detection_service.sh").read_text(encoding="utf-8")
    status = (ROOT / "scripts/check_deploy_status.sh").read_text(encoding="utf-8")
    transaction_paths = (
        "scripts/remote/activate_detection_service.sh",
        "scripts/remote/activate_public_gpu_deploy.sh",
        "scripts/remote/rollback_detection_service.sh",
        "scripts/remote/rollback_public_gpu_deploy.sh",
    )

    for path in transaction_paths:
        assert path in deploy
        assert path in status
    assert "public_runtime" in status
    assert 'd.get("deploymentCommit") == sys.argv[3]' in status
    assert 'd.get("responseIntegrityReady") is True' in status


def test_gpu_response_integrity_uses_a_secret_not_sent_as_bearer_token():
    remote = (ROOT / "services/realguard-detection/remote_inference.py").read_text(
        encoding="utf-8"
    )
    client = (
        ROOT
        / "realguard-server-main/RealGuard/imagedetection/Agent/tools/AIGC_Detection/inference_onnx.py"
    ).read_text(encoding="utf-8")

    assert "REALGUARD_MODEL_RESPONSE_HMAC_KEY" in remote
    assert "REALGUARD_MODEL_RESPONSE_HMAC_KEY" in client
    assert '_configured_token().encode("utf-8")' not in remote
    assert 'headers["X-RealGuard-Internal-Token"] = REMOTE_RESPONSE_HMAC_KEY' not in client
    assert 'headers = {"X-RealGuard-Request-Nonce": nonce}' in client

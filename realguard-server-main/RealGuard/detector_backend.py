import importlib
import hmac
import io
import json
import os
import sys
import threading
import time
import uuid
import warnings
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request
from PIL import Image, UnidentifiedImageError
import requests
from werkzeug.utils import secure_filename

from model_decision_contract import validate_inference_audit, validate_model_decision

from imagedetection.views.utils import (
    create_folder,
    excute_detection_sql,
    excute_detection_sql_lastid,
    get_file_size_str,
    get_image_info,
    normalize_account_uuid,
    safe_truncate,
)


ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "bmp", "gif"}
MAX_IMAGE_UPLOAD_BYTES = max(
    1024,
    int(os.environ.get("REALGUARD_MAX_IMAGE_UPLOAD_BYTES", str(25 * 1024 * 1024))),
)
MAX_IMAGE_SOURCE_PIXELS = max(
    1,
    int(os.environ.get("REALGUARD_MAX_IMAGE_SOURCE_PIXELS", "24000000")),
)
DETECTOR_INTERNAL_TOKEN = os.environ.get("REALGUARD_DETECTOR_INTERNAL_TOKEN", "").strip()
PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = PROJECT_ROOT / "imagedetection" / "static"
V1_ARTIFACT_DIR = PROJECT_ROOT / "imagedetection" / "Agent" / "tools" / "AIGC_Detection"
V1_ONNX_PATH = V1_ARTIFACT_DIR / "model_deploy.onnx"
V1_EXTERNAL_DATA_PATH = V1_ARTIFACT_DIR / "model_deploy.onnx.data"
V1_EXTERNAL_MIN_BYTES = int(os.environ.get("REALGUARD_V1_EXTERNAL_MIN_BYTES", str(100 * 1024 * 1024)))
REMOTE_INFERENCE_URL = os.environ.get("REALGUARD_REMOTE_INFERENCE_URL", "").strip()
REMOTE_INFERENCE_TOKEN = os.environ.get("REALGUARD_MODEL_INTERNAL_TOKEN", "").strip()
REMOTE_HEALTH_CACHE_SECONDS = float(os.environ.get("REALGUARD_REMOTE_HEALTH_CACHE_SECONDS", "3"))
REQUIRED_MODULES = ("onnxruntime", "numpy", "PIL", "openai", "requests")
_REMOTE_HEALTH_CACHE = {"expiresAt": 0.0, "value": None}
_REMOTE_HEALTH_LOCK = threading.Lock()


def _load_env_file(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _human_size(size):
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{value:.1f}GB"


def _describe_path(path):
    path = Path(path)
    if not path.exists():
        return {"path": str(path), "exists": False, "sizeBytes": 0, "size": "missing"}
    size = path.stat().st_size
    return {"path": str(path), "exists": True, "sizeBytes": size, "size": _human_size(size)}


def _dependency_status():
    missing = []
    for module_name in REQUIRED_MODULES:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            missing.append(f"{module_name}: {exc}")
    return {
        "ready": not missing,
        "missing": missing,
    }


def _artifact_status():
    artifact = _describe_path(V1_ONNX_PATH)
    external = _describe_path(V1_EXTERNAL_DATA_PATH)
    warnings = []

    if artifact["exists"] is False:
        warnings.append("missing ONNX graph file: model_deploy.onnx")
    if external["exists"] is False:
        warnings.append("missing external ONNX weight file: model_deploy.onnx.data")
    elif external["sizeBytes"] < V1_EXTERNAL_MIN_BYTES:
        warnings.append(
            "external ONNX weight file is too small: "
            f"{external['size']} < {_human_size(V1_EXTERNAL_MIN_BYTES)}"
        )

    return {
        "ready": not warnings,
        "warnings": warnings,
        "artifact": artifact,
        "externalData": external,
    }


def _remote_health_url():
    if not REMOTE_INFERENCE_URL:
        return ""
    if REMOTE_INFERENCE_URL.endswith("/predict"):
        return REMOTE_INFERENCE_URL[:-len("/predict")] + "/health"
    return REMOTE_INFERENCE_URL.rstrip("/") + "/health"


def _remote_inference_status():
    if not REMOTE_INFERENCE_URL:
        return {"configured": False, "ready": False, "error": "not configured"}

    now = time.monotonic()
    cached = _REMOTE_HEALTH_CACHE.get("value")
    if cached is not None and now < _REMOTE_HEALTH_CACHE.get("expiresAt", 0.0):
        return dict(cached)

    with _REMOTE_HEALTH_LOCK:
        now = time.monotonic()
        cached = _REMOTE_HEALTH_CACHE.get("value")
        if cached is not None and now < _REMOTE_HEALTH_CACHE.get("expiresAt", 0.0):
            return dict(cached)

        headers = {}
        if REMOTE_INFERENCE_TOKEN:
            headers["X-RealGuard-Internal-Token"] = REMOTE_INFERENCE_TOKEN
        started = time.perf_counter()
        try:
            response = requests.get(
                _remote_health_url(),
                headers=headers,
                timeout=(2, 4),
            )
            payload = response.json()
            data = payload.get("data") or {}
            provider = data.get("activeProvider")
            ready = (
                response.status_code == 200
                and payload.get("code") == 200
                and provider == "CUDAExecutionProvider"
            )
            result = {
                "configured": True,
                "ready": ready,
                "activeProvider": provider,
                "cudaDeviceId": data.get("cudaDeviceId"),
                "modelRevision": data.get("modelRevision"),
                "modelSha256": data.get("modelSha256"),
                "deploymentCommit": data.get("deploymentCommit"),
                "responseIntegrityReady": data.get("responseIntegrityReady") is True,
                "verdictReady": data.get("verdictReady") is True,
                "decisionMode": str(data.get("decisionMode") or "review_only"),
                "decisionGateReasons": [str(item) for item in data.get("decisionGateReasons") or []],
                "latencyMs": round((time.perf_counter() - started) * 1000.0, 2),
                "error": "" if ready else (payload.get("msg") or f"HTTP {response.status_code}"),
            }
        except Exception as exc:
            result = {
                "configured": True,
                "ready": False,
                "activeProvider": None,
                "cudaDeviceId": None,
                "modelRevision": None,
                "modelSha256": None,
                "deploymentCommit": None,
                "responseIntegrityReady": False,
                "verdictReady": False,
                "decisionMode": "unavailable",
                "decisionGateReasons": ["remote_model_health_unavailable"],
                "latencyMs": round((time.perf_counter() - started) * 1000.0, 2),
                "error": str(exc),
            }
        _REMOTE_HEALTH_CACHE["value"] = result
        _REMOTE_HEALTH_CACHE["expiresAt"] = time.monotonic() + max(0.0, REMOTE_HEALTH_CACHE_SECONDS)
        return dict(result)


def _capability_status():
    artifacts = _artifact_status()
    dependencies = _dependency_status()
    remote = _remote_inference_status()
    using_remote = bool(REMOTE_INFERENCE_URL)
    warnings = [] if using_remote else list(artifacts["warnings"])
    warnings.extend(f"missing runtime dependency: {item}" for item in dependencies["missing"])
    if using_remote and not remote["ready"]:
        warnings.append(f"remote CUDA inference unavailable: {remote.get('error') or 'unknown error'}")
    if using_remote and remote["ready"] and not remote.get("verdictReady"):
        warnings.append("remote model runtime is ready but automatic verdict calibration is not authorized")
    capability_ready = dependencies["ready"] and (
        remote["ready"] if using_remote else artifacts["ready"]
    )
    return {
        "serviceOk": True,
        "artifactReady": artifacts["ready"],
        "dependencyReady": dependencies["ready"],
        "capabilityReady": capability_ready,
        "verdictReady": bool(remote.get("verdictReady")) if using_remote else False,
        "decisionMode": str(remote.get("decisionMode") or "review_only") if using_remote else "review_only",
        "decisionGateReasons": list(remote.get("decisionGateReasons") or []) if using_remote else ["local_model_not_calibrated"],
        "inferenceMode": "remote-cuda" if using_remote else "local-onnx",
        "remoteInference": remote,
        "artifacts": {
            "artifact": artifacts["artifact"],
            "externalData": artifacts["externalData"],
        },
        "dependencies": dependencies,
        "warnings": warnings,
    }


def _ensure_capability_ready():
    status = _capability_status()
    if status["capabilityReady"]:
        return
    raise RuntimeError("主鉴伪模型未就绪：" + "；".join(status["warnings"]))


def _allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _to_float(value, default=0.0):
    if value is None:
        return float(default)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("%", "")
    if not text:
        return float(default)
    try:
        return float(text)
    except ValueError:
        return float(default)


def _confidence_level(score):
    if str(score or "") in ("高", "中", "低"):
        return str(score)
    score = max(0.0, min(1.0, _to_float(score, 0.5)))
    delta = abs(score - 0.5)
    if delta >= 0.35:
        return "高"
    if delta >= 0.18:
        return "中"
    return "低"


def _save_upload(image_bytes, folder, filename):
    upload_dir = STATIC_ROOT / "uploads" / folder / "image"
    create_folder(str(upload_dir))
    safe_name = secure_filename(filename) or f"{uuid.uuid4().hex}.png"
    stored_name = f"{uuid.uuid4().hex[:12]}-{safe_name}"
    file_path = upload_dir / stored_name
    file_path.write_bytes(image_bytes)
    file_path.chmod(0o600)
    return stored_name, str(file_path)


def _extract_metadata_field(metadata, *names):
    for name in names:
        for key, value in (metadata or {}).items():
            if str(key).lower().endswith(name.lower()) and value not in (None, ""):
                return str(value)
    return None


def _run_v1_detect(image_path):
    from imagedetection.Agent.main import detect

    return detect(image_path)


def _consume_remote_inference_evidence():
    module_names = (
        "tools.AIGC_Detection.inference_onnx",
        "imagedetection.Agent.tools.AIGC_Detection.inference_onnx",
    )
    for module_name in module_names:
        module = sys.modules.get(module_name)
        consume = getattr(module, "consume_remote_evidence", None) if module else None
        if callable(consume):
            evidence = consume()
            if evidence:
                return evidence
    return {}


def _apply_remote_model_decision_gate(payload, remote_evidence):
    decision = (
        (remote_evidence or {}).get("modelDecision")
        if isinstance(remote_evidence, dict) else None
    )
    model_run = (
        remote_evidence.get("modelRun")
        if isinstance(remote_evidence, dict)
        and isinstance(remote_evidence.get("modelRun"), dict)
        else None
    )
    contract_ready = bool(
        validate_model_decision(decision)
        and validate_inference_audit(model_run, decision)
    )
    if contract_ready:
        return payload
    raw_score = _to_float(
        (decision or {}).get("rawModelScore") if isinstance(decision, dict) else None,
        payload.get("detector_probability", payload.get("probability", 0.5)),
    )
    raw_score = max(0.0, min(1.0, raw_score))
    if isinstance(remote_evidence, dict):
        original_reasons = (
            list(decision.get("gateReasons") or [])
            if isinstance(decision, dict) and isinstance(decision.get("gateReasons"), list)
            else []
        )
        reason = (
            "model_decision_contract_invalid"
            if isinstance(decision, dict)
            else "model_decision_contract_missing"
        )
        decision = dict(decision or {})
        decision.update({
            "ready": False,
            "mode": "review_only",
            "rawModelScore": raw_score,
            "gateReasons": list(dict.fromkeys([*original_reasons, reason])),
        })
        remote_evidence["modelDecision"] = decision
    payload["detector_probability"] = 0.5
    payload["probability"] = 0.5
    payload["final_label"] = "需人工复核"
    payload["confidence"] = "低"
    payload["model_decision_ready"] = False
    payload["explanation"] = (
        "主鉴伪模型尚未通过独立数据集校准门禁；原始模型分数仅保存在受限审计记录中，"
        "不能解释为 AI 生成概率，也不会作为真假结论对外发布。"
        "本次结果须结合来源凭证、已确认的平台水印和人工复核。"
    )
    return payload


def _detection_user_id(phone="", openid=""):
    phone = str(phone or "").strip()
    openid = str(openid or "").strip()
    if phone:
        rows = excute_detection_sql("SELECT Userid FROM user WHERE phone = %s LIMIT 1", (phone,))
    elif openid and not openid.startswith("guest-"):
        rows = excute_detection_sql("SELECT Userid FROM user WHERE openid = %s LIMIT 1", (openid,))
    else:
        return None
    return (rows or [{}])[0].get("Userid")


def _valid_source_task_id(value):
    task_id = str(value or "").strip().lower()
    return (
        len(task_id) == 24
        and task_id.startswith("job_")
        and all(char in "0123456789abcdef" for char in task_id[4:])
    )


def _persist_result(
    payload,
    image_bytes,
    filename,
    openid,
    phone,
    account_uuid="",
    source_task_id="",
):
    folder = openid or phone or "guest"
    stored_name, file_path = _save_upload(image_bytes, folder, filename)
    img_format, resolution = get_image_info(file_path)
    file_size = get_file_size_str(file_path)
    probability = max(0.0, min(1.0, _to_float(payload.get("probability"), 0.5)))
    detector_probability = max(0.0, min(1.0, _to_float(payload.get("detector_probability"), probability)))
    fake_pct = round(probability * 100, 2)
    final_label = payload.get("final_label") or ("AI生成图像" if probability >= 0.5 else "真实图像")
    confidence = _confidence_level(payload.get("confidence") or probability)
    explanation = str(payload.get("explanation") or "").strip()
    visual_issues = payload.get("visual_issues") or []
    all_metadata = payload.get("all_metadata") or {}
    metadata_signals = payload.get("metadata_signals") or {}

    if visual_issues and "视觉可疑点" not in explanation:
        explanation = f"{explanation}\n视觉可疑点\n" + "\n".join(f"- {item}" for item in visual_issues)

    itemid = excute_detection_sql_lastid(
        """
        INSERT INTO data
            (createtime, filename, fake, detector_probability, openid, phone, aigc,
             file_size, img_format, resolution, clarity, explantation, Userid,
             owner_account_uuid, developer_task_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            stored_name,
            fake_pct,
            detector_probability,
            openid,
            phone,
            final_label,
            file_size,
            img_format,
            resolution,
            confidence,
            safe_truncate(explanation, 500),
            _detection_user_id(phone, openid),
            normalize_account_uuid(account_uuid) or None,
            source_task_id or None,
        ),
    )
    if not itemid:
        raise RuntimeError("检测结果写入失败")

    if all_metadata:
        excute_detection_sql_lastid(
            """
            INSERT INTO exif
                (data_itemid, createtime, filename, openid, phone, metadata_count,
                 has_ai_signal, has_real_signal, all_metadata, software, user_comment,
                 camera_make, camera_model, lens_model, lens_info, gps_position,
                 datetime_original, exposure_time, fnumber, iso, focal_length)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                itemid,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                stored_name,
                openid,
                phone,
                len(all_metadata),
                1 if metadata_signals.get("has_ai_signal") else 0,
                1 if metadata_signals.get("has_real_signal") else 0,
                json.dumps(all_metadata, ensure_ascii=False),
                _extract_metadata_field(all_metadata, "Software"),
                _extract_metadata_field(all_metadata, "UserComment", "User Comment"),
                _extract_metadata_field(all_metadata, "Make"),
                _extract_metadata_field(all_metadata, "Model"),
                _extract_metadata_field(all_metadata, "LensModel", "Lens Model"),
                _extract_metadata_field(all_metadata, "LensInfo", "Lens Info"),
                _extract_metadata_field(all_metadata, "GPSPosition", "GPS Position"),
                _extract_metadata_field(all_metadata, "DateTimeOriginal", "Date/Time Original"),
                _extract_metadata_field(all_metadata, "ExposureTime", "Exposure Time"),
                _extract_metadata_field(all_metadata, "FNumber", "F Number"),
                _extract_metadata_field(all_metadata, "ISO"),
                _extract_metadata_field(all_metadata, "FocalLength", "Focal Length"),
            ),
        )

    return {
        "data_itemid": itemid,
        "fake_percentage": fake_pct,
        "detector_probability": detector_probability,
        "final_label": final_label,
        "confidence": confidence,
        "image_url": f"{request.host_url.rstrip('/')}/static/uploads/{folder}/image/{stored_name}",
        "filename": stored_name,
        "file_size": file_size,
        "img_format": img_format,
        "resolution": resolution,
        "explanation": explanation,
        "visual_issues": visual_issues,
        "agent_reasoning": payload.get("agent_reasoning") or payload.get("raw_response") or "",
        "full_exif_info": all_metadata,
        "remote_evidence": payload.get("remote_evidence") or {},
        "meta": {
            "file_size": file_size,
            "img_format": img_format,
            "resolution": resolution,
            "model": "realguard-v2-onnx-cuda" if REMOTE_INFERENCE_URL else "realguard-v1-onnx-mil",
            "detector_probability": detector_probability,
        },
    }


def create_app():
    app = Flask(__name__, static_folder=str(STATIC_ROOT), static_url_path="/static")

    @app.before_request
    def protect_detector_ingress():
        if request.method != "POST" or request.path not in {"/image", "/video"}:
            return None
        if not DETECTOR_INTERNAL_TOKEN:
            return jsonify({"code": 503, "msg": "Detector internal token is not configured"}), 503
        provided = request.headers.get("X-RealGuard-Detector-Token", "").strip()
        if not provided or not hmac.compare_digest(provided, DETECTOR_INTERNAL_TOKEN):
            return jsonify({"code": 401, "msg": "Unauthorized"}), 401
        if request.content_length is None:
            return jsonify({"code": 411, "msg": "Content-Length is required"}), 411
        if request.path == "/image" and request.content_length > MAX_IMAGE_UPLOAD_BYTES + (1024 * 1024):
            return jsonify({"code": 413, "msg": "Image is too large"}), 413
        return None

    @app.get("/health")
    def health():
        capability = _capability_status()
        token_ready = len(DETECTOR_INTERNAL_TOKEN) >= 32
        status = "ok" if capability["capabilityReady"] and token_ready else "degraded"
        return jsonify({
            "status": status,
            "service": "realguard-v1-detector",
            "model": "realguard-v2-onnx-cuda" if REMOTE_INFERENCE_URL else "v1-onnx-mil",
            "tokenReady": token_ready,
            **capability,
        })

    @app.get("/ready")
    def ready():
        capability = _capability_status()
        token_ready = len(DETECTOR_INTERNAL_TOKEN) >= 32
        ready_now = capability["capabilityReady"] and token_ready
        http_status = 200 if ready_now else 503
        return jsonify({
            "status": "ok" if ready_now else "error",
            "service": "realguard-v1-detector",
            "model": "realguard-v2-onnx-cuda" if REMOTE_INFERENCE_URL else "v1-onnx-mil",
            "tokenReady": token_ready,
            **capability,
        }), http_status

    @app.get("/internal/ready")
    def internal_ready():
        if not DETECTOR_INTERNAL_TOKEN:
            return jsonify({"status": "error", "tokenReady": False}), 503
        provided = request.headers.get("X-RealGuard-Detector-Token", "").strip()
        if not provided or not hmac.compare_digest(provided, DETECTOR_INTERNAL_TOKEN):
            return jsonify({"status": "error", "tokenReady": False}), 401
        capability = _capability_status()
        ready_now = capability["capabilityReady"]
        return jsonify({
            "status": "ok" if ready_now else "error",
            "tokenReady": True,
            **capability,
        }), 200 if ready_now else 503

    @app.post("/image")
    def image():
        file = request.files.get("image_file") or request.files.get("image") or request.files.get("file")
        if not file or not file.filename:
            return jsonify({"code": 400, "msg": "请上传图片文件"}), 400
        if not _allowed_file(file.filename):
            return jsonify({"code": 400, "msg": "不支持的文件格式"}), 400

        safe_name = secure_filename(file.filename) or file.filename
        image_bytes = file.stream.read(MAX_IMAGE_UPLOAD_BYTES + 1)
        if not image_bytes:
            return jsonify({"code": 400, "msg": "请上传非空图片文件"}), 400
        if len(image_bytes) > MAX_IMAGE_UPLOAD_BYTES:
            return jsonify({"code": 413, "msg": "Image is too large"}), 413
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(image_bytes)) as image:
                    width, height = image.size
                    if width <= 0 or height <= 0:
                        raise ValueError("invalid image dimensions")
                    if bool(getattr(image, "is_animated", False)) and int(getattr(image, "n_frames", 1)) > 1:
                        return jsonify({"code": 415, "msg": "Animated images are not supported"}), 415
                    if width * height > MAX_IMAGE_SOURCE_PIXELS:
                        return jsonify({"code": 413, "msg": "Image pixel dimensions are too large"}), 413
                    image.verify()
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            return jsonify({"code": 413, "msg": "Image pixel dimensions are too large"}), 413
        except (UnidentifiedImageError, OSError, ValueError):
            return jsonify({"code": 400, "msg": "Invalid image"}), 400

        openid = str(request.form.get("openid") or "").strip()[:64]
        phone = str(request.form.get("phone") or "").strip()[:20]
        raw_account_uuid = str(request.form.get("account_uuid") or "").strip()
        account_uuid = normalize_account_uuid(raw_account_uuid)
        if raw_account_uuid and not account_uuid:
            return jsonify({"code": 400, "msg": "账号标识格式无效"}), 400
        raw_source_task_id = str(request.form.get("source_task_id") or "").strip().lower()
        if raw_source_task_id and not _valid_source_task_id(raw_source_task_id):
            return jsonify({"code": 400, "msg": "任务标识格式无效"}), 400
        temp_path = None
        try:
            _ensure_capability_ready()
            _, temp_path = _save_upload(image_bytes, openid or phone or "guest", safe_name)
            payload = _run_v1_detect(temp_path)
            remote_evidence = _consume_remote_inference_evidence()
            _apply_remote_model_decision_gate(payload, remote_evidence)
            if remote_evidence:
                payload["remote_evidence"] = remote_evidence
            if request.form.get("internal_probe") == "1" and openid == "deployment-probe":
                return jsonify({
                    "code": 200,
                    "msg": "success",
                    "data": {
                        "probe": True,
                        "final_label": payload.get("final_label"),
                        "detector_probability": payload.get("detector_probability"),
                        "remote_evidence": remote_evidence,
                    },
                })
            data = _persist_result(
                payload,
                image_bytes,
                safe_name,
                openid,
                phone,
                account_uuid,
                raw_source_task_id,
            )
            return jsonify({"code": 200, "msg": "success", "data": data})
        except RuntimeError as exc:
            status_code = int(getattr(exc, "status_code", 503) or 503)
            if status_code not in {413, 415, 429, 503}:
                status_code = 503
            response = jsonify({
                "code": status_code,
                "errorCode": getattr(exc, "error_code", "detector_unavailable"),
                "msg": (
                    "GPU 推理队列已满，请稍后重试"
                    if status_code == 429
                    else "图像鉴伪服务暂不可用，请稍后重试"
                ),
            })
            retry_after = str(getattr(exc, "retry_after", "") or "").strip()
            if status_code == 429:
                response.headers["Retry-After"] = retry_after or "5"
            return response, status_code
        except Exception as exc:
            app.logger.exception("V1 native detection failed")
            return jsonify({"code": 500, "msg": "图像鉴伪服务处理失败，请稍后重试"}), 500
        finally:
            if temp_path:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except Exception:
                    pass

    @app.post("/video")
    def video():
        return jsonify({"code": 501, "msg": "V1 视频检测后端暂未启用"}), 501

    return app


_load_env_file()
app = create_app()


if __name__ == "__main__":
    host = os.environ.get("REALGUARD_DETECTOR_HOST", "127.0.0.1")
    port = int(os.environ.get("REALGUARD_DETECTOR_PORT", "15000"))
    app.run(host=host, port=port)

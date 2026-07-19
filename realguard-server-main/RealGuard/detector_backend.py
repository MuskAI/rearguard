import importlib
import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request
import requests
from werkzeug.utils import secure_filename

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
                "latencyMs": round((time.perf_counter() - started) * 1000.0, 2),
                "error": "" if ready else (payload.get("msg") or f"HTTP {response.status_code}"),
            }
        except Exception as exc:
            result = {
                "configured": True,
                "ready": False,
                "activeProvider": None,
                "cudaDeviceId": None,
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
    capability_ready = dependencies["ready"] and (
        remote["ready"] if using_remote else artifacts["ready"]
    )
    return {
        "serviceOk": True,
        "artifactReady": artifacts["ready"],
        "dependencyReady": dependencies["ready"],
        "capabilityReady": capability_ready,
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


def _persist_result(payload, image_bytes, filename, openid, phone, account_uuid=""):
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
             file_size, img_format, resolution, clarity, explantation, Userid, owner_account_uuid)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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

    @app.get("/health")
    def health():
        capability = _capability_status()
        status = "ok" if capability["capabilityReady"] else "degraded"
        return jsonify({
            "status": status,
            "service": "realguard-v1-detector",
            "model": "realguard-v2-onnx-cuda" if REMOTE_INFERENCE_URL else "v1-onnx-mil",
            **capability,
        })

    @app.get("/ready")
    def ready():
        capability = _capability_status()
        http_status = 200 if capability["capabilityReady"] else 503
        return jsonify({
            "status": "ok" if capability["capabilityReady"] else "error",
            "service": "realguard-v1-detector",
            "model": "realguard-v2-onnx-cuda" if REMOTE_INFERENCE_URL else "v1-onnx-mil",
            **capability,
        }), http_status

    @app.post("/image")
    def image():
        file = request.files.get("image_file") or request.files.get("image") or request.files.get("file")
        if not file or not file.filename:
            return jsonify({"code": 400, "msg": "请上传图片文件"}), 400
        if not _allowed_file(file.filename):
            return jsonify({"code": 400, "msg": "不支持的文件格式"}), 400

        safe_name = secure_filename(file.filename) or file.filename
        image_bytes = file.read()
        if not image_bytes:
            return jsonify({"code": 400, "msg": "请上传非空图片文件"}), 400

        openid = str(request.form.get("openid") or "").strip()[:64]
        phone = str(request.form.get("phone") or "").strip()[:20]
        raw_account_uuid = str(request.form.get("account_uuid") or "").strip()
        account_uuid = normalize_account_uuid(raw_account_uuid)
        if raw_account_uuid and not account_uuid:
            return jsonify({"code": 400, "msg": "账号标识格式无效"}), 400
        temp_path = None
        try:
            _ensure_capability_ready()
            _, temp_path = _save_upload(image_bytes, openid or phone or "guest", safe_name)
            payload = _run_v1_detect(temp_path)
            remote_evidence = _consume_remote_inference_evidence()
            if remote_evidence:
                payload["remote_evidence"] = remote_evidence
            data = _persist_result(payload, image_bytes, safe_name, openid, phone, account_uuid)
            return jsonify({"code": 200, "msg": "success", "data": data})
        except RuntimeError as exc:
            return jsonify({"code": 503, "msg": str(exc)}), 503
        except Exception as exc:
            return jsonify({"code": 500, "msg": f"V1 原生检测失败: {exc}"}), 500
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

import json
import os
import time
import uuid
from pathlib import Path


CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parents[1]
STATIC_ROOT = PROJECT_ROOT / "imagedetection" / "static"
DEFAULT_ENDPOINT = "green-cip.cn-beijing.aliyuncs.com"

SERVICES = {
    "aigcDetector_pro": {
        "name": "AIGC 鉴伪专业版",
        "description": "AI 生成图片、合成图及人脸合成风险检测。",
    },
    "aigcDetectorFull": {
        "name": "AIGC 鉴伪全量版",
        "description": "补充 AIGC 隐式标识/元数据检测，适合冲突样本复核。",
        "infoType": "aigcData",
    },
    "aigcDetector_ultra": {
        "name": "AIGC 鉴伪旗舰版",
        "description": "增强局部 AI 编辑和复杂伪造检测，适合高风险升级复核。",
    },
    "psDetector": {
        "name": "PS 篡改检测",
        "description": "检测图片是否存在 PS 修改痕迹。",
    },
    "recapDetector": {
        "name": "翻拍检测",
        "description": "检测屏幕翻拍、二次拍摄等风险。",
    },
}


def is_aliyun_model(model):
    endpoint = str((model or {}).get("endpoint") or "")
    runtime = str((model or {}).get("runtime") or "")
    return endpoint.startswith("internal://aliyun/") or runtime == "aliyun-green"


def service_from_model(model):
    endpoint = str((model or {}).get("endpoint") or "")
    if endpoint.startswith("internal://aliyun/"):
        return endpoint.rsplit("/", 1)[-1]
    return str((model or {}).get("service") or "").strip()


def configured():
    return bool(_access_key_id() and _access_key_secret())


def public_base_url():
    return (
        os.environ.get("REALGUARD_PUBLIC_BASE_URL")
        or os.environ.get("REALGUARD_EXTERNAL_BASE_URL")
        or ""
    ).strip().rstrip("/")


def health(model):
    service = service_from_model(model)
    warnings = []
    if service not in SERVICES:
        warnings.append(f"unsupported aliyun service: {service or '-'}")
    if not _access_key_id():
        warnings.append("missing ALIBABA_CLOUD_ACCESS_KEY_ID")
    if not _access_key_secret():
        warnings.append("missing ALIBABA_CLOUD_ACCESS_KEY_SECRET")
    if not public_base_url():
        warnings.append("missing REALGUARD_PUBLIC_BASE_URL for imageUrl upload flow")
    sdk_ready, sdk_error = _sdk_ready()
    if not sdk_ready:
        warnings.append(sdk_error)
    ok = not warnings
    return {
        "ok": ok,
        "serviceOk": ok,
        "artifactReady": True,
        "dependencyReady": sdk_ready,
        "capabilityReady": ok,
        "httpStatus": None,
        "latencyMs": None,
        "message": "ok" if ok else "；".join(warnings),
        "warnings": warnings,
        "checkedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "provider": "aliyun",
        "service": service,
    }


def detect_image_bytes(service, image_bytes, filename="realguard-probe.png"):
    if service not in SERVICES:
        raise ValueError(f"unsupported aliyun service: {service}")
    base_url = public_base_url()
    if not base_url:
        raise RuntimeError("REALGUARD_PUBLIC_BASE_URL is required for Aliyun imageUrl detection")

    probe_dir = STATIC_ROOT / "uploads" / "aliyun-probes"
    probe_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(filename or "image.png").suffix.lower() or ".png"
    name = f"{uuid.uuid4().hex}{ext}"
    path = probe_dir / name
    path.write_bytes(image_bytes)
    image_url = f"{base_url}/static/uploads/aliyun-probes/{name}"
    try:
        return detect_image_url(service, image_url)
    finally:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


def detect_image_url(service, image_url, data_id=None):
    if service not in SERVICES:
        raise ValueError(f"unsupported aliyun service: {service}")
    start = time.time()
    client, models, util_models = _client()
    params = {
        "imageUrl": image_url,
        "dataId": data_id or uuid.uuid4().hex,
    }
    info_type = SERVICES[service].get("infoType")
    if info_type:
        params["infoType"] = info_type
    request = models.ImageModerationRequest(
        service=service,
        service_parameters=json.dumps(params, ensure_ascii=False),
    )
    runtime = util_models.RuntimeOptions()
    response = client.image_moderation_with_options(request, runtime)
    raw = _to_plain(response)
    return {
        "ok": True,
        "provider": "aliyun",
        "service": service,
        "latencyMs": int((time.time() - start) * 1000),
        "normalized": normalize(raw, service),
        "raw": raw,
    }


def normalize(raw, service):
    labels = _find_values(raw, {"label", "Label"})
    descriptions = _find_values(raw, {"description", "Description"})
    confidences = _find_values(raw, {"confidence", "Confidence", "score", "Score"})
    risk_levels = _find_values(raw, {"riskLevel", "RiskLevel", "risk_level"})
    confidence = _first_number(confidences)
    label_text = " ".join(str(item) for item in labels + descriptions + risk_levels)
    risky = _looks_risky(label_text, service)
    if confidence is None:
        confidence = 0.8 if risky else 0.55
    if confidence > 1:
        confidence = confidence / 100.0
    confidence = max(0.0, min(1.0, confidence))
    risk_score = confidence if risky else max(0.0, min(1.0, 1.0 - confidence))
    return {
        "finalLabel": _final_label(service, risky),
        "riskScore": round(risk_score, 4),
        "confidence": "高" if abs(risk_score - 0.5) >= 0.35 else ("中" if abs(risk_score - 0.5) >= 0.18 else "低"),
        "labels": labels[:8],
        "descriptions": descriptions[:8],
        "riskLevels": risk_levels[:8],
    }


def _final_label(service, risky):
    if service == "psDetector":
        return "疑似PS篡改" if risky else "未发现明显PS篡改"
    if service == "recapDetector":
        return "疑似翻拍" if risky else "未发现明显翻拍"
    return "疑似AI生成" if risky else "未发现明显AI生成"


def _looks_risky(text, service):
    value = str(text or "").lower()
    safe_terms = ("normal", "pass", "non", "none", "无风险", "正常", "通过", "未发现")
    risky_terms = ("aigc", "ai", "generated", "synthesis", "synthetic", "fake", "risk", "疑似", "伪造", "合成")
    if service == "psDetector":
        risky_terms = risky_terms + ("ps", "tamper", "篡改", "修改")
    if service == "recapDetector":
        risky_terms = risky_terms + ("recap", "recapture", "翻拍", "屏幕")
    if any(term in value for term in safe_terms) and not any(term in value for term in risky_terms):
        return False
    return any(term in value for term in risky_terms)


def _find_values(value, names):
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in names and item not in (None, ""):
                found.append(item)
            found.extend(_find_values(item, names))
    elif isinstance(value, list):
        for item in value:
            found.extend(_find_values(item, names))
    return found


def _first_number(values):
    for value in values:
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _access_key_id():
    return (
        os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID")
        or os.environ.get("ALIYUN_GREEN_ACCESS_KEY_ID")
        or ""
    ).strip()


def _access_key_secret():
    return (
        os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
        or os.environ.get("ALIYUN_GREEN_ACCESS_KEY_SECRET")
        or ""
    ).strip()


def _sdk_ready():
    try:
        import alibabacloud_green20220302  # noqa: F401
        import alibabacloud_tea_openapi  # noqa: F401
        import alibabacloud_tea_util  # noqa: F401
        return True, ""
    except Exception as exc:
        return False, f"missing aliyun green sdk dependency: {exc}"


def _client():
    from alibabacloud_green20220302.client import Client
    from alibabacloud_green20220302 import models
    from alibabacloud_tea_openapi import models as open_api_models
    from alibabacloud_tea_util import models as util_models

    config = open_api_models.Config(
        access_key_id=_access_key_id(),
        access_key_secret=_access_key_secret(),
    )
    config.endpoint = os.environ.get("REALGUARD_ALIYUN_GREEN_ENDPOINT", DEFAULT_ENDPOINT)
    return Client(config), models, util_models


def _to_plain(value):
    if hasattr(value, "to_map"):
        return value.to_map()
    if isinstance(value, dict):
        return {key: _to_plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    if hasattr(value, "__dict__"):
        return {
            key: _to_plain(item)
            for key, item in value.__dict__.items()
            if not key.startswith("_")
        }
    return value

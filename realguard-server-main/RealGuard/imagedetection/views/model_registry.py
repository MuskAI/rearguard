import fcntl
import json
import os
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

import requests

from imagedetection.views import aliyun_green


CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parents[1]
DEFAULT_REGISTRY_PATH = PROJECT_ROOT / "model_registry.json"
REGISTRY_PATH = Path(os.environ.get("REALGUARD_MODEL_REGISTRY_PATH", str(DEFAULT_REGISTRY_PATH)))
MODEL_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{1,63}$")
DEFAULT_SWARM_EXPERTS = [
    {"id": "primary", "name": "主路由鉴伪专家", "role": "主检测", "provider": "internal", "enabled": True, "weight": 0.34},
    {"id": "metadata", "name": "元数据取证专家", "role": "元数据", "provider": "local", "enabled": True, "weight": 0.08},
    {"id": "v2", "name": "V2视觉语言复核专家", "role": "语义复核", "provider": "internal", "enabled": True, "weight": 0.18},
    {"id": "aliyun_pro", "name": "AIGC专业版专家", "role": "生成检测", "provider": "aliyun", "enabled": True, "weight": 0.16, "modelId": "aliyun-aigc-pro"},
    {"id": "aliyun_full", "name": "隐式标识专家", "role": "标识复核", "provider": "aliyun", "enabled": True, "weight": 0.08, "modelId": "aliyun-aigc-full"},
    {"id": "aliyun_ultra", "name": "局部编辑专家", "role": "局部伪造", "provider": "aliyun", "enabled": True, "weight": 0.09, "modelId": "aliyun-aigc-ultra"},
    {"id": "aliyun_ps", "name": "篡改痕迹专家", "role": "PS篡改", "provider": "aliyun", "enabled": True, "weight": 0.05, "modelId": "aliyun-ps-detector"},
    {"id": "aliyun_recap", "name": "翻拍风险专家", "role": "翻拍检测", "provider": "aliyun", "enabled": True, "weight": 0.02, "modelId": "aliyun-recap-detector"},
    {"id": "visible_watermark", "name": "AI 平台水印识别专家", "role": "平台水印复核", "provider": "hybrid", "enabled": True, "weight": 0.0},
]
HEALTH_CACHE_TTL_SECONDS = int(os.environ.get("REALGUARD_MODEL_HEALTH_CACHE_SECONDS", "20"))
_HEALTH_CACHE = {}
_HEALTH_CACHE_LOCK = threading.Lock()
_HEALTH_INFLIGHT = {}


def _truthy(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _default_registry():
    detector_base = os.environ.get("REALGUARD_DETECTION_BACKEND_URL", "http://127.0.0.1:15000").rstrip("/")
    legacy_v1_base = os.environ.get("REALGUARD_V1_LEGACY_BACKEND_URL", "http://127.0.0.1:15000").rstrip("/")
    v2_detect_url = os.environ.get("REALGUARD_V2_INTERNAL_DETECT_URL", "http://127.0.0.1:8848/api/detect").strip()
    artifact_root = PROJECT_ROOT / "imagedetection" / "Agent" / "tools" / "AIGC_Detection"
    return {
        "version": 1,
        "updatedAt": "",
        "routing": {
            "imagePrimary": "v1-onnx-mil",
            "imageFallback": "v2-qwen-vlm",
            "fallbackEnabled": _truthy(os.environ.get("REALGUARD_IMAGE_DETECT_FALLBACK", "0")),
            "fallbackMode": "automatic",
            "notes": "V1 is the primary image detector. V2 fallback is disabled by default to avoid silent model substitution.",
        },
        "swarm": {
            "enabled": _truthy(os.environ.get("REALGUARD_SWARM_ENABLED", "1")),
            "minExperts": int(os.environ.get("REALGUARD_SWARM_MIN_EXPERTS", "2")),
            "consensusThreshold": float(os.environ.get("REALGUARD_SWARM_CONSENSUS_THRESHOLD", "0.65")),
            "disagreementThreshold": float(os.environ.get("REALGUARD_SWARM_DISAGREEMENT_THRESHOLD", "0.35")),
            "notes": "Swarm mode dispatches image samples to multiple forensic experts and aggregates weighted votes.",
            "experts": deepcopy(DEFAULT_SWARM_EXPERTS),
        },
        "models": [
            {
                "id": "v1-onnx-mil",
                "name": "RealGuard V1 ONNX/MIL",
                "family": "AIGC image classifier",
                "version": "v1-onnx-mil",
                "role": "primary",
                "modality": "image",
                "enabled": True,
                "endpoint": f"{detector_base}/image",
                "healthUrl": f"{detector_base}/health",
                "timeoutSeconds": int(os.environ.get("REALGUARD_IMAGE_DETECT_TIMEOUT", "180")),
                "runtime": "onnxruntime-cpu",
                "artifactPath": str(artifact_root / "model_deploy.onnx"),
                "externalDataPath": str(artifact_root / "model_deploy.onnx.data"),
                "description": "CLIP/MIL-style AIGC detector exported to ONNX. Requires the external .onnx.data weight file.",
            },
            {
                "id": "v1-legacy-tunnel",
                "name": "RealGuard V1 Legacy Tunnel",
                "family": "AIGC image classifier",
                "version": "v1-legacy-agent",
                "role": "primary",
                "modality": "image",
                "enabled": True,
                "endpoint": f"{legacy_v1_base}/image",
                "healthUrl": f"{legacy_v1_base}/image",
                "timeoutSeconds": int(os.environ.get("REALGUARD_IMAGE_DETECT_TIMEOUT", "180")),
                "runtime": "legacy-flask-ssh-tunnel",
                "artifactPath": "",
                "externalDataPath": "",
                "description": "Legacy V1 API currently exposed through port 15000. Keep it visible as a model route, but migrate to managed v1-onnx-mil once the external ONNX weight file is restored.",
            },
            {
                "id": "v2-qwen-vlm",
                "name": "Jianzhen V2 Qwen VLM",
                "family": "vision language model",
                "version": os.environ.get("VLM_MODEL", "qwen3-vl-flash"),
                "role": "fallback",
                "modality": "image,document",
                "enabled": True,
                "endpoint": v2_detect_url,
                "healthUrl": os.environ.get("REALGUARD_V2_HEALTH_URL", "http://127.0.0.1:8848/api/health"),
                "timeoutSeconds": int(os.environ.get("REALGUARD_V2_DETECT_TIMEOUT", "180")),
                "runtime": "dashscope-vlm",
                "artifactPath": "",
                "externalDataPath": "",
                "description": "V2 multimodal fallback and API model. Useful when V1 is unavailable, but may differ from V1 behavior.",
            },
            {
                "id": "aliyun-aigc-pro",
                "name": "Aliyun AIGC Detector Pro",
                "family": "external AIGC image forensics",
                "version": "aigcDetector_pro",
                "role": "candidate",
                "modality": "image",
                "enabled": True,
                "endpoint": "internal://aliyun/aigcDetector_pro",
                "healthUrl": "internal://aliyun/aigcDetector_pro",
                "timeoutSeconds": 60,
                "runtime": "aliyun-green",
                "artifactPath": "",
                "externalDataPath": "",
                "description": "阿里云 AIGC 鉴伪专业版，用于 AI 生成、合成图和人脸合成风险检测。",
            },
            {
                "id": "aliyun-aigc-full",
                "name": "Aliyun AIGC Detector Full Marker",
                "family": "external AIGC image forensics",
                "version": "aigcDetectorFull",
                "role": "review",
                "modality": "image",
                "enabled": True,
                "endpoint": "internal://aliyun/aigcDetectorFull",
                "healthUrl": "internal://aliyun/aigcDetectorFull",
                "timeoutSeconds": 60,
                "runtime": "aliyun-green",
                "artifactPath": "",
                "externalDataPath": "",
                "description": "阿里云 AIGC 鉴伪全量版，补充隐式标识和元数据检测，适合冲突样本复核。",
            },
            {
                "id": "aliyun-aigc-ultra",
                "name": "Aliyun AIGC Detector Ultra",
                "family": "external AIGC image forensics",
                "version": "aigcDetector_ultra",
                "role": "review",
                "modality": "image",
                "enabled": True,
                "endpoint": "internal://aliyun/aigcDetector_ultra",
                "healthUrl": "internal://aliyun/aigcDetector_ultra",
                "timeoutSeconds": 60,
                "runtime": "aliyun-green",
                "artifactPath": "",
                "externalDataPath": "",
                "description": "阿里云 AIGC 鉴伪旗舰版，用于高风险和局部 AI 编辑冲突样本升级复核。",
            },
            {
                "id": "aliyun-ps-detector",
                "name": "Aliyun PS Detector",
                "family": "external tamper forensics",
                "version": "psDetector",
                "role": "review",
                "modality": "image",
                "enabled": True,
                "endpoint": "internal://aliyun/psDetector",
                "healthUrl": "internal://aliyun/psDetector",
                "timeoutSeconds": 60,
                "runtime": "aliyun-green",
                "artifactPath": "",
                "externalDataPath": "",
                "description": "阿里云 PS 篡改检测，适合证件、材料、截图等专项复核。",
            },
            {
                "id": "aliyun-recap-detector",
                "name": "Aliyun Recapture Detector",
                "family": "external recapture forensics",
                "version": "recapDetector",
                "role": "review",
                "modality": "image",
                "enabled": True,
                "endpoint": "internal://aliyun/recapDetector",
                "healthUrl": "internal://aliyun/recapDetector",
                "timeoutSeconds": 60,
                "runtime": "aliyun-green",
                "artifactPath": "",
                "externalDataPath": "",
                "description": "阿里云翻拍检测，适合屏幕翻拍和二次拍摄专项复核。",
            },
        ],
    }


def _merge_defaults(saved):
    default = _default_registry()
    if not isinstance(saved, dict):
        return default
    merged = deepcopy(default)
    merged["version"] = saved.get("version", merged["version"])
    merged["updatedAt"] = saved.get("updatedAt", merged["updatedAt"])
    if isinstance(saved.get("routing"), dict):
        merged["routing"].update(saved["routing"])
    if isinstance(saved.get("swarm"), dict):
        merged["swarm"].update({key: saved["swarm"].get(key, merged["swarm"].get(key)) for key in ("enabled", "minExperts", "consensusThreshold", "disagreementThreshold", "notes")})
        saved_experts = {
            str(expert.get("id")): expert
            for expert in saved["swarm"].get("experts", [])
            if isinstance(expert, dict) and expert.get("id")
        }
        experts = []
        for expert in deepcopy(DEFAULT_SWARM_EXPERTS):
            override = saved_experts.pop(expert["id"], None)
            if override:
                expert.update(override)
            experts.append(expert)
        experts.extend(saved_experts.values())
        merged["swarm"]["experts"] = experts
    if isinstance(saved.get("routingHistory"), list):
        merged["routingHistory"] = saved["routingHistory"][:30]
    else:
        merged["routingHistory"] = []
    saved_models = {
        str(model.get("id")): model
        for model in saved.get("models", [])
        if isinstance(model, dict) and model.get("id")
    }
    models = []
    for model in merged["models"]:
        override = saved_models.pop(model["id"], None)
        if override:
            model.update(override)
        models.append(model)
    models.extend(saved_models.values())
    merged["models"] = models
    return merged


def _registry_lock_path():
    return REGISTRY_PATH.with_suffix(REGISTRY_PATH.suffix + ".lock")


@contextmanager
def _registry_lock(exclusive=False):
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _registry_lock_path()
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _load_registry_unlocked():
    try:
        if REGISTRY_PATH.exists():
            return _merge_defaults(json.loads(REGISTRY_PATH.read_text(encoding="utf-8")))
    except Exception as exc:
        print(f"[MODEL REGISTRY ERROR] load failed: {exc}")
    return _default_registry()


def load_registry():
    with _registry_lock():
        return _load_registry_unlocked()


def _save_registry_unlocked(registry):
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = deepcopy(registry)
    data["updatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
    descriptor, tmp_name = tempfile.mkstemp(prefix=f".{REGISTRY_PATH.name}.", suffix=".tmp", dir=REGISTRY_PATH.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, 0o600)
        tmp_path.replace(REGISTRY_PATH)
        os.chmod(REGISTRY_PATH, 0o600)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return data


def save_registry(registry):
    with _registry_lock(exclusive=True):
        return _save_registry_unlocked(registry)


def list_models():
    return load_registry().get("models", [])


def get_model(model_id):
    for model in list_models():
        if model.get("id") == model_id:
            return model
    return None


def get_routing():
    return load_registry().get("routing", {})


def get_swarm_config():
    return load_registry().get("swarm", _default_registry()["swarm"])


def _coerce_swarm_config(current, updates):
    config = deepcopy(current or _default_registry()["swarm"])
    if "enabled" in updates:
        config["enabled"] = _truthy(updates.get("enabled"))
    for key in ("notes",):
        if key in updates:
            config[key] = str(updates.get(key) or "").strip()
    for key, default, minimum, maximum in (
        ("minExperts", 2, 1, 20),
    ):
        if key in updates:
            try:
                config[key] = max(minimum, min(int(updates.get(key)), maximum))
            except (TypeError, ValueError):
                config[key] = default
    for key, default in (
        ("consensusThreshold", 0.65),
        ("disagreementThreshold", 0.35),
    ):
        if key in updates:
            try:
                config[key] = max(0.0, min(float(updates.get(key)), 1.0))
            except (TypeError, ValueError):
                config[key] = default
    if isinstance(updates.get("experts"), list):
        current_by_id = {
            str(expert.get("id")): deepcopy(expert)
            for expert in config.get("experts", [])
            if isinstance(expert, dict) and expert.get("id")
        }
        merged = []
        for incoming in updates.get("experts") or []:
            if not isinstance(incoming, dict) or not incoming.get("id"):
                continue
            expert_id = str(incoming.get("id"))
            expert = current_by_id.pop(expert_id, {"id": expert_id})
            for key in ("name", "role", "provider", "modelId"):
                if key in incoming:
                    expert[key] = str(incoming.get(key) or "").strip()
            if "enabled" in incoming:
                expert["enabled"] = _truthy(incoming.get("enabled"))
            if "weight" in incoming:
                try:
                    expert["weight"] = max(0.0, min(float(incoming.get("weight")), 1.0))
                except (TypeError, ValueError):
                    expert["weight"] = 0.0
            merged.append(expert)
        merged.extend(current_by_id.values())
        config["experts"] = merged
    return config


def update_swarm_config(updates):
    with _registry_lock(exclusive=True):
        registry = _load_registry_unlocked()
        before = deepcopy(registry.get("swarm") or _default_registry()["swarm"])
        registry["swarm"] = _coerce_swarm_config(before, updates or {})
        return _save_registry_unlocked(registry)["swarm"]


def routing_history(limit=10):
    try:
        limit = max(1, min(int(limit), 30))
    except (TypeError, ValueError):
        limit = 10
    return load_registry().get("routingHistory", [])[:limit]


def _coerce_model_payload(payload):
    model_id = str(payload.get("id") or "").strip()
    if not MODEL_ID_RE.match(model_id):
        return None, "模型 ID 只能包含字母、数字、点、下划线、冒号和连字符，长度 2-64"
    timeout = payload.get("timeoutSeconds", 180)
    try:
        timeout = max(1, min(int(timeout), 600))
    except (TypeError, ValueError):
        timeout = 180
    return {
        "id": model_id,
        "name": str(payload.get("name") or model_id).strip(),
        "family": str(payload.get("family") or "").strip(),
        "version": str(payload.get("version") or "").strip(),
        "role": str(payload.get("role") or "candidate").strip(),
        "modality": str(payload.get("modality") or "image").strip(),
        "enabled": _truthy(payload.get("enabled", True)),
        "endpoint": str(payload.get("endpoint") or "").strip(),
        "healthUrl": str(payload.get("healthUrl") or "").strip(),
        "timeoutSeconds": timeout,
        "runtime": str(payload.get("runtime") or "custom-http").strip(),
        "artifactPath": str(payload.get("artifactPath") or "").strip(),
        "externalDataPath": str(payload.get("externalDataPath") or "").strip(),
        "description": str(payload.get("description") or "").strip(),
    }, ""


def create_model(payload):
    model, error = _coerce_model_payload(payload or {})
    if error:
        return None, None, error
    with _registry_lock(exclusive=True):
        registry = _load_registry_unlocked()
        if any(item.get("id") == model["id"] for item in registry.get("models", [])):
            return None, None, "模型 ID 已存在"
        registry.setdefault("models", []).append(model)
        return _save_registry_unlocked(registry), model, ""


def update_model(model_id, updates):
    allowed = {
        "name",
        "family",
        "version",
        "role",
        "modality",
        "enabled",
        "endpoint",
        "healthUrl",
        "timeoutSeconds",
        "runtime",
        "artifactPath",
        "externalDataPath",
        "description",
    }
    with _registry_lock(exclusive=True):
        registry = _load_registry_unlocked()
        for model in registry.get("models", []):
            if model.get("id") == model_id:
                candidate = dict(model)
                candidate.update({key: value for key, value in updates.items() if key in allowed})
                candidate["id"] = model_id
                normalized, error = _coerce_model_payload(candidate)
                if error:
                    return None, None
                model.clear()
                model.update(normalized)
                return _save_registry_unlocked(registry), model
    return None, None


def delete_model(model_id):
    with _registry_lock(exclusive=True):
        registry = _load_registry_unlocked()
        routing = registry.get("routing", {})
        if model_id in (routing.get("imagePrimary"), routing.get("imageFallback")):
            return None, "模型正在路由策略中使用，不能删除"
        models = registry.get("models", [])
        remaining = [model for model in models if model.get("id") != model_id]
        if len(remaining) == len(models):
            return None, "模型不存在"
        registry["models"] = remaining
        return _save_registry_unlocked(registry), ""


def _validated_routing(registry, updates):
    routing = deepcopy(registry.get("routing") or {})
    allowed = {"imagePrimary", "imageFallback", "fallbackEnabled", "fallbackMode", "notes"}
    for key, value in (updates or {}).items():
        if key in allowed:
            routing[key] = value
    routing["imagePrimary"] = str(routing.get("imagePrimary") or "").strip()
    routing["imageFallback"] = str(routing.get("imageFallback") or "").strip()
    routing["fallbackEnabled"] = _truthy(routing.get("fallbackEnabled"))
    routing["fallbackMode"] = str(routing.get("fallbackMode") or "automatic").strip()
    routing["notes"] = str(routing.get("notes") or "").strip()[:1000]
    if routing["fallbackMode"] not in ("automatic", "manual"):
        raise ValueError("fallbackMode 仅支持 automatic 或 manual")
    models = {str(model.get("id")): model for model in registry.get("models", [])}
    primary = models.get(routing["imagePrimary"])
    if not primary:
        raise ValueError("主模型不存在")
    if primary.get("enabled") is False or "image" not in str(primary.get("modality") or "").lower():
        raise ValueError("主模型必须是已启用的图像模型")
    fallback = models.get(routing["imageFallback"]) if routing["imageFallback"] else None
    if routing["fallbackEnabled"]:
        if not fallback:
            raise ValueError("启用兜底时必须选择存在的兜底模型")
        if fallback.get("enabled") is False or "image" not in str(fallback.get("modality") or "").lower():
            raise ValueError("兜底模型必须是已启用的图像模型")
        if routing["imageFallback"] == routing["imagePrimary"]:
            raise ValueError("主模型和兜底模型不能相同")
    return routing


def update_routing(updates):
    with _registry_lock(exclusive=True):
        registry = _load_registry_unlocked()
        before = deepcopy(registry.get("routing") or {})
        routing = _validated_routing(registry, updates)
        registry["routing"] = routing
        if before != routing:
            history = registry.setdefault("routingHistory", [])
            history.insert(0, {
                "id": f"route_{int(time.time() * 1000)}",
                "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                "routing": before,
                "after": deepcopy(routing),
            })
            registry["routingHistory"] = history[:30]
        return _save_registry_unlocked(registry)["routing"]


def rollback_routing(snapshot_id=None):
    with _registry_lock(exclusive=True):
        registry = _load_registry_unlocked()
        history = registry.get("routingHistory", [])
        if not history:
            return None, None, "没有可回滚的路由快照"
        snapshot = None
        if snapshot_id:
            snapshot = next((item for item in history if item.get("id") == snapshot_id), None)
            if not snapshot:
                return None, None, "指定路由快照不存在"
        else:
            snapshot = history[0]
        target = deepcopy(snapshot.get("routing") or {})
        if not target:
            return None, snapshot, "路由快照内容为空"
        try:
            target = _validated_routing(registry, target)
        except ValueError as exc:
            return None, snapshot, f"路由快照已失效：{exc}"
        current = deepcopy(registry.get("routing") or {})
        registry["routing"] = target
        history.insert(0, {
            "id": f"route_{int(time.time() * 1000)}",
            "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "routing": current,
            "after": target,
            "rollbackOf": snapshot.get("id"),
        })
        registry["routingHistory"] = history[:30]
        return _save_registry_unlocked(registry)["routing"], snapshot, ""


def artifact_status(model):
    artifact_path = str(model.get("artifactPath") or "")
    external_path = str(model.get("externalDataPath") or "")

    def describe(path):
        if not path:
            return {"path": "", "exists": None, "sizeBytes": None, "size": ""}
        p = Path(path)
        if not p.exists():
            return {"path": path, "exists": False, "sizeBytes": 0, "size": "missing"}
        size = p.stat().st_size
        return {"path": path, "exists": True, "sizeBytes": size, "size": _human_size(size)}

    return {
        "artifact": describe(artifact_path),
        "externalData": describe(external_path),
    }


def model_artifact_ready(model):
    artifacts = artifact_status(model)
    warnings = []
    if model.get("id") == "v1-onnx-mil":
        artifact = artifacts["artifact"]
        external = artifacts["externalData"]
        min_external = int(os.environ.get("REALGUARD_V1_EXTERNAL_MIN_BYTES", str(100 * 1024 * 1024)))
        if artifact["exists"] is False:
            warnings.append("missing ONNX graph file: model_deploy.onnx")
        if external["exists"] is False:
            warnings.append("missing external ONNX weight file: model_deploy.onnx.data")
        elif external["sizeBytes"] is not None and external["sizeBytes"] < min_external:
            warnings.append(
                "external ONNX weight file is too small: "
                f"{external['size']} < {_human_size(min_external)}"
            )
    return not warnings, warnings, artifacts


def _human_size(size):
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{value:.1f}GB"


def check_model_health(model):
    if aliyun_green.is_aliyun_model(model):
        return aliyun_green.health(model)

    start = time.time()
    health_url = str(model.get("healthUrl") or "").strip()
    status = {
        "ok": False,
        "serviceOk": False,
        "artifactReady": True,
        "dependencyReady": True,
        "capabilityReady": False,
        "httpStatus": None,
        "latencyMs": None,
        "message": "healthUrl not configured",
        "warnings": [],
        "checkedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if health_url:
        try:
            with requests.Session() as sess:
                sess.trust_env = False
                resp = sess.get(health_url, timeout=min(int(model.get("timeoutSeconds") or 10), 10))
            status["httpStatus"] = resp.status_code
            status["serviceOk"] = 200 <= resp.status_code < 300
            status["message"] = "service reachable" if status["serviceOk"] else resp.text[:120]
            try:
                payload = resp.json()
                if "serviceOk" in payload:
                    status["serviceOk"] = status["serviceOk"] and _truthy(payload.get("serviceOk"))
                if str(payload.get("status") or "").strip().lower() in ("error", "failed", "offline", "unhealthy"):
                    status["serviceOk"] = False
                if "artifactReady" in payload:
                    status["artifactReady"] = _truthy(payload.get("artifactReady"))
                if "dependencyReady" in payload:
                    status["dependencyReady"] = _truthy(payload.get("dependencyReady"))
                remote = payload.get("remoteInference") if isinstance(payload.get("remoteInference"), dict) else {}
                status["telemetry"] = {
                    "inferenceMode": str(payload.get("inferenceMode") or ""),
                    "activeProvider": str(remote.get("activeProvider") or payload.get("activeProvider") or ""),
                    "cudaDeviceId": remote.get("cudaDeviceId", payload.get("cudaDeviceId")),
                    "remoteReady": remote.get("ready"),
                    "remoteLatencyMs": remote.get("latencyMs"),
                    "queueDepth": payload.get("queueDepth"),
                    "gpu": payload.get("gpu") if isinstance(payload.get("gpu"), dict) else None,
                }
                for warning in payload.get("warnings") or []:
                    if warning not in status["warnings"]:
                        status["warnings"].append(str(warning))
            except ValueError:
                pass
        except Exception as exc:
            status["message"] = str(exc)
    status["latencyMs"] = int((time.time() - start) * 1000)

    artifact_ready, artifact_warnings, artifacts = model_artifact_ready(model)
    status["artifactReady"] = status["artifactReady"] and artifact_ready
    for warning in artifact_warnings:
        if warning not in status["warnings"]:
            status["warnings"].append(warning)
    status["artifacts"] = artifacts
    status["capabilityReady"] = status["artifactReady"] and status["dependencyReady"]
    status["ok"] = status["serviceOk"] and status["capabilityReady"]
    if status["serviceOk"] and not status["capabilityReady"]:
        status["message"] = "service reachable but model capability is not ready"
    elif status["ok"]:
        status["message"] = "ok"
    return status


def _health_cache_key(model):
    relevant = {
        key: model.get(key)
        for key in (
            "id",
            "enabled",
            "runtime",
            "healthUrl",
            "timeoutSeconds",
            "artifactPath",
            "externalDataPath",
        )
    }
    return json.dumps(relevant, sort_keys=True, ensure_ascii=True, default=str)


def check_models_health(models, force=False):
    """Probe model health concurrently and reuse recent results across admin panels."""
    now = time.time()
    results = {}
    pending = []
    waiting = []
    with _HEALTH_CACHE_LOCK:
        for model in models:
            model_id = str(model.get("id") or "")
            key = _health_cache_key(model)
            cached = _HEALTH_CACHE.get(key)
            if not force and cached and now < cached[0]:
                results[model_id] = deepcopy(cached[1])
            elif not force and key in _HEALTH_INFLIGHT:
                waiting.append((model_id, key, model, _HEALTH_INFLIGHT[key]))
            else:
                event = None
                if not force:
                    event = threading.Event()
                    _HEALTH_INFLIGHT[key] = event
                pending.append((model_id, key, model, event))
    if pending:
        workers = min(max(1, len(pending)), int(os.environ.get("REALGUARD_MODEL_HEALTH_WORKERS", "6")))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="model-health") as executor:
            futures = {
                executor.submit(check_model_health, model): (model_id, key, event)
                for model_id, key, model, event in pending
            }
            for future in as_completed(futures):
                model_id, key, owner_event = futures[future]
                try:
                    health = future.result()
                except Exception as exc:
                    health = {
                        "ok": False,
                        "serviceOk": False,
                        "message": str(exc),
                        "checkedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                results[model_id] = health
                with _HEALTH_CACHE_LOCK:
                    _HEALTH_CACHE[key] = (time.time() + max(1, HEALTH_CACHE_TTL_SECONDS), deepcopy(health))
                    if owner_event is not None:
                        _HEALTH_INFLIGHT.pop(key, None)
                        owner_event.set()
    for model_id, key, model, event in waiting:
        timeout = max(5, min(int(model.get("timeoutSeconds") or 10) + 2, 190))
        event.wait(timeout=timeout)
        with _HEALTH_CACHE_LOCK:
            cached = _HEALTH_CACHE.get(key)
        if cached:
            results[model_id] = deepcopy(cached[1])
            continue
        health = check_model_health(model)
        results[model_id] = health
        with _HEALTH_CACHE_LOCK:
            _HEALTH_CACHE[key] = (time.time() + max(1, HEALTH_CACHE_TTL_SECONDS), deepcopy(health))
    return results


def clear_health_cache():
    with _HEALTH_CACHE_LOCK:
        _HEALTH_CACHE.clear()

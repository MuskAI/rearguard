"""C2PA content credential expert for the Swarm pipeline.

Reads the C2PA manifest embedded in an image (via contentauth/c2pa-python),
validates the signature chain and inspects ``claim_generator`` plus
``digitalSourceType`` assertions to decide whether the image was AI-generated
or captured by a real device.

Returns the standard Swarm expert update dict:
    status, score, verdict, confidence, evidence, message, latencyMs
"""

from __future__ import annotations

import io
import json
import mimetypes
import re
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    import c2pa as _c2pa_lib  # type: ignore
    _C2PA_AVAILABLE = True
    _C2PA_IMPORT_ERROR: Optional[str] = None
except Exception as exc:  # pragma: no cover - import guard
    _c2pa_lib = None
    _C2PA_AVAILABLE = False
    _C2PA_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


AI_GENERATOR_PATTERNS = (
    "midjourney",
    "stable diffusion",
    "stablediffusion",
    "stability",
    "comfyui",
    "dall-e",
    "dall·e",
    "dalle",
    "openai",
    "firefly",
    "adobe firefly",
    "imagen",
    "ideogram",
    "flux",
    "black forest labs",
    "runwayml",
    "leonardo",
    "kling",
    "qwen-image",
    "wan-",
    "sora",
    "nano banana",
    "gemini image",
    "google ai",
    "deepseek",
    "synthid",
)

AI_DIGITAL_SOURCE_TYPES = {
    "http://cv.iptc.org/newscodes/digitalsourcetype/trainedalgorithmicmedia",
    "http://cv.iptc.org/newscodes/digitalsourcetype/algorithmicmedia",
    "http://cv.iptc.org/newscodes/digitalsourcetype/compositesynthetic",
    "http://cv.iptc.org/newscodes/digitalsourcetype/synthetic",
}

CAMERA_DIGITAL_SOURCE_TYPES = {
    "http://cv.iptc.org/newscodes/digitalsourcetype/digitalcapture",
    "http://cv.iptc.org/newscodes/digitalsourcetype/negativefilm",
    "http://cv.iptc.org/newscodes/digitalsourcetype/positivefilm",
    "http://cv.iptc.org/newscodes/digitalsourcetype/print",
}


def is_available() -> bool:
    return _C2PA_AVAILABLE


def import_error() -> Optional[str]:
    return _C2PA_IMPORT_ERROR


def _truncate(text: str, limit: int = 120) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _guess_mime(filename: Optional[str], fallback: Optional[str]) -> str:
    if fallback:
        return fallback
    if filename:
        guess, _ = mimetypes.guess_type(filename)
        if guess:
            return guess
    return "image/jpeg"


def _read_manifest(image_bytes: bytes, mime: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Returns (parsed_manifest_json, error_message). Either may be None."""
    if not _C2PA_AVAILABLE or _c2pa_lib is None:
        return None, _C2PA_IMPORT_ERROR or "c2pa-python 不可用"
    try:
        stream = io.BytesIO(image_bytes)
        reader = _c2pa_lib.Reader(mime, stream)
    except Exception as exc:
        # ManifestNotFound is the expected "no provenance" case
        name = type(exc).__name__
        if "NotFound" in name or "ManifestNotFound" in name:
            return None, "no_manifest"
        return None, f"{name}: {_truncate(str(exc), 200)}"
    try:
        raw = reader.json()
    except Exception as exc:
        return None, f"reader.json failed: {_truncate(str(exc), 160)}"
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        return None, f"manifest JSON 解析失败: {_truncate(str(exc), 160)}"
    return parsed, None


def _collect_generators(manifest: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    if not isinstance(manifest, dict):
        return out
    direct = manifest.get("claim_generator") or manifest.get("claim_generator_info")
    if isinstance(direct, str):
        out.append(direct)
    elif isinstance(direct, list):
        for item in direct:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                name = item.get("name")
                if isinstance(name, str):
                    out.append(name)
    for assertion in manifest.get("assertions") or []:
        if not isinstance(assertion, dict):
            continue
        data = assertion.get("data") or {}
        if isinstance(data, dict):
            name = data.get("claim_generator") or data.get("name")
            if isinstance(name, str):
                out.append(name)
    return out


def _collect_digital_source_types(manifest: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    if not isinstance(manifest, dict):
        return out
    for assertion in manifest.get("assertions") or []:
        if not isinstance(assertion, dict):
            continue
        label = (assertion.get("label") or "").lower()
        data = assertion.get("data") or {}
        if not isinstance(data, dict):
            continue
        if "actions" in label or "actions" in data:
            actions = data.get("actions") or []
            for action in actions:
                if not isinstance(action, dict):
                    continue
                dst = action.get("digitalSourceType")
                if isinstance(dst, str):
                    out.append(dst)
        dst = data.get("digitalSourceType")
        if isinstance(dst, str):
            out.append(dst)
    return out


def _validation_summary(manifest_envelope: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Return (severity, issues) where missing validation is never trusted."""
    results = manifest_envelope.get("validation_results") if isinstance(manifest_envelope, dict) else None
    issues: List[str] = []
    severity = "unknown"
    saw_success = False
    if isinstance(results, dict):
        for bucket_name in ("activeManifest", "active_manifest", "ingredient_active_manifest"):
            bucket = results.get(bucket_name) if isinstance(results.get(bucket_name), dict) else None
            if not bucket:
                continue
            for level in ("failure", "informational", "success"):
                items = bucket.get(level) or []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    code = item.get("code") or ""
                    explanation = item.get("explanation") or item.get("url") or code
                    if level == "failure":
                        severity = "error"
                        issues.append(f"签名校验失败: {_truncate(explanation, 80)}")
                    elif level == "informational" and severity != "error":
                        if any(k in code.lower() for k in ("trust", "untrusted", "revoked")):
                            severity = "warning"
                            issues.append(f"信任链警告: {_truncate(explanation, 80)}")
                    elif level == "success":
                        saw_success = True
    if saw_success and severity == "unknown":
        severity = "ok"
    if severity == "unknown":
        issues.append("C2PA 未返回可验证的签名校验结果。")
    return severity, issues


def _collect_signer_info(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Extract signer / certificate metadata when present."""
    if not isinstance(manifest, dict):
        return {}
    info: Dict[str, Any] = {}
    sig = manifest.get("signature_info") or manifest.get("signatureInfo") or {}
    if isinstance(sig, dict):
        for k in ("issuer", "common_name", "alg", "time", "cert_serial_number"):
            v = sig.get(k)
            if isinstance(v, (str, int, float)):
                info[k] = v
    credentials = manifest.get("credentials") or []
    if isinstance(credentials, list) and credentials:
        first = credentials[0]
        if isinstance(first, dict):
            for k in ("issuer", "subject"):
                v = first.get(k)
                if isinstance(v, (str, dict)):
                    info[f"credential_{k}"] = v
    return info


def _collect_actions(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pick out the c2pa.actions assertions and return a flat action list."""
    if not isinstance(manifest, dict):
        return []
    out: List[Dict[str, Any]] = []
    for assertion in manifest.get("assertions") or []:
        if not isinstance(assertion, dict):
            continue
        label = str(assertion.get("label") or "").lower()
        if "actions" not in label:
            continue
        data = assertion.get("data") or {}
        if not isinstance(data, dict):
            continue
        actions = data.get("actions") or []
        if not isinstance(actions, list):
            continue
        for action in actions:
            if not isinstance(action, dict):
                continue
            entry = {
                "action": str(action.get("action") or "").strip(),
                "when": str(action.get("when") or "").strip(),
                "softwareAgent": str(action.get("softwareAgent") or "").strip(),
                "digitalSourceType": str(action.get("digitalSourceType") or "").strip(),
            }
            out.append({k: v for k, v in entry.items() if v})
    return out


def _walk_manifest_chain(manifest_envelope: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Walk the active manifest plus every ingredient's active manifest in
    breadth-first order, returning a list of ``{label, manifest, role}`` dicts
    where ``role`` is ``"active"`` for the root and ``"ingredient"`` otherwise."""
    if not isinstance(manifest_envelope, dict):
        return []
    manifests = manifest_envelope.get("manifests") or {}
    if not isinstance(manifests, dict):
        return []

    active_label = manifest_envelope.get("active_manifest")
    if not active_label or active_label not in manifests:
        for label in manifests:
            active_label = label
            break

    walked: List[Dict[str, Any]] = []
    seen: set = set()
    queue: List[Tuple[str, str]] = []
    if active_label:
        queue.append((active_label, "active"))

    while queue:
        label, role = queue.pop(0)
        if label in seen:
            continue
        seen.add(label)
        manifest = manifests.get(label)
        if not isinstance(manifest, dict):
            continue
        walked.append({"label": label, "manifest": manifest, "role": role})
        for ingredient in manifest.get("ingredients") or []:
            if not isinstance(ingredient, dict):
                continue
            ref = (
                ingredient.get("active_manifest")
                or ingredient.get("activeManifest")
                or ingredient.get("manifest")
            )
            if isinstance(ref, str) and ref:
                queue.append((ref, "ingredient"))
    return walked


def _summarize_source(manifest: Dict[str, Any]) -> str:
    """Classify a single manifest as 'ai', 'camera', or 'unknown' so we can
    detect chain inconsistencies."""
    generators = _collect_generators(manifest)
    dsts = _collect_digital_source_types(manifest)
    gen_blob = " | ".join(generators).lower()
    if any(pat in gen_blob for pat in AI_GENERATOR_PATTERNS):
        return "ai"
    if any(dst in AI_DIGITAL_SOURCE_TYPES for dst in dsts):
        return "ai"
    if any(dst in CAMERA_DIGITAL_SOURCE_TYPES for dst in dsts):
        return "camera"
    return "unknown"


def _decide_verdict(
    generators: List[str],
    digital_source_types: List[str],
    validation_severity: str,
    validation_issues: List[str],
) -> Tuple[float, str, str, List[str]]:
    """Returns (score, verdict, confidence_label, evidence)."""
    evidence: List[str] = []
    gen_blob = " | ".join(generators).lower()

    ai_gen_hits = [pat for pat in AI_GENERATOR_PATTERNS if pat in gen_blob]
    ai_dst_hits = [dst for dst in digital_source_types if dst in AI_DIGITAL_SOURCE_TYPES]
    cam_dst_hits = [dst for dst in digital_source_types if dst in CAMERA_DIGITAL_SOURCE_TYPES]

    if validation_severity == "error":
        evidence.extend(validation_issues[:2])
        evidence.append("C2PA 签名链校验失败，凭证可能被篡改。")
        return 0.78, "C2PA 签名校验失败", "中", evidence

    if ai_gen_hits or ai_dst_hits:
        if ai_gen_hits:
            evidence.append(f"C2PA claim_generator 指向 AI 工具：{', '.join(sorted(set(ai_gen_hits))[:3])}")
        if ai_dst_hits:
            evidence.append("C2PA digitalSourceType 标注为合成媒体。")
        if validation_severity != "ok":
            evidence.append(validation_issues[0] if validation_issues else "C2PA 信任链未验证。")
            return 0.58, "C2PA 声明为生成内容（凭证未验证）", "低", evidence
        return 0.94, "C2PA 标识为生成内容", "高", evidence

    if cam_dst_hits:
        if validation_severity != "ok":
            evidence.append("C2PA 声明为相机拍摄，但签名或信任链未验证；该声明保持中性。")
            return 0.5, "C2PA 相机声明未验证", "低", evidence
        evidence.append("C2PA digitalSourceType 标注为相机捕获。")
        if generators:
            evidence.append(f"凭证签名方：{_truncate(generators[0], 60)}")
        return 0.08, "C2PA 标识为真实拍摄", "高", evidence

    if generators:
        evidence.append(f"C2PA 凭证由 {_truncate(generators[0], 60)} 签发，但来源类别未明示。")
        if validation_severity != "ok":
            evidence.append(validation_issues[0] if validation_issues else "C2PA 信任链未验证。")
        return 0.5, "C2PA 凭证存在但来源未知", "低", evidence

    evidence.append("C2PA 凭证缺少 claim_generator 与 digitalSourceType 信息。")
    return 0.5, "C2PA 凭证信息不完整", "低", evidence


def run_c2pa_expert(image_bytes: bytes, filename: Optional[str], mimetype: Optional[str]) -> Dict[str, Any]:
    started = time.time()

    def _finish(update: Dict[str, Any]) -> Dict[str, Any]:
        latency = int((time.time() - started) * 1000)
        update.setdefault("latencyMs", latency)
        update.setdefault("provenance_kind", "c2pa")
        return update

    if not _C2PA_AVAILABLE:
        return _finish({
            "status": "skipped",
            "score": None,
            "verdict": "未安装 C2PA 库",
            "confidence": "",
            "evidence": [],
            "message": f"c2pa-python 不可用：{_truncate(_C2PA_IMPORT_ERROR or '', 80)}",
        })

    if not image_bytes:
        return _finish({
            "status": "failed",
            "score": None,
            "verdict": "无图像",
            "confidence": "",
            "evidence": [],
            "message": "未收到图像字节",
        })

    mime = _guess_mime(filename, mimetype)
    manifest_envelope, err = _read_manifest(image_bytes, mime)

    if err == "no_manifest":
        return _finish({
            "status": "success",
            "score": 0.5,
            "verdict": "无 C2PA 内容凭证",
            "confidence": "低",
            "evidence": [
                "图像未携带 C2PA 内容凭证（Content Credentials）。",
                "未携带凭证不能证明真伪，仅作为辅助信号。",
            ],
            "message": "未发现内容凭证",
        })

    if manifest_envelope is None:
        return _finish({
            "status": "failed",
            "score": None,
            "verdict": "C2PA 读取失败",
            "confidence": "",
            "evidence": [],
            "message": _truncate(err or "", 120),
        })

    chain = _walk_manifest_chain(manifest_envelope)
    if not chain:
        return _finish({
            "status": "success",
            "score": 0.5,
            "verdict": "C2PA 凭证为空",
            "confidence": "低",
            "evidence": ["读取到 C2PA 容器但未发现可用 manifest。"],
            "message": "empty_manifests",
        })

    active_entry = chain[0]
    active_manifest = active_entry["manifest"]
    ingredient_entries = [entry for entry in chain[1:] if entry["role"] == "ingredient"]

    generators = _collect_generators(active_manifest)
    digital_source_types = _collect_digital_source_types(active_manifest)
    validation_severity, validation_issues = _validation_summary(manifest_envelope)
    signer_info = _collect_signer_info(active_manifest)
    actions = _collect_actions(active_manifest)

    chain_sources = [_summarize_source(entry["manifest"]) for entry in chain]
    chain_conflict = (
        "ai" in chain_sources
        and "camera" in chain_sources
        and len(chain) >= 2
    )

    score, verdict, confidence, evidence = _decide_verdict(
        generators,
        digital_source_types,
        validation_severity,
        validation_issues,
    )

    if chain_conflict:
        evidence.append(
            f"C2PA 来源链存在冲突：链上同时出现 AI 生成与相机捕获声明（共 {len(chain)} 段 manifest）。"
        )
        score = max(score, 0.7)
        verdict = f"{verdict}（来源链冲突）"
    if ingredient_entries:
        evidence.append(
            f"C2PA ingredient 链长度 {len(ingredient_entries)}，根 manifest 已遍历。"
        )
    if signer_info.get("issuer"):
        evidence.append(f"C2PA 签发方：{_truncate(str(signer_info['issuer']), 60)}")
    if actions:
        first_action = actions[0].get("action") or ""
        last_action = actions[-1].get("action") or ""
        if first_action or last_action:
            evidence.append(
                f"C2PA 动作链：{_truncate(first_action, 40) or '?'} → {_truncate(last_action, 40) or '?'}（共 {len(actions)} 步）"
            )

    details = {
        "chain_length": len(chain),
        "chain_sources": chain_sources,
        "chain_conflict": chain_conflict,
        "ingredient_count": len(ingredient_entries),
        "actions": actions[:6],
        "signer": signer_info,
        "generators": generators[:4],
        "digital_source_types": digital_source_types[:4],
        "validation_severity": validation_severity,
        "validation_issues": validation_issues[:3],
    }

    message_parts: List[str] = []
    if generators:
        message_parts.append(f"signer={_truncate(generators[0], 40)}")
    if digital_source_types:
        message_parts.append(f"dst={_truncate(digital_source_types[0], 40)}")
    if validation_severity != "ok":
        message_parts.append(f"validation={validation_severity}")
    if chain_conflict:
        message_parts.append("chain_conflict=true")

    return _finish({
        "status": "success",
        "score": round(score, 4),
        "verdict": verdict,
        "provenance_kind": "c2pa",
        "details": details,
        "confidence": confidence,
        "evidence": evidence[:5],
        "message": " | ".join(message_parts) or "已读取 C2PA 凭证",
    })

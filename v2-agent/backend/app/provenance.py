"""内容凭证验证（对标 OpenAI Verify 的 C2PA 部分）。

读取图片内嵌的 C2PA 内容凭证（带密码学签名的来源/编辑记录），提取生成器、签发者、
签名校验状态、编辑历史，以及是否声明为 AI 生成（IPTC digitalSourceType）。

SynthID 为 Google 专有隐形水印，无公开解码器，无法自行实现，仅如实标注不支持。
"""
from __future__ import annotations

import io
import json

from . import capture_evidence
from . import metadata as metadata_reader

try:
    from c2pa import C2paError, Reader
except Exception:
    C2paError = None
    Reader = None

EXT_TO_MIME = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "webp": "image/webp", "gif": "image/gif", "tif": "image/tiff", "tiff": "image/tiff",
    "heic": "image/heic", "heif": "image/heif",
    "mp4": "video/mp4", "mov": "video/quicktime", "avif": "image/avif",
}

# IPTC digitalSourceType → 是否 AI 生成。胶片、相纸与打印件描述的是
# 介质/再数字化来源，不能等同于当前文件由数字相机直接捕获。
AI_SOURCE_TYPES = frozenset({
    "trainedalgorithmicmedia",
    "compositewithtrainedalgorithmicmedia",
    "algorithmicmedia",
})
DIRECT_CAMERA_SOURCE_TYPES = frozenset({"digitalcapture"})

SYNTHID_NOTE = "SynthID 为 Google 专有隐形水印，无公开解码器，需 Google 授权，暂不支持检测。"


def _is_trusted_validation_state(value: object) -> bool:
    """C2PA `Valid` verifies structure/signature; only `Trusted` verifies the trust chain."""
    return str(value or "").strip().lower() == "trusted"


def mime_for(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return EXT_TO_MIME.get(ext, "image/jpeg")


def _classify_source(dst: str | None) -> bool | None:
    if not dst:
        return None
    token = str(dst).strip().rstrip("/").rsplit("/", 1)[-1].rsplit("#", 1)[-1].lower()
    if token in AI_SOURCE_TYPES:
        return True
    if token in DIRECT_CAMERA_SOURCE_TYPES:
        return False
    return None


def _object(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _array(value: object, label: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def _parse_credentials(reader: object, capture: dict | None) -> dict:
    """Parse into an isolated object so partial credentials are never published."""
    doc = _object(json.loads(reader.json()), "C2PA document")
    validation_state = str(reader.get_validation_state()).split(".")[-1]
    parsed = {
        "hasCredentials": True,
        "validationState": validation_state,
        "credentialTrusted": _is_trusted_validation_state(validation_state),
        "generator": None,
        "issuer": None,
        "signatureAlg": None,
        "signedTime": None,
        "isAiGenerated": None,
        "actions": [],
        "ingredients": [],
        "captureEvidence": capture,
    }

    manifests = _object(doc.get("manifests") or {}, "C2PA manifests")
    active = doc.get("active_manifest")
    am = _object(manifests.get(active, {}) if active else {}, "C2PA active manifest")

    generators = _array(am.get("claim_generator_info") or [], "C2PA claim_generator_info")
    if generators:
        generator = _object(generators[0], "C2PA claim generator")
        parsed["generator"] = f'{generator.get("name", "?")} {generator.get("version", "")}'.strip()
    else:
        parsed["generator"] = am.get("claim_generator")

    signature = _object(am.get("signature_info") or {}, "C2PA signature_info")
    parsed["issuer"] = signature.get("issuer")
    parsed["signatureAlg"] = signature.get("alg")
    parsed["signedTime"] = signature.get("time")

    ai_flag = None
    assertions = _array(am.get("assertions") or [], "C2PA assertions")
    for raw_assertion in assertions:
        assertion = _object(raw_assertion, "C2PA assertion")
        if not str(assertion.get("label", "")).startswith("c2pa.action"):
            continue
        assertion_data = _object(assertion.get("data") or {}, "C2PA action assertion data")
        actions = _array(assertion_data.get("actions") or [], "C2PA actions")
        for raw_action in actions:
            action = _object(raw_action, "C2PA action")
            digital_source_type = action.get("digitalSourceType")
            parsed["actions"].append({
                "action": action.get("action"),
                "softwareAgent": action.get("softwareAgent"),
                "digitalSourceType": digital_source_type,
            })
            flag = _classify_source(digital_source_type)
            if flag is not None:
                ai_flag = flag if ai_flag is None else (ai_flag or flag)
    parsed["isAiGenerated"] = ai_flag

    ingredients = _array(am.get("ingredients") or [], "C2PA ingredients")
    for raw_ingredient in ingredients:
        ingredient = _object(raw_ingredient, "C2PA ingredient")
        parsed["ingredients"].append({
            "title": ingredient.get("title"),
            "relationship": ingredient.get("relationship"),
        })

    source_types = {
        str(item.get("digitalSourceType") or "")
        for item in parsed["actions"]
    }
    has_camera_declaration = any(_classify_source(value) is False for value in source_types)
    if (
        parsed["isAiGenerated"] is False
        and parsed["credentialTrusted"] is True
        and has_camera_declaration
    ):
        parsed["captureEvidence"] = capture_evidence.add_verified_camera_credential(
            parsed.get("captureEvidence"),
            issuer=str(parsed.get("issuer") or parsed.get("generator") or ""),
        )
    return parsed


def read_provenance(data: bytes, mime: str, filename: str = "") -> dict:
    metadata_report = metadata_reader.inspect_metadata(data, filename=filename, mime=mime)
    ai_metadata = metadata_report.get("aiDetection") or {}
    report: dict = {
        "hasCredentials": False,
        "validationState": None,
        "credentialTrusted": False,
        "generator": None,
        "issuer": None,
        "signatureAlg": None,
        "signedTime": None,
        "isAiGenerated": None,
        "actions": [],
        "ingredients": [],
        "metadataAiGenerated": bool(ai_metadata.get("isAiLikely")),
        "aiMetadata": ai_metadata,
        "metadata": metadata_report.get("metadata"),
        "metadataSummary": metadata_report.get("metadataSummary"),
        "captureEvidence": metadata_report.get("captureEvidence"),
        "synthid": {"supported": False, "detected": None, "note": SYNTHID_NOTE},
        "error": None,
    }

    if Reader is None:
        report["error"] = "c2pa_unavailable"
        return report

    try:
        reader = Reader(mime, io.BytesIO(data))
    except Exception as exc:
        if C2paError is not None and isinstance(exc, C2paError.ManifestNotFound):
            report["error"] = "no_manifest"
        elif C2paError is not None and isinstance(exc, C2paError.NotSupported):
            report["error"] = "unsupported_format"
        else:
            report["error"] = f"c2pa_read_error:{type(exc).__name__}"
        return report

    try:
        parsed = _parse_credentials(reader, report.get("captureEvidence"))
        report.update(parsed)
    except Exception as e:
        report.update({
            "hasCredentials": False,
            "validationState": None,
            "credentialTrusted": False,
            "generator": None,
            "issuer": None,
            "signatureAlg": None,
            "signedTime": None,
            "isAiGenerated": None,
            "actions": [],
            "ingredients": [],
        })
        report["error"] = f"parse_error: {e}"
    finally:
        try:
            reader.close()
        except Exception:
            pass

    return report

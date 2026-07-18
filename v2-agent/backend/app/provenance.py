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
    from c2pa import Reader
except Exception:
    Reader = None

EXT_TO_MIME = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "webp": "image/webp", "gif": "image/gif", "tif": "image/tiff", "tiff": "image/tiff",
    "mp4": "video/mp4", "mov": "video/quicktime", "avif": "image/avif",
}

# IPTC digitalSourceType → 是否 AI 生成
AI_SOURCE_TYPES = ("trainedAlgorithmicMedia", "compositeWithTrainedAlgorithmicMedia", "algorithmicMedia")
CAMERA_SOURCE_TYPES = ("digitalCapture", "negativeFilm", "positiveFilm", "print")

SYNTHID_NOTE = "SynthID 为 Google 专有隐形水印，无公开解码器，需 Google 授权，暂不支持检测。"


def mime_for(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return EXT_TO_MIME.get(ext, "image/jpeg")


def _classify_source(dst: str | None) -> bool | None:
    if not dst:
        return None
    if any(t in dst for t in AI_SOURCE_TYPES):
        return True
    if any(t in dst for t in CAMERA_SOURCE_TYPES):
        return False
    return None


def read_provenance(data: bytes, mime: str, filename: str = "") -> dict:
    metadata_report = metadata_reader.inspect_metadata(data, filename=filename, mime=mime)
    ai_metadata = metadata_report.get("aiDetection") or {}
    report: dict = {
        "hasCredentials": False,
        "validationState": None,
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
    except Exception:
        # 没有内嵌凭证（最常见情形）或格式不支持
        report["error"] = "no_manifest"
        return report

    try:
        doc = json.loads(reader.json())
        report["hasCredentials"] = True
        report["validationState"] = str(reader.get_validation_state()).split(".")[-1]

        active = doc.get("active_manifest")
        am = doc.get("manifests", {}).get(active, {}) if active else {}

        gi = am.get("claim_generator_info") or []
        if gi:
            g = gi[0]
            report["generator"] = f'{g.get("name", "?")} {g.get("version", "")}'.strip()
        else:
            report["generator"] = am.get("claim_generator")

        sig = am.get("signature_info", {}) or {}
        report["issuer"] = sig.get("issuer")
        report["signatureAlg"] = sig.get("alg")
        report["signedTime"] = sig.get("time")

        ai_flag = None
        for asr in am.get("assertions", []) or []:
            if str(asr.get("label", "")).startswith("c2pa.action"):
                for a in asr.get("data", {}).get("actions", []):
                    dst = a.get("digitalSourceType")
                    report["actions"].append({
                        "action": a.get("action"),
                        "softwareAgent": a.get("softwareAgent"),
                        "digitalSourceType": dst,
                    })
                    flag = _classify_source(dst)
                    if flag is not None:
                        ai_flag = flag if ai_flag is None else (ai_flag or flag)
        report["isAiGenerated"] = ai_flag

        for ing in am.get("ingredients", []) or []:
            report["ingredients"].append({
                "title": ing.get("title"),
                "relationship": ing.get("relationship"),
            })
        source_types = {
            str(item.get("digitalSourceType") or "")
            for item in report.get("actions") or []
            if isinstance(item, dict)
        }
        validation_state = str(report.get("validationState") or "").lower()
        has_camera_declaration = any(any(kind in value for kind in CAMERA_SOURCE_TYPES) for value in source_types)
        if (
            report.get("hasCredentials")
            and report.get("isAiGenerated") is False
            and validation_state in {"valid", "trusted"}
            and has_camera_declaration
        ):
            report["captureEvidence"] = capture_evidence.add_verified_camera_credential(
                report.get("captureEvidence"),
                issuer=str(report.get("issuer") or report.get("generator") or ""),
            )
    except Exception as e:
        report["error"] = f"parse_error: {e}"
    finally:
        try:
            reader.close()
        except Exception:
            pass

    return report

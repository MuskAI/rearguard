from __future__ import annotations

# The V1 and V2 services deploy independently, so this small evidence model is
# vendored into each runtime. Keep its public schema compatible with V1.
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Iterable


MODEL_VERSION = "huijian-capture-evidence-v2"
LEVEL_TEXT = {"strong": "强", "medium": "中等", "weak": "弱", "none": "无", "conflict": "存在冲突"}


def _normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _text(value: Any, limit: int = 120) -> str:
    rendered = re.sub(r"\s+", " ", str(value or "")).strip()
    if not rendered or rendered.lower() in {"none", "null", "unknown", "n/a", "-"}:
        return ""
    return rendered if len(rendered) <= limit else f"{rendered[:limit]}..."


def _flatten(value: Any, prefix: str = "", rows=None, depth: int = 0):
    rows = [] if rows is None else rows
    if depth > 10 or len(rows) >= 2500:
        return rows
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(item, (dict, list, tuple)) and item:
                rows.append((path, "[present]"))
            _flatten(item, path, rows, depth + 1)
        return rows
    if isinstance(value, (list, tuple)):
        if value and all(not isinstance(item, (dict, list, tuple)) for item in value):
            if len(value) == 2 and all(isinstance(item, (int, float)) for item in value):
                rows.append((prefix, f"{value[0]}/{value[1]}"))
            else:
                rendered = ", ".join(_text(item, 80) for item in value if _text(item, 80))
                if rendered:
                    rows.append((prefix, rendered))
            return rows
        for index, item in enumerate(value[:200]):
            _flatten(item, f"{prefix}[{index}]", rows, depth + 1)
        return rows
    rendered = _text(value, 500)
    if rendered:
        rows.append((prefix, rendered))
    return rows


def _field(rows, *aliases: str) -> str:
    normalized_aliases = sorted({_normalize(alias) for alias in aliases if alias}, key=len, reverse=True)
    for alias in normalized_aliases:
        for path, value in rows:
            normalized_path = _normalize(path)
            if (normalized_path == alias or normalized_path.endswith(alias)) and value != "[present]":
                return _text(value)
    return ""


def _has_field(rows, *aliases: str) -> bool:
    normalized_aliases = {_normalize(alias) for alias in aliases if alias}
    return any(any(_normalize(path) == alias or _normalize(path).endswith(alias) for alias in normalized_aliases) for path, _ in rows)


def _number(value: str) -> float | None:
    fraction = re.search(r"(-?\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)", value or "")
    if fraction:
        denominator = float(fraction.group(2))
        return float(fraction.group(1)) / denominator if denominator else None
    match = re.search(r"-?\d+(?:\.\d+)?", value or "")
    return float(match.group(0)) if match else None


def _decimal(value: str) -> str:
    number = _number(value)
    if number is None:
        return _text(value)
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _parse_datetime(value: str) -> datetime | None:
    cleaned = str(value or "").strip().replace("Z", "+00:00")
    for candidate in (cleaned, re.sub(r"^(\d{4}):(\d{2}):(\d{2})", r"\1-\2-\3", cleaned)):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _item(key: str, label: str, value: str, strength: str = "medium") -> dict[str, str]:
    return {"key": key, "label": label, "value": _text(value, 160), "strength": strength}


def analyze_capture_evidence(metadata: dict[str, Any] | None, *, ai_markers: Iterable[Any] | None = None, now: datetime | None = None) -> dict[str, Any]:
    rows = _flatten(metadata if isinstance(metadata, dict) else {})
    marker_texts = [_text(value, 180) for value in (ai_markers or []) if _text(value, 180)]
    make = _field(rows, "EXIF:Make", "EXIF_Make", "TIFF:Make", "cameraMake")
    model = _field(rows, "EXIF:Model", "EXIF_Model", "TIFF:Model", "cameraModel")
    lens = _field(rows, "EXIF:LensModel", "EXIF_LensModel", "LensInfo", "LensSpec", "lensModel")
    exposure = _field(rows, "EXIF:ExposureTime", "ExposureTime", "ShutterSpeedValue")
    aperture = _field(rows, "EXIF:FNumber", "FNumber", "ApertureValue")
    iso = _field(rows, "EXIF:ISO", "PhotographicSensitivity", "ISOSpeedRatings", "ISO")
    focal = _field(rows, "EXIF:FocalLength", "FocalLength", "FocalLengthIn35mmFormat")
    captured_at = _field(rows, "EXIF:DateTimeOriginal", "DateTimeOriginal", "CreateDate", "DateCreated")
    offset_time = _field(rows, "OffsetTimeOriginal", "OffsetTime", "TimeZoneOffset")
    software = _field(rows, "EXIF:Software", "XMP:CreatorTool", "ProcessingSoftware", "Software", "CreatorTool")
    serial = _field(rows, "BodySerialNumber", "CameraSerialNumber", "SerialNumber", "LensSerialNumber")
    has_maker_note = _has_field(rows, "MakerNote", "MakerNotes", "Makernotes")
    has_gps = _has_field(rows, "GPSInfo", "GPSPosition", "GPSLatitude", "GPSLongitude", "GPSDateStamp")
    has_thumbnail = _has_field(rows, "ThumbnailImage", "JPEGInterchangeFormat", "PreviewImage")
    native_fields = sum(1 for aliases in (("ExifVersion",), ("FlashpixVersion",), ("SensingMethod",), ("SceneType",), ("ExposureMode",), ("MeteringMode",), ("WhiteBalance",), ("ColorSpace",)) if _has_field(rows, *aliases))

    evidence = []
    conflicts = []
    limitations = ["普通 EXIF 可以被修改或复制，因此不能单独证明图片真实。"]
    groups = []
    points = 0.0
    device = f"{make} {model}".strip() if make and make.lower() not in model.lower() else model or make
    if device:
        evidence.append(_item("device", "拍摄设备", device, "medium" if make and model else "weak"))
        groups.append("device")
        points += 2.2 if make and model else 1.3
    if lens:
        evidence.append(_item("lens", "镜头信息", lens))
        groups.append("optics")
        points += 1.0
    parameters = [value for value in (exposure, aperture, iso, focal) if value]
    if parameters:
        display = []
        if exposure:
            display.append(exposure if "s" in exposure.lower() else f"{exposure}s")
        if aperture:
            display.append(aperture if aperture.lower().startswith("f/") else f"f/{_decimal(aperture)}")
        if iso:
            display.append(iso if iso.lower().startswith("iso") else f"ISO {iso}")
        if focal:
            display.append(focal if "mm" in focal.lower() else f"{_decimal(focal)}mm")
        evidence.append(_item("exposure", "拍摄参数", " · ".join(display), "medium" if len(parameters) >= 3 else "weak"))
        groups.append("exposure")
        points += 2.0 if len(parameters) >= 3 else 1.35 if len(parameters) >= 2 else 0.65
        invalid = [label for label, value in (("快门", exposure), ("光圈", aperture), ("ISO", iso), ("焦距", focal)) if value and _number(value) is not None and _number(value) <= 0]
        if invalid:
            conflicts.append(_item("invalid_exposure", "参数异常", f"{'、'.join(invalid)}数值不符合常规拍摄参数"))
    if captured_at:
        evidence.append(_item("capture_time", "原始拍摄时间", "已记录 DateTimeOriginal（精确时间已隐藏）"))
        groups.append("capture_time")
        points += 1.2
        parsed = _parse_datetime(captured_at)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        if parsed and parsed > current + timedelta(days=2):
            conflicts.append(_item("future_time", "时间冲突", "原始拍摄时间明显晚于当前时间"))
    if offset_time:
        evidence.append(_item("timezone", "时区信息", "拍摄时区字段存在", "weak"))
        points += 0.35
    if has_maker_note:
        evidence.append(_item("maker_note", "设备私有字段", "检测到 MakerNote 相机私有信息（内容已隐藏）"))
        groups.append("maker_note")
        points += 1.4
    if serial:
        evidence.append(_item("serial", "设备标识", "检测到机身或镜头序列字段（值已隐藏）"))
        groups.append("device_serial")
        points += 0.8
    if has_gps:
        evidence.append(_item("gps", "位置与时间", "检测到 GPS 拍摄字段（精确位置已隐藏）", "weak"))
        groups.append("gps")
        points += 0.55
    if has_thumbnail:
        evidence.append(_item("thumbnail", "内嵌预览", "文件包含相机内嵌缩略图或预览记录", "weak"))
        groups.append("embedded_preview")
        points += 0.45
    if native_fields >= 3:
        evidence.append(_item("native_tags", "相机原生字段", f"检测到 {native_fields} 类曝光/色彩/场景原生字段", "weak"))
        groups.append("native_tags")
        points += 0.75
    if software:
        limitations.append(f"文件记录了处理软件“{_text(software, 70)}”；这表示可能经过导出或后期处理，不等同于 AI 生成。")
    if marker_texts:
        conflicts.append(_item("ai_declaration", "生成声明冲突", "元数据中存在明确的 AI 生成工具、参数或工作流声明", "strong"))

    native_support_groups = {
        "optics",
        "maker_note",
        "device_serial",
        "gps",
        "embedded_preview",
        "native_tags",
    }.intersection(groups)
    rich_native_chain = (
        bool(make and model)
        and len(parameters) >= 3
        and bool(captured_at)
        and len(native_support_groups) >= 2
        and points >= 6.5
    )

    if conflicts:
        level, supports, title, summary, ratio = "conflict", False, "拍摄元数据存在冲突", "读取到拍摄字段，但其中存在生成声明、时间或参数冲突，不能作为实拍支持证据。", 1.0
        profile = "conflicted"
    elif rich_native_chain:
        level, supports, title, summary, ratio = "medium", True, "发现丰富且一致的原生拍摄链", "设备、光学参数、原始时间与相机私有或原生字段相互支持，可作为真实拍摄的较强辅助证据。", 0.45
        profile = "native_capture_chain"
    elif device and len(parameters) >= 2 and captured_at and points >= 5:
        level, supports, title, summary, ratio = "medium", True, "发现一致的相机拍摄链路", "设备、拍摄参数与原始时间相互支持，可作为真实拍摄的中等强度辅助证据。", 0.65
        profile = "coherent_exif"
    elif device and (captured_at or parameters) and points >= 2.4:
        level, supports, title, summary, ratio = "weak", True, "发现部分拍摄链路线索", "读取到部分设备或拍摄参数，但链路不完整，仅提供弱支持。", 0.84
        profile = "partial_exif"
    else:
        level, supports, title, summary, ratio = "none", False, "未形成可用的实拍证据", "没有读取到足够完整且相互一致的相机拍摄字段；元数据缺失保持中性。", 1.0
        profile = "none"
    return {
        "version": MODEL_VERSION,
        "level": level,
        "levelText": LEVEL_TEXT[level],
        "profile": profile,
        "supportsRealCapture": supports,
        "score": round(min(points / 8.0, 1.0), 3),
        "likelihoodRatio": ratio,
        "title": title,
        "summary": summary,
        "evidence": evidence[:8],
        "conflicts": conflicts[:4],
        "limitations": limitations[:3],
        "groups": list(dict.fromkeys(groups)),
        "nativeSupportCount": len(native_support_groups),
        "adjustmentEligible": bool(rich_native_chain and not conflicts),
        "fieldCount": len(rows),
        "privacy": {"gpsRedacted": has_gps, "serialRedacted": bool(serial), "captureTimeRedacted": bool(captured_at)},
    }


def add_verified_camera_credential(capture_evidence: dict[str, Any] | None, *, issuer: str = "") -> dict[str, Any]:
    result = deepcopy(capture_evidence or analyze_capture_evidence({}))
    if result.get("conflicts"):
        return result
    value = "C2PA 签名有效，来源声明为相机捕获"
    if issuer:
        value += f"（签发方：{_text(issuer, 60)}）"
    result["evidence"] = [_item("c2pa_camera", "可信内容凭证", value, "strong"), *(result.get("evidence") or [])][:8]
    result["groups"] = list(dict.fromkeys(["signed_camera_capture", *(result.get("groups") or [])]))
    result.update({
        "level": "strong", "levelText": LEVEL_TEXT["strong"], "supportsRealCapture": True,
        "profile": "verified_camera_credential", "adjustmentEligible": True,
        "score": max(float(result.get("score") or 0), 0.96), "likelihoodRatio": 0.08,
        "title": "内容凭证确认相机捕获",
        "summary": "通过校验的 C2PA 来源凭证声明该文件由相机捕获，构成强实拍来源证据。",
    })
    return result

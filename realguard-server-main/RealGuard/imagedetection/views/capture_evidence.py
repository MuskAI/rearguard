from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


MODEL_VERSION = "huijian-capture-evidence-v1"
LEVEL_TEXT = {
    "strong": "强",
    "medium": "中等",
    "weak": "弱",
    "none": "无",
    "conflict": "存在冲突",
}


def _normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _text(value: Any, limit: int = 120) -> str:
    rendered = re.sub(r"\s+", " ", str(value or "")).strip()
    if not rendered or rendered.lower() in {"none", "null", "unknown", "n/a", "-"}:
        return ""
    return rendered if len(rendered) <= limit else f"{rendered[:limit]}..."


def _flatten(value: Any, prefix: str = "", rows: list[tuple[str, str]] | None = None, depth: int = 0):
    if rows is None:
        rows = []
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


def _field(rows: list[tuple[str, str]], *aliases: str) -> str:
    normalized_aliases = tuple(sorted({_normalize(alias) for alias in aliases if alias}, key=len, reverse=True))
    for alias in normalized_aliases:
        for path, value in rows:
            normalized_path = _normalize(path)
            if normalized_path == alias or normalized_path.endswith(alias):
                if value != "[present]":
                    return _text(value)
    return ""


def _has_field(rows: list[tuple[str, str]], *aliases: str) -> bool:
    normalized_aliases = tuple({_normalize(alias) for alias in aliases if alias})
    return any(
        any(_normalize(path) == alias or _normalize(path).endswith(alias) for alias in normalized_aliases)
        for path, _ in rows
    )


def _number(value: str) -> float | None:
    if not value:
        return None
    fraction = re.search(r"(-?\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)", value)
    if fraction:
        denominator = float(fraction.group(2))
        return float(fraction.group(1)) / denominator if denominator else None
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    return float(match.group(0)) if match else None


def _decimal(value: str) -> str:
    number = _number(value)
    if number is None:
        return _text(value)
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    cleaned = value.strip().replace("Z", "+00:00")
    candidates = (
        cleaned,
        re.sub(r"^(\d{4}):(\d{2}):(\d{2})", r"\1-\2-\3", cleaned),
    )
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    for pattern in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned[:19], pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _display_device(make: str, model: str) -> str:
    if make and model and make.lower() not in model.lower():
        return _text(f"{make} {model}", 90)
    return _text(model or make, 90)


def _display_exposure(exposure: str, aperture: str, iso: str, focal: str) -> str:
    parts = []
    if exposure:
        parts.append(exposure if any(unit in exposure.lower() for unit in ("s", "sec", "秒")) else f"{exposure}s")
    if aperture:
        parts.append(aperture if aperture.lower().startswith("f/") else f"f/{_decimal(aperture)}")
    if iso:
        parts.append(iso if iso.lower().startswith("iso") else f"ISO {iso}")
    if focal:
        parts.append(focal if "mm" in focal.lower() else f"{_decimal(focal)}mm")
    return " · ".join(parts[:4])


def _item(key: str, label: str, value: str, strength: str = "medium") -> dict[str, str]:
    return {"key": key, "label": label, "value": _text(value, 160), "strength": strength}


def analyze_capture_evidence(
    metadata: dict[str, Any] | None,
    *,
    ai_markers: Iterable[Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    metadata = metadata if isinstance(metadata, dict) else {}
    rows = _flatten(metadata)
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
    native_fields = sum(
        1
        for aliases in (
            ("ExifVersion",),
            ("FlashpixVersion",),
            ("SensingMethod",),
            ("SceneType",),
            ("ExposureMode",),
            ("MeteringMode",),
            ("WhiteBalance",),
            ("ColorSpace",),
        )
        if _has_field(rows, *aliases)
    )

    evidence: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    limitations = ["普通 EXIF 可以被修改或复制，因此不能单独证明图片真实。"]
    groups: list[str] = []
    points = 0.0

    device = _display_device(make, model)
    if device:
        evidence.append(_item("device", "拍摄设备", device, "medium" if make and model else "weak"))
        groups.append("device")
        points += 2.2 if make and model else 1.3
    if lens:
        evidence.append(_item("lens", "镜头信息", lens, "medium"))
        groups.append("optics")
        points += 1.0

    parameters = [value for value in (exposure, aperture, iso, focal) if value]
    if parameters:
        evidence.append(_item("exposure", "拍摄参数", _display_exposure(exposure, aperture, iso, focal), "medium" if len(parameters) >= 3 else "weak"))
        groups.append("exposure")
        points += 2.0 if len(parameters) >= 3 else 1.35 if len(parameters) >= 2 else 0.65
        invalid_parameters = []
        for label, value in (("快门", exposure), ("光圈", aperture), ("ISO", iso), ("焦距", focal)):
            parsed = _number(value)
            if value and parsed is not None and parsed <= 0:
                invalid_parameters.append(label)
        if invalid_parameters:
            conflicts.append(_item("invalid_exposure", "参数异常", f"{'、'.join(invalid_parameters)}数值不符合常规拍摄参数", "medium"))

    if captured_at:
        evidence.append(_item("capture_time", "原始拍摄时间", "已记录 DateTimeOriginal（精确时间已隐藏）", "medium"))
        groups.append("capture_time")
        points += 1.2
        parsed_time = _parse_datetime(captured_at)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        if parsed_time and parsed_time > current + timedelta(days=2):
            conflicts.append(_item("future_time", "时间冲突", "原始拍摄时间明显晚于当前时间", "medium"))
    if offset_time:
        evidence.append(_item("timezone", "时区信息", "拍摄时区字段存在", "weak"))
        points += 0.35

    if has_maker_note:
        evidence.append(_item("maker_note", "设备私有字段", "检测到 MakerNote 相机私有信息（内容已隐藏）", "medium"))
        groups.append("maker_note")
        points += 1.4
    if serial:
        evidence.append(_item("serial", "设备标识", "检测到机身或镜头序列字段（值已隐藏）", "medium"))
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

    unique_groups = list(dict.fromkeys(groups))
    score = round(min(points / 8.0, 1.0), 3)
    has_device = bool(device)
    complete_parameters = len(parameters) >= 2
    if conflicts:
        level = "conflict"
        supports_real = False
        title = "拍摄元数据存在冲突"
        summary = "读取到拍摄流程字段，但其中存在生成声明、时间或参数冲突，不能确认图片来源。"
        likelihood_ratio = 1.0
    elif has_device and complete_parameters and bool(captured_at) and points >= 5.0:
        level = "medium"
        supports_real = True
        title = "拍摄流程元数据较一致"
        summary = "设备、拍摄参数与原始时间相互一致，可作为相机工作流的辅助线索，但不能单独证明图片真实。"
        likelihood_ratio = 0.65
    elif has_device and (bool(captured_at) or bool(parameters)) and points >= 2.4:
        level = "weak"
        supports_real = True
        title = "发现部分拍摄流程线索"
        summary = "读取到部分设备或拍摄参数，但信息不完整且可以被编辑，仅作弱辅助线索。"
        likelihood_ratio = 0.84
    else:
        level = "none"
        supports_real = False
        title = "未形成可用的拍摄流程线索"
        summary = "没有读取到足够完整且相互一致的相机拍摄字段；元数据缺失保持中性。"
        likelihood_ratio = 1.0

    return {
        "version": MODEL_VERSION,
        "level": level,
        "levelText": LEVEL_TEXT[level],
        "supportsRealCapture": supports_real,
        "score": score,
        "likelihoodRatio": likelihood_ratio,
        "title": title,
        "summary": summary,
        "evidence": evidence[:8],
        "conflicts": conflicts[:4],
        "limitations": limitations[:3],
        "groups": unique_groups,
        "fieldCount": len(rows),
        "privacy": {
            "gpsRedacted": has_gps,
            "serialRedacted": bool(serial),
            "captureTimeRedacted": bool(captured_at),
        },
    }


def add_verified_camera_credential(
    capture_evidence: dict[str, Any] | None,
    *,
    issuer: str = "",
) -> dict[str, Any]:
    result = copy.deepcopy(capture_evidence or analyze_capture_evidence({}))
    if result.get("conflicts"):
        return result
    credential_value = "C2PA 签名有效，来源声明为相机捕获"
    if issuer:
        credential_value += f"（签发方：{_text(issuer, 60)}）"
    credential = _item("c2pa_camera", "可信内容凭证", credential_value, "strong")
    result["evidence"] = [credential, *(result.get("evidence") or [])][:8]
    result["groups"] = list(dict.fromkeys(["signed_camera_capture", *(result.get("groups") or [])]))
    result.update({
        "level": "strong",
        "levelText": LEVEL_TEXT["strong"],
        "supportsRealCapture": True,
        "score": max(float(result.get("score") or 0.0), 0.96),
        "likelihoodRatio": 0.08,
        "title": "内容凭证确认相机捕获",
        "summary": "通过校验的 C2PA 来源凭证声明该文件由相机捕获，构成强实拍来源证据。",
    })
    return result

def _count_capture_related_fields(meta: dict) -> int:
    """统计与真实相机拍摄相关的元数据字段数量（含 Composite 派生项）。"""
    if not meta:
        return 0
    keys = (
        "EXIF:ExposureTime", "EXIF:FNumber", "EXIF:ISO", "EXIF:FocalLength",
        "EXIF:LensModel", "EXIF:LensInfo", "EXIF:Flash", "EXIF:WhiteBalance",
        "Composite:ShutterSpeed", "Composite:ShutterSpeedValue",
        "Composite:Aperture", "Composite:ApertureValue",
        "Composite:ISO", "Composite:LensID", "Composite:LensSpec",
        "Composite:FocalLength", "Composite:FocalLength35efl",
        "Composite:ExposureTime", "Composite:LightValue",
    )
    n = 0
    for k in keys:
        v = meta.get(k)
        if v is None or v == "" or v == 0:
            continue
        n += 1
    return n


def _count_core_real_fields(meta: dict) -> int:
    """
    统计“强真实”核心字段数量（镜头+曝光+时间+厂商信息）。
    """
    if not meta:
        return 0
    keys = (
        "EXIF:Make", "EXIF:Model", "EXIF:LensModel", "EXIF:LensInfo",
        "EXIF:ExposureTime", "EXIF:FNumber", "EXIF:ISO", "EXIF:FocalLength",
        "EXIF:DateTimeOriginal", "EXIF:CreateDate", "EXIF:GPSTimeStamp",
        "Composite:ShutterSpeed", "Composite:Aperture", "Composite:LensID",
    )
    c = 0
    for k in keys:
        v = meta.get(k)
        if v is None or v == "":
            continue
        c += 1
    return c


def summarize_evidence(evidence: dict) -> dict:
    """
    对原始证据进行结构化汇总，生成供推理Agent使用的标准摘要。

    权重规则：
        预训练模型 = 视觉推理 > 元数据（有强证据除外）

    强元数据信号定义（可覆盖检测器权重）：
        - 强AI信号：EXIF:UserComment 含生成提示词、JUMBF 块存在
        - 强真实信号：完整相机三件套（Make + Model + GPS 或 LensModel）同时存在
    """
    prob_fake = evidence.get("detector_probability", 0.5)
    metadata_signals = evidence.get("metadata_signals", {})
    metadata_raw = evidence.get("metadata_raw", {})

    # ===== 检测模型置信度分级 =====
    detector_label = "AI生成" if prob_fake >= 0.5 else "真实图像"
    if prob_fake >= 0.85 or prob_fake <= 0.15:
        detector_confidence_level = "高"
    elif prob_fake >= 0.70 or prob_fake <= 0.30:
        detector_confidence_level = "中"
    else:
        detector_confidence_level = "低"

    # ===== 元数据信号 =====
    ai_metadata_signal  = metadata_signals.get("has_ai_signal", False)
    real_metadata_signal = metadata_signals.get("has_real_signal", False)
    ai_signal_details   = metadata_signals.get("ai_signals", [])
    real_signal_details = metadata_signals.get("real_signals", [])

    # ===== 强元数据信号识别 =====
    # 强AI信号：UserComment 含明显生成提示词 / JUMBF 块
    strong_ai_metadata = False
    strong_ai_reason = ""
    for sig in ai_signal_details:
        if "UserComment" in sig:
            val = sig.split("=", 1)[-1].strip()
            # 排除相机私有标记（如 oplus_xxx），只认明显提示词
            if len(val) > 20 and not val.startswith("oplus") and not val.startswith("ASCII"):
                strong_ai_metadata = True
                strong_ai_reason = f"UserComment 含生成提示词: {val[:60]}..."
    if any("JUMBF" in s for s in ai_signal_details):
        strong_ai_metadata = True
        strong_ai_reason = "存在 JUMBF 元数据块（AI内容标记）"

    # 强真实信号（以原始字典为准）：
    # A) 经典：厂商+型号+(GPS或镜头)
    # B) 设备信息可能被裁剪时：镜头/曝光/时间等核心参数较丰富
    # C) MakerNotes/相机软件/设备标记存在（厂商相机生态信号）
    strong_real_metadata = False
    strong_real_reason = ""
    has_make = bool(metadata_raw.get("EXIF:Make"))
    has_model = bool(metadata_raw.get("EXIF:Model"))
    has_gps = bool(
        metadata_raw.get("Composite:GPSPosition")
        or metadata_raw.get("EXIF:GPSLatitude")
    )
    has_lens = bool(
        metadata_raw.get("EXIF:LensModel")
        or metadata_raw.get("EXIF:LensInfo")
        or metadata_raw.get("Composite:LensID")
        or metadata_raw.get("Composite:LensSpec")
    )
    has_dt = bool(
        metadata_raw.get("EXIF:DateTimeOriginal")
        or metadata_raw.get("EXIF:CreateDate")
        or metadata_raw.get("EXIF:GPSTimeStamp")
    )
    capture_n = _count_capture_related_fields(metadata_raw)
    core_n = _count_core_real_fields(metadata_raw)
    has_maker_notes = any(k.startswith("MakerNotes:") for k in metadata_raw.keys())
    has_camera_software = bool(metadata_raw.get("EXIF:Software"))
    has_device_comment = bool(metadata_raw.get("EXIF:UserComment"))

    classic_real = has_make and has_model and (has_gps or has_lens)
    rich_capture = (
        (has_make or has_model or has_lens or has_device_comment)
        and (
            capture_n >= 4
            or (has_dt and capture_n >= 3)
            or core_n >= 5
        )
    )
    camera_ecology = (
        (has_lens and has_dt and core_n >= 4)
        or (has_maker_notes and core_n >= 3)
        or (has_camera_software and has_lens and capture_n >= 3)
    )

    if classic_real or rich_capture or camera_ecology:
        strong_real_metadata = True
        parts = []
        for label, key in (
            ("厂商", "EXIF:Make"),
            ("型号", "EXIF:Model"),
            ("镜头", "EXIF:LensModel"),
            ("GPS", "Composite:GPSPosition"),
            ("时间", "EXIF:DateTimeOriginal"),
        ):
            v = metadata_raw.get(key)
            if v:
                parts.append(f"{label}={v}")
        strong_real_reason = "真实拍摄元数据: " + " | ".join(parts[:5])
        if not parts:
            strong_real_reason = "真实拍摄元数据: 存在镜头/曝光/时间等核心参数组合"
        strong_real_reason += f"（core={core_n}, capture={capture_n}）"

    # ===== 证据一致性评估 =====
    detector_says_ai = prob_fake >= 0.5

    if strong_ai_metadata:
        evidence_consistency = f"强AI元数据信号（可提升权重）: {strong_ai_reason}"
    elif strong_real_metadata and not detector_says_ai:
        evidence_consistency = f"强真实元数据信号（与检测器一致）: {strong_real_reason}"
    elif strong_real_metadata and detector_says_ai:
        evidence_consistency = f"冲突：检测器→AI，但有强真实元数据: {strong_real_reason}"
    elif detector_says_ai and ai_metadata_signal and not real_metadata_signal:
        evidence_consistency = "一致：检测器与元数据均指向AI生成"
    elif not detector_says_ai and real_metadata_signal and not ai_metadata_signal:
        evidence_consistency = "一致：检测器与元数据均指向真实图像"
    elif not ai_metadata_signal and not real_metadata_signal:
        evidence_consistency = "元数据无信号，完全依赖检测器与视觉"
    else:
        evidence_consistency = "混合信号：同时存在AI与真实图像元数据"

    summary = {
        "detector_probability":      prob_fake,
        "detector_label":            detector_label,
        "detector_confidence_level": detector_confidence_level,
        "ai_metadata_signal":        ai_metadata_signal,
        "real_metadata_signal":      real_metadata_signal,
        "ai_signal_details":         ai_signal_details,
        "real_signal_details":       real_signal_details,
        "strong_ai_metadata":        strong_ai_metadata,
        "strong_real_metadata":      strong_real_metadata,
        "strong_ai_reason":          strong_ai_reason,
        "strong_real_reason":        strong_real_reason,
        "all_metadata":              metadata_raw,
        "evidence_consistency":      evidence_consistency,
        "metadata_field_count":      len(metadata_raw),
        "metadata_capture_fields":   capture_n,
    }

    return summary

import subprocess
import json
import shutil
import os
from PIL import Image


def analyze_image_metadata(image_path: str, verbose: bool = False):
    """
    提取并分析图像元数据，用于区分真实图像与AI生成图像。
    与论文 AIFo 框架中的元数据分析工具保持一致，采用双重过滤策略：
    1. KEY_FIELD_EXACT: 精确匹配关键字段（相机参数、GPS、时间等）
    2. KEY_FIELD_PREFIXES: 前缀匹配（MakerNotes、JUMBF、MPF等）
    
    返回值:
        dict: 过滤后的元数据字段，包含真实图像信号与AI图像信号。
    """

    # ===== 精确匹配字段（来自论文 Table 16）=====
    KEY_FIELD_EXACT = {
        # 软件/生成信息（AI信号）
        "XMP:CreatorTool",
        "EXIF:Software",
        "EXIF:UserComment",
        "EXIF:Artist",
        "XMP:Creator",
        "IPTC:By-line",
        "File:Comment",
        "XMP:Description",
        "XMP:Title",
        "XMP:Rights",
        "XMP:Source",

        # 相机物理参数（真实图像信号）
        "EXIF:Make",
        "EXIF:Model",
        "EXIF:LensModel",
        "EXIF:LensInfo",
        "EXIF:LensSerialNumber",
        "EXIF:ExposureTime",
        "EXIF:FNumber",
        "EXIF:ISO",
        "EXIF:FocalLength",
        "EXIF:SerialNumber",

        # 时间 / 地理（真实图像信号）
        "EXIF:GPSLatitude",
        "EXIF:GPSLongitude",
        "EXIF:GPSTimeStamp",
        "EXIF:DateTimeOriginal",
        "EXIF:CreateDate",
        "Composite:GPSPosition",

        # 复合光学参数（真实图像信号）
        "Composite:Aperture",
        "Composite:ShutterSpeed",
        "Composite:LensID",

        # ICC / IPTC 色彩与版权（辅助信号）
        "ICC_Profile:ProfileDescription",
        "ICC_Profile:ProfileCopyright",
        "IPTC:DocumentNotes",
        "IPTC:ApplicationRecordVersion"
    }

    # ===== 前缀匹配字段（来自论文 Table 16）=====
    KEY_FIELD_PREFIXES = [
        "MakerNotes:",   # 相机制造商私有数据（真实图像强信号）
        "JUMBF:",        # AI生成内容标记（AI信号）
        "MPF:",          # 多帧图像格式
        "AIGC:",         # AIGC 专用标记字段（AI信号）
    ]

    # 检查 exiftool 是否可用
    if shutil.which("exiftool") is None:
        raise EnvironmentError(
            "ExifTool 未找到，请先安装：apt install exiftool 或 conda install -c conda-forge exiftool"
        )

    # 调用 exiftool 提取完整元数据（JSON格式，含组名 -G）
    cmd = ["exiftool", "-j", "-G", "-a", image_path]
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(f"ExifTool 执行错误: {result.stderr}")

    raw_list = json.loads(result.stdout)
    if not raw_list:
        return {}

    raw_metadata = raw_list[0]

    # ===== 双重过滤策略 =====
    filtered = {}
    for key, value in raw_metadata.items():
        # 精确匹配
        if key in KEY_FIELD_EXACT:
            filtered[key] = value
            continue
        # 前缀匹配
        for prefix in KEY_FIELD_PREFIXES:
            if key.startswith(prefix):
                filtered[key] = value
                break

    # ===== Verbose 分析报告（与论文一致的信号分类）=====
    if verbose:
        print("\n--- 元数据分析报告 ---")

        real_signals = []
        ai_signals = []

        # 真实图像信号
        if "EXIF:Make" in filtered:
            real_signals.append(f"相机品牌 (Camera Make): {filtered['EXIF:Make']}")
        if "EXIF:Model" in filtered:
            real_signals.append(f"相机型号 (Camera Model): {filtered['EXIF:Model']}")
        if "EXIF:LensInfo" in filtered:
            real_signals.append(f"镜头信息 (Lens Info): {filtered['EXIF:LensInfo']}")
        if "EXIF:LensModel" in filtered:
            real_signals.append(f"镜头型号 (Lens Model): {filtered['EXIF:LensModel']}")
        if "Composite:GPSPosition" in filtered:
            real_signals.append(f"GPS位置 (GPS Position): {filtered['Composite:GPSPosition']}")
        if "EXIF:DateTimeOriginal" in filtered:
            real_signals.append(f"拍摄时间 (DateTime): {filtered['EXIF:DateTimeOriginal']}")
        if "EXIF:ExposureTime" in filtered:
            real_signals.append(f"曝光时间 (Exposure): {filtered['EXIF:ExposureTime']}")
        if "EXIF:FNumber" in filtered:
            real_signals.append(f"光圈值 (FNumber): {filtered['EXIF:FNumber']}")
        if "EXIF:ISO" in filtered:
            real_signals.append(f"ISO: {filtered['EXIF:ISO']}")
        if any(k.startswith("MakerNotes:") for k in filtered):
            real_signals.append("发现相机制造商私有数据 (MakerNotes Found)")

        # AI图像信号
        if "EXIF:UserComment" in filtered:
            ai_signals.append(f"用户注释 (UserComment): {filtered['EXIF:UserComment']}")
        if "XMP:CreatorTool" in filtered:
            ai_signals.append(f"创建工具 (CreatorTool): {filtered['XMP:CreatorTool']}")
        if "EXIF:Software" in filtered:
            ai_signals.append(f"软件标签 (Software): {filtered['EXIF:Software']}")
        if "EXIF:Artist" in filtered:
            ai_signals.append(f"作者字段 (Artist): {filtered['EXIF:Artist']}")
        if "XMP:Creator" in filtered:
            ai_signals.append(f"XMP创建者 (XMP:Creator): {filtered['XMP:Creator']}")
        if "IPTC:By-line" in filtered:
            ai_signals.append(f"IPTC作者 (By-line): {filtered['IPTC:By-line']}")
        if any(k.startswith("JUMBF:") for k in filtered):
            ai_signals.append("发现JUMBF元数据块 (JUMBF metadata，常见于AI内容标记)")
        if any(k.startswith("AIGC:") for k in filtered):
            aigc_vals = {k: filtered[k] for k in filtered if k.startswith("AIGC:")}
            ai_signals.append(f"发现AIGC专用标记字段: {aigc_vals}")

        print(f"\n[+] 真实图像指标数量: {len(real_signals)}")
        for s in real_signals:
            print(f"  - {s}")

        print(f"\n[!] AI/编辑图像指标数量: {len(ai_signals)}")
        for s in ai_signals:
            print(f"  - {s}")

        print(f"\n[总计提取字段数]: {len(filtered)}")

        # 图像物理信息
        image_info = _analyze_image_physical(image_path)
        if image_info:
            print(f"\n[图像物理信息]")
            print(f"  - 文件大小  : {image_info['file_size_str']}")
            print(f"  - 图像格式  : {image_info['format']}")
            print(f"  - 分辨率    : {image_info['width']} x {image_info['height']} 像素")
            print(f"  - 清晰度等级: {image_info['clarity_level']}（{image_info['clarity_desc']}）")

    return filtered


def _analyze_image_physical(image_path: str) -> dict:
    """
    分析图像的物理属性：文件大小、格式、分辨率及清晰度分级。

    清晰度分级标准（基于短边像素数）：
        模糊  : 短边 < 480px
        标准  : 480px ≤ 短边 < 1080px
        高清  : 短边 ≥ 1080px

    返回值:
        dict: {
            "file_size_bytes" : int,   文件字节数
            "file_size_str"   : str,   可读大小（如 "1.23 MB"）
            "format"          : str,   图像格式（如 "JPEG"、"PNG"）
            "width"           : int,   宽度（像素）
            "height"          : int,   高度（像素）
            "short_side"      : int,   短边像素数
            "clarity_level"   : str,   "模糊" / "标准" / "高清"
            "clarity_desc"    : str,   清晰度描述
        }
        出错时返回 {}
    """
    try:
        file_size = os.path.getsize(image_path)
        if file_size < 1024:
            size_str = f"{file_size} B"
        elif file_size < 1024 * 1024:
            size_str = f"{file_size / 1024:.2f} KB"
        else:
            size_str = f"{file_size / (1024 * 1024):.2f} MB"

        with Image.open(image_path) as img:
            fmt = img.format or os.path.splitext(image_path)[-1].lstrip(".").upper()
            width, height = img.size

        short_side = min(width, height)
        if short_side < 480:
            clarity_level = "模糊"
            clarity_desc = f"短边 {short_side}px，低于480px"
        elif short_side < 1080:
            clarity_level = "标准"
            clarity_desc = f"短边 {short_side}px，480~1079px"
        else:
            clarity_level = "高清"
            clarity_desc = f"短边 {short_side}px，≥1080px"

        return {
            "file_size_bytes": file_size,
            "file_size_str":   size_str,
            "format":          fmt,
            "width":           width,
            "height":          height,
            "short_side":      short_side,
            "clarity_level":   clarity_level,
            "clarity_desc":    clarity_desc,
        }
    except Exception as e:
        print(f"  [警告] 图像物理信息分析失败: {e}")
        return {}


def _looks_like_camera_app_software(val) -> bool:
    """
    手机/相机直出常见 Software 字段（含 MediaTek Camera、厂商相册等），
    不应当作「AI 生成工具」元数据信号。
    """
    if val is None:
        return False
    v = str(val).lower()
    if not v.strip():
        return False
    keywords = (
        "camera", "mediatek", "qualcomm", "spreadtrum", "samsung", "huawei",
        "honor", "xiaomi", "redmi", "oppo", "vivo", "oneplus", "realme",
        "meizu", "sony", "pixel", "lg ", "lg-", "mtk", "hyperos", "miui",
        "coloros", "originos", "magicui", "harmonyos", "leica", "hasselblad",
        "zeiss", "fujifilm", "canon", "nikon", "olympus", "panasonic", "dji",
        "apple", "iphone", "ipad", "hdr", "snapdragon", "exynos", "asus",
        "nothing", "motorola", "nokia",
    )
    return any(k in v for k in keywords)


def _looks_like_camera_user_comment(val) -> bool:
    """
    判断 UserComment 是否属于手机厂商/相机系统写入的设备标记，
    如 oplus_xxx、xiaomi_xxx 等。这类不应判为 AI 生成信号。
    """
    if val is None:
        return False
    v = str(val).strip().lower()
    if not v:
        return False
    vendor_prefix = (
        "oplus_", "oneplus_", "oppo_", "vivo_", "xiaomi_", "redmi_",
        "huawei_", "honor_", "samsung_", "realme_", "meizu_",
    )
    if v.startswith(vendor_prefix):
        return True
    # 常见相机写入占位/编码提示
    if v.startswith("ascii") or v.startswith("unicod"):
        return True
    return False


def classify_metadata_signals(metadata: dict, image_path: str = None) -> dict:
    """
    对提取到的元数据进行信号分类，返回结构化的信号分析结果。
    与论文 evidence_summary 逻辑对应，但更加详细。

    返回值:
        dict: {
            "ai_signals"    : list,   AI生成的证据列表
            "real_signals"  : list,   真实图像的证据列表
            "has_ai_signal" : bool,   是否存在AI信号
            "has_real_signal": bool,  是否存在真实图像信号
            "all_metadata"  : dict,   完整过滤后的元数据
            "image_info"    : dict,   图像物理信息（大小/格式/分辨率/清晰度）
        }
    """
    ai_signals = []
    real_signals = []

    if not isinstance(metadata, dict):
        return {
            "ai_signals": [],
            "real_signals": [],
            "has_ai_signal": False,
            "has_real_signal": False,
            "all_metadata": {},
            "image_info": {}
        }

    # ===== AI信号检测 =====
    if "XMP:CreatorTool" in metadata:
        ai_signals.append(f"XMP:CreatorTool = {metadata['XMP:CreatorTool']}")
    if "EXIF:Software" in metadata:
        sw = metadata["EXIF:Software"]
        if _looks_like_camera_app_software(sw):
            real_signals.append(f"EXIF:Software(相机应用) = {sw}")
        else:
            ai_signals.append(f"EXIF:Software = {sw}")
    if "EXIF:UserComment" in metadata:
        uc = metadata["EXIF:UserComment"]
        if _looks_like_camera_user_comment(uc):
            real_signals.append(f"EXIF:UserComment(设备标记) = {uc}")
        else:
            ai_signals.append(f"EXIF:UserComment = {uc}")
    if "File:Comment" in metadata:
        ai_signals.append(f"File:Comment = {metadata['File:Comment']}")
    # Artist 字段（常被AI工具写入生成器名称）
    if "EXIF:Artist" in metadata:
        ai_signals.append(f"EXIF:Artist = {metadata['EXIF:Artist']}")
    if "XMP:Creator" in metadata:
        ai_signals.append(f"XMP:Creator = {metadata['XMP:Creator']}")
    if "IPTC:By-line" in metadata:
        ai_signals.append(f"IPTC:By-line = {metadata['IPTC:By-line']}")
    # JUMBF 块（AI内容标记）
    for key in metadata:
        if key.startswith("JUMBF:"):
            ai_signals.append(f"{key} = {metadata[key]}")
    # AIGC 专用标记字段
    for key in metadata:
        if key.startswith("AIGC:"):
            ai_signals.append(f"{key} = {metadata[key]}")

    # ===== 真实图像信号检测 =====
    if "EXIF:Make" in metadata:
        real_signals.append(f"EXIF:Make = {metadata['EXIF:Make']}")
    if "EXIF:Model" in metadata:
        real_signals.append(f"EXIF:Model = {metadata['EXIF:Model']}")
    if "EXIF:LensModel" in metadata:
        real_signals.append(f"EXIF:LensModel = {metadata['EXIF:LensModel']}")
    if "EXIF:LensInfo" in metadata:
        real_signals.append(f"EXIF:LensInfo = {metadata['EXIF:LensInfo']}")
    if "Composite:GPSPosition" in metadata:
        real_signals.append(f"Composite:GPSPosition = {metadata['Composite:GPSPosition']}")
    if "EXIF:DateTimeOriginal" in metadata:
        real_signals.append(f"EXIF:DateTimeOriginal = {metadata['EXIF:DateTimeOriginal']}")
    if "EXIF:ExposureTime" in metadata:
        real_signals.append(f"EXIF:ExposureTime = {metadata['EXIF:ExposureTime']}")
    if "EXIF:FNumber" in metadata:
        real_signals.append(f"EXIF:FNumber = {metadata['EXIF:FNumber']}")
    if "EXIF:ISO" in metadata:
        real_signals.append(f"EXIF:ISO = {metadata['EXIF:ISO']}")
    if "EXIF:FocalLength" in metadata:
        real_signals.append(f"EXIF:FocalLength = {metadata['EXIF:FocalLength']}")
    if "EXIF:SerialNumber" in metadata:
        real_signals.append(f"EXIF:SerialNumber = {metadata['EXIF:SerialNumber']}")
    # Composite 中的曝光/镜头字段（exiftool 命名空间），同属真实拍摄证据
    for ck in (
        "Composite:LensID",
        "Composite:LensSpec",
        "Composite:ShutterSpeed",
        "Composite:ShutterSpeedValue",
        "Composite:Aperture",
        "Composite:ApertureValue",
        "Composite:ExposureTime",
        "Composite:ISO",
        "Composite:FocalLength",
        "Composite:FocalLength35efl",
        "Composite:LightValue",
    ):
        if ck in metadata:
            real_signals.append(f"{ck} = {metadata[ck]}")
    for key in metadata:
        if key.startswith("MakerNotes:"):
            real_signals.append(f"{key} (相机私有数据)")
            break

    # ===== 图像物理信息 =====
    image_info = _analyze_image_physical(image_path) if image_path else {}

    return {
        "ai_signals":     ai_signals,
        "real_signals":   real_signals,
        "has_ai_signal":  len(ai_signals) > 0,
        "has_real_signal": len(real_signals) > 0,
        "all_metadata":   metadata,
        "image_info":     image_info,
    }


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "test.jpg"
    meta = analyze_image_metadata(path, verbose=True)
    signals = classify_metadata_signals(meta, image_path=path)
    print("\n--- 信号分类结果 ---")
    print(f"AI信号: {signals['ai_signals']}")
    print(f"真实信号: {signals['real_signals']}")
    if signals["image_info"]:
        info = signals["image_info"]
        print(f"\n--- 图像物理信息 ---")
        print(f"文件大小  : {info['file_size_str']}")
        print(f"图像格式  : {info['format']}")
        print(f"分辨率    : {info['width']} x {info['height']} 像素")
        print(f"清晰度等级: {info['clarity_level']}（{info['clarity_desc']}）")

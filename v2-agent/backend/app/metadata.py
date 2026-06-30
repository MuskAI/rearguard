"""File metadata extraction and AI-generation metadata heuristics.

This module is intentionally dependency-light.  It reads common embedded
metadata containers directly so the provenance endpoint can still return useful
evidence when a file has no C2PA manifest.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import struct
import zlib
from dataclasses import dataclass
from typing import Any

try:
    from PIL import ExifTags, Image
except Exception:  # pragma: no cover - Pillow is an optional runtime dep.
    ExifTags = None
    Image = None


MAX_ROWS = 2500
MAX_STRING_LENGTH = 240_000
MAX_SNIPPET_LENGTH = 180
MAX_TEXT_WINDOW_BYTES = 2 * 1024 * 1024
MAX_ISO_BMFF_BYTES = 16 * 1024 * 1024

CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
TRAILING_NULLS_RE = re.compile(r"\x00+$")


@dataclass(frozen=True)
class SignalPattern:
    id: str
    label: str
    weight: int
    regex: re.Pattern[str]


TOOL_PATTERNS = [
    SignalPattern(
        "stable-diffusion",
        "Stable Diffusion / SDXL / FLUX",
        46,
        re.compile(
            r"\b(stable diffusion|stable-diffusion|sdxl|sd\s?1\.5|sd\s?2\.1|sd3|stable cascade|flux\.?1|flux-dev|flux-schnell|dreamstudio|stability ai|automatic1111|a1111|webui|invokeai|fooocus)\b",
            re.I,
        ),
    ),
    SignalPattern(
        "comfyui",
        "ComfyUI 工作流",
        48,
        re.compile(r"\b(comfyui|ksampler|checkpointloader|cliptextencode|vaeloader|saveimage|loadimage)\b", re.I),
    ),
    SignalPattern(
        "novelai",
        "NovelAI 生成信息",
        46,
        re.compile(r"\b(novelai|novel ai|nai diffusion|nai-diffusion|novelai_diffusion)\b", re.I),
    ),
    SignalPattern(
        "midjourney",
        "Midjourney 生成信息",
        44,
        re.compile(r"\b(midjourney|niji journey|niji\s?model|--ar\s+\d+:\d+|--stylize|--sref|--cref)\b", re.I),
    ),
    SignalPattern(
        "openai",
        "OpenAI / DALL-E 生成信息",
        44,
        re.compile(r"\b(openai|dall[ -]?e|dall·e|chatgpt|gpt-image|sora)\b", re.I),
    ),
    SignalPattern(
        "commercial-ai-tools",
        "商业 AI 生成工具",
        40,
        re.compile(
            r"\b(adobe firefly|firefly image|runway|pika labs|pika\s?1|pika\s?2|kling|hailuo|minimax|leonardo\.?ai|ideogram|imagen|recraft|playground ai|seaart|tensor\.?art|pixai|mage\.space|canva ai)\b",
            re.I,
        ),
    ),
    SignalPattern(
        "tc260-aigc",
        "TC260 AIGC 标识",
        70,
        re.compile(
            r"(tc260:aigc|tc260\.org\.cn/ns/aigc|/ns/aigc/1\.0|contentproducer|produceid|(?:\"|&quot;)Label(?:\"|&quot;)\s*:\s*(?:\"|&quot;)1(?:\"|&quot;))",
            re.I,
        ),
    ),
    SignalPattern(
        "civitai-lora",
        "模型/LoRA 生态痕迹",
        30,
        re.compile(r"\b(civitai|lora|lycoris|controlnet|t2i-adapter|ip-adapter|textual inversion|vae|unet)\b", re.I),
    ),
]

PARAMETER_PATTERNS = [
    SignalPattern(
        "sd-parameters",
        "Stable Diffusion 参数块",
        42,
        re.compile(
            r"(?:negative prompt\s*:|steps\s*:\s*\d+|sampler\s*:\s*[\w .+-]+|cfg scale\s*:\s*[\d.]+|seed\s*:\s*\d+|model hash\s*:\s*[a-f0-9]+|denoising strength\s*:)",
            re.I,
        ),
    ),
    SignalPattern(
        "generation-prompt",
        "生成式 prompt 字段",
        20,
        re.compile(r"\b(prompt|negative_prompt|negative prompt|positive prompt|uc|caption)\b", re.I),
    ),
    SignalPattern(
        "generation-settings",
        "采样/种子/模型参数",
        24,
        re.compile(
            r"\b(seed|sampler|steps|cfg[_\s-]?scale|guidance[_\s-]?scale|model[_\s-]?(hash|name|id)?|scheduler|denoise|clip[_\s-]?skip)\b",
            re.I,
        ),
    ),
    SignalPattern(
        "workflow-json",
        "节点式生成工作流",
        40,
        re.compile(r'"class_type"\s*:\s*"(?:KSampler|CheckpointLoader|CLIPTextEncode|VAEDecode|SaveImage)"', re.I),
    ),
    SignalPattern(
        "aigc-disclosure-metadata",
        "AIGC 披露元数据",
        54,
        re.compile(
            r"(<tc260:aigc|</tc260:aigc>|xmlns:tc260|(?:\"|&quot;)ContentProducer(?:\"|&quot;)|(?:\"|&quot;)ProduceID(?:\"|&quot;))",
            re.I,
        ),
    ),
]

KEY_PATTERNS = [
    SignalPattern(
        "ai-key-parameters",
        "AI 参数字段名",
        22,
        re.compile(
            r"(^|[.\[_ -])(parameters|generation[_ -]?data|sd[_ -]?metadata|prompt|negative[_ -]?prompt|workflow|sampler|cfg[_ -]?scale|seed|model[_ -]?hash|model[_ -]?name|clip[_ -]?skip)($|[\]._ -])",
            re.I,
        ),
    ),
    SignalPattern(
        "ai-key-tool",
        "生成工具字段名",
        16,
        re.compile(r"(^|[.\[_ -])(creator[_ -]?tool|software|generator|source|application|producer)($|[\]._ -])", re.I),
    ),
]

CONFIDENCE_TEXT = {
    "high": "高",
    "medium": "中",
    "low": "低",
    "none": "无",
}

SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", "png", "image/png", "image"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg", "image"),
    (b"RIFF", "webp", "image/webp", "image"),
    (b"%PDF-", "pdf", "application/pdf", "pdf"),
    (b"{", "json", "application/json", "json"),
    (b"[", "json", "application/json", "json"),
    (b"<", "xml", "application/xml", "xml"),
]

IMAGE_EXTENSIONS = {"jpg", "jpeg", "jpe", "jfif", "png", "webp", "bmp", "gif", "tif", "tiff", "avif", "heic", "heif"}
ISO_BMFF_EXTENSIONS = {"mp4", "mov", "m4a", "webm", "avif", "heic", "heif"}


def byte_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(max(size, 0))
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    return f"{value:.0f} {units[index]}" if index == 0 else f"{value:.1f} {units[index]}"


def _extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _decode(data: bytes, encoding: str = "utf-8") -> str:
    try:
        return data.decode(encoding)
    except UnicodeDecodeError:
        return data.decode(encoding, errors="replace")


def _snip(value: Any, limit: int = MAX_SNIPPET_LENGTH) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


def _sanitize(value: Any, depth: int = 0) -> Any:
    if depth > 16:
        return "[max depth reached]"
    if isinstance(value, bytes):
        return f"[binary: {len(value)} bytes]"
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str) and len(value) > MAX_STRING_LENGTH:
            return f"{value[:MAX_STRING_LENGTH]}...[truncated {len(value) - MAX_STRING_LENGTH} chars]"
        return value
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, depth + 1) for item in value[:500]]
    if isinstance(value, dict):
        return {str(key): _sanitize(item, depth + 1) for key, item in list(value.items())[:1000]}
    return str(value)


def _count_leaves(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, dict):
        return sum(_count_leaves(item) for item in value.values())
    if isinstance(value, list):
        return sum(_count_leaves(item) for item in value)
    return 1


def _flatten(value: Any, prefix: str = "", rows: list[dict[str, str]] | None = None, depth: int = 0) -> list[dict[str, str]]:
    if rows is None:
        rows = []
    if len(rows) >= 200 or depth > 10:
        return rows
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten(item, f"{prefix}.{key}" if prefix else str(key), rows, depth + 1)
            if len(rows) >= 200:
                break
        return rows
    if isinstance(value, list):
        for index, item in enumerate(value[:100]):
            _flatten(item, f"{prefix}[{index}]", rows, depth + 1)
            if len(rows) >= 200:
                break
        return rows
    rows.append({"path": prefix or "$", "value": _snip(value)})
    return rows


def _collect_rows(value: Any, prefix: str = "", rows: list[dict[str, str]] | None = None, depth: int = 0) -> list[dict[str, str]]:
    if rows is None:
        rows = []
    if len(rows) >= MAX_ROWS or depth > 12 or value is None:
        return rows
    if not isinstance(value, (dict, list)):
        rows.append({"path": prefix or "$", "value": "" if value is None else str(value)})
        return rows
    if isinstance(value, list):
        for index, item in enumerate(value[:200]):
            _collect_rows(item, f"{prefix}[{index}]", rows, depth + 1)
            if len(rows) >= MAX_ROWS:
                break
        return rows
    for key, item in value.items():
        _collect_rows(item, f"{prefix}.{key}" if prefix else str(key), rows, depth + 1)
        if len(rows) >= MAX_ROWS:
            break
    return rows


def _collect_embedded_json_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    extra: list[dict[str, str]] = []
    for row in rows:
        text = row["value"].strip()
        if not text or len(text) > MAX_STRING_LENGTH or text[0] not in "{[":
            continue
        try:
            parsed = json.loads(text)
        except Exception:
            continue
        _collect_rows(parsed, f"{row['path']}{{json}}", extra)
        if len(extra) >= MAX_ROWS:
            break
    return extra[:MAX_ROWS]


def _score_signals(signals: list[dict[str, Any]]) -> int:
    by_id: dict[str, dict[str, Any]] = {}
    for signal in signals:
        current = by_id.get(signal["id"])
        if current is None or signal["weight"] > current["weight"]:
            by_id[signal["id"]] = signal
    base = sum(signal["weight"] for signal in by_id.values())
    density_bonus = min(18, max(0, len(signals) - len(by_id)) * 3)
    return min(100, round(base + density_bonus))


def _confidence(score: int) -> str:
    if score >= 75:
        return "high"
    if score >= 45:
        return "medium"
    if score >= 25:
        return "low"
    return "none"


def analyze_ai_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    base_rows = _collect_rows(metadata)
    rows = base_rows + _collect_embedded_json_rows(base_rows)
    signals: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_signal(pattern: SignalPattern, path: str, value: str, reason: str) -> None:
        key = f"{pattern.id}:{path}"
        if key in seen:
            return
        seen.add(key)
        signals.append(
            {
                "id": pattern.id,
                "label": pattern.label,
                "weight": pattern.weight,
                "path": path,
                "reason": reason,
                "value": _snip(value),
            }
        )

    for row in rows:
        value = row["value"]
        searchable = f"{row['path']}\n{value}"
        lower_path = row["path"].lower()
        for pattern in TOOL_PATTERNS:
            if pattern.regex.search(searchable):
                add_signal(pattern, row["path"], value, "命中已知 AI 生成工具或模型名称")
        for pattern in PARAMETER_PATTERNS:
            if pattern.regex.search(searchable):
                add_signal(pattern, row["path"], value, "命中生成参数、prompt 或工作流结构")
        for pattern in KEY_PATTERNS:
            if pattern.regex.search(lower_path):
                add_signal(pattern, row["path"], value, "命中常见 AI 元数据字段名")

    ranked = sorted(signals, key=lambda item: item["weight"], reverse=True)[:16]
    score = _score_signals(signals)
    confidence = _confidence(score)
    return {
        "score": score,
        "confidence": confidence,
        "confidenceText": CONFIDENCE_TEXT[confidence],
        "isAiLikely": score >= 45,
        "signalCount": len(signals),
        "matchedTools": list(dict.fromkeys(signal["label"] for signal in ranked))[:8],
        "signals": ranked,
    }


def _detect_signature(data: bytes, filename: str, mime: str | None) -> dict[str, Any]:
    head = data[:32]
    for magic, extension, detected_mime, kind in SIGNATURES:
        if head.startswith(magic):
            if extension == "webp" and data[8:12] != b"WEBP":
                continue
            return {"extension": extension, "mime": detected_mime, "kind": kind, "magic": head[: len(magic)].hex()}
    ext = _extension(filename)
    if ext in IMAGE_EXTENSIONS:
        kind = "image"
    elif ext in ISO_BMFF_EXTENSIONS:
        kind = "iso-bmff"
    elif ext == "pdf":
        kind = "pdf"
    elif ext in {"json"}:
        kind = "json"
    elif ext in {"xml", "xmp", "svg"}:
        kind = "xml"
    elif ext in {"txt", "md"}:
        kind = "text"
    else:
        kind = "generic"
    return {"extension": ext or None, "mime": mime or "", "kind": kind, "magic": head[:12].hex()}


def _read_pil_image(data: bytes) -> dict[str, Any] | None:
    if Image is None:
        return None
    import io

    with Image.open(io.BytesIO(data)) as image:
        output: dict[str, Any] = {
            "format": image.format,
            "mode": image.mode,
            "size": {"width": image.width, "height": image.height},
        }
        if image.info:
            output["info"] = {key: value for key, value in image.info.items() if not isinstance(value, bytes)}
        exif = image.getexif()
        if exif:
            tags = ExifTags.TAGS if ExifTags is not None else {}
            output["exif"] = {str(tags.get(tag, tag)): _sanitize(value) for tag, value in exif.items()}
        return output


def _split_null(data: bytes, start: int = 0) -> tuple[bytes, int]:
    index = data.find(b"\x00", start)
    if index < 0:
        return data[start:], len(data)
    return data[start:index], index + 1


def _parse_png_text_chunk(chunk_type: str, payload: bytes) -> dict[str, Any]:
    keyword, pos = _split_null(payload)
    result: dict[str, Any] = {"keyword": _decode(keyword, "latin-1")}
    if chunk_type == "tEXt":
        result["text"] = _decode(payload[pos:], "latin-1")
        return result
    if chunk_type == "zTXt":
        compression_method = payload[pos] if pos < len(payload) else None
        result["compressionMethod"] = compression_method
        compressed = payload[pos + 1 :]
        try:
            result["text"] = _decode(zlib.decompress(compressed), "latin-1")
        except Exception as exc:
            result["error"] = f"zTXt decompress failed: {exc}"
        return result
    if chunk_type == "iTXt":
        compression_flag = payload[pos] if pos < len(payload) else 0
        compression_method = payload[pos + 1] if pos + 1 < len(payload) else 0
        language, pos2 = _split_null(payload, pos + 2)
        translated, pos3 = _split_null(payload, pos2)
        text_bytes = payload[pos3:]
        if compression_flag:
            try:
                text_bytes = zlib.decompress(text_bytes)
            except Exception as exc:
                result["error"] = f"iTXt decompress failed: {exc}"
        result.update(
            {
                "compressionFlag": compression_flag,
                "compressionMethod": compression_method,
                "language": _decode(language),
                "translatedKeyword": _decode(translated),
                "text": _decode(text_bytes),
            }
        )
    return result


def _read_png(data: bytes) -> dict[str, Any] | None:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    chunks = []
    text_chunks = []
    cursor = 8
    while cursor + 12 <= len(data):
        length = struct.unpack(">I", data[cursor : cursor + 4])[0]
        chunk_type = _decode(data[cursor + 4 : cursor + 8], "latin-1")
        payload_start = cursor + 8
        payload_end = payload_start + length
        if payload_end + 4 > len(data):
            break
        crc = struct.unpack(">I", data[payload_end : payload_end + 4])[0]
        chunks.append({"type": chunk_type, "length": length, "crc": f"0x{crc:08x}"})
        if chunk_type in {"tEXt", "zTXt", "iTXt"}:
            text_chunks.append({"type": chunk_type, **_parse_png_text_chunk(chunk_type, data[payload_start:payload_end])})
        cursor = payload_end + 4
        if chunk_type == "IEND":
            break
    return {"chunks": chunks, "textChunks": text_chunks}


def _read_null_string(data: bytes) -> str:
    return _decode(data.split(b"\x00", 1)[0], "latin-1")


def _read_jpeg(data: bytes) -> dict[str, Any] | None:
    if len(data) < 4 or not data.startswith(b"\xff\xd8"):
        return None
    segments = []
    comments = []
    xmp_packets = []
    cursor = 2
    while cursor + 4 <= len(data):
        if data[cursor] != 0xFF:
            cursor += 1
            continue
        marker = data[cursor + 1]
        cursor += 2
        if marker in {0xD9, 0xDA}:
            break
        if cursor + 2 > len(data):
            break
        seg_length = struct.unpack(">H", data[cursor : cursor + 2])[0]
        payload_start = cursor + 2
        payload_end = payload_start + seg_length - 2
        if seg_length < 2 or payload_end > len(data):
            break
        payload = data[payload_start:payload_end]
        marker_name = f"APP{marker - 0xE0}" if 0xE0 <= marker <= 0xEF else "COM" if marker == 0xFE else f"0x{marker:02x}"
        segment: dict[str, Any] = {"marker": marker_name, "length": len(payload)}
        if marker == 0xE0:
            segment["identifier"] = _read_null_string(payload)
        elif marker == 0xE1:
            header = _read_null_string(payload)
            segment["identifier"] = header
            if header == "Exif":
                segment["kind"] = "exif"
            elif header == "http://ns.adobe.com/xap/1.0/":
                segment["kind"] = "xmp"
                xmp_packets.append(_decode(payload[len("http://ns.adobe.com/xap/1.0/") + 1 :]))
        elif marker == 0xE2:
            segment["identifier"] = _read_null_string(payload)
            if segment["identifier"].startswith("ICC_PROFILE"):
                segment["kind"] = "icc"
        elif marker == 0xED:
            segment["identifier"] = _read_null_string(payload)
            if segment["identifier"].startswith("Photoshop"):
                segment["kind"] = "photoshop-iptc"
        elif marker == 0xFE:
            segment["kind"] = "comment"
            comments.append(_decode(payload))
        segments.append(segment)
        cursor = payload_end
    return {"segments": segments, "comments": comments, "xmpPackets": xmp_packets}


def _read_webp(data: bytes) -> dict[str, Any] | None:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    chunks = []
    metadata_chunks: dict[str, str] = {}
    cursor = 12
    while cursor + 8 <= len(data):
        chunk_type = _decode(data[cursor : cursor + 4], "latin-1")
        length = struct.unpack("<I", data[cursor + 4 : cursor + 8])[0]
        payload_start = cursor + 8
        payload_end = payload_start + length
        if payload_end > len(data):
            break
        payload = data[payload_start:payload_end]
        chunks.append({"type": chunk_type, "length": length})
        if chunk_type in {"EXIF", "XMP ", "ICCP"}:
            key = chunk_type.strip().lower()
            metadata_chunks[key] = _decode(payload) if chunk_type == "XMP " else f"[binary: {len(payload)} bytes]"
        cursor = payload_end + (length % 2)
    return {"chunks": chunks, "metadataChunks": metadata_chunks}


def _read_text_window(data: bytes) -> str:
    if len(data) <= MAX_TEXT_WINDOW_BYTES:
        sample = data
    else:
        half = MAX_TEXT_WINDOW_BYTES // 2
        sample = data[:half] + b"\n...[middle omitted]...\n" + data[-half:]
    return _decode(sample)


def _decode_pdf_value(value: str) -> str:
    value = value.strip()
    if value.startswith("(") and value.endswith(")"):
        return value[1:-1].replace(r"\)", ")").replace(r"\(", "(")
    if value.startswith("<") and value.endswith(">"):
        hex_text = re.sub(r"\s+", "", value[1:-1])
        try:
            return bytes.fromhex(hex_text).decode("utf-16-be" if hex_text.startswith("feff") else "utf-8", errors="replace")
        except Exception:
            return value
    return value


def _read_pdf(data: bytes) -> dict[str, Any]:
    text = _read_text_window(data)
    info: dict[str, Any] | None = None
    raw_info = ""
    ref_match = re.search(r"/Info\s+(\d+)\s+(\d+)\s+R", text)
    if ref_match:
        object_regex = re.compile(rf"{ref_match.group(1)}\s+{ref_match.group(2)}\s+obj([\s\S]*?)endobj")
        raw_info = (object_regex.search(text) or [None, ""])[1].strip()
    if not raw_info:
        raw_match = re.search(r"<<[\s\S]{0,4000}/(?:Title|Author|Creator|Producer|CreationDate|ModDate)[\s\S]*?>>", text)
        raw_info = raw_match.group(0) if raw_match else ""
    if raw_info:
        entries = {}
        for match in re.finditer(r"/([A-Za-z][A-Za-z0-9]*)\s*(\((?:\\.|[^\\)])*\)|<[\da-fA-F\s]+>|/[^\s<>\[\]()/]+|\[[^\]]*]|-?\d+(?:\.\d+)?)", raw_info):
            entries[match.group(1)] = _decode_pdf_value(match.group(2))
        info = {"raw": raw_info, "entries": entries}
    xmp_match = re.search(r"<x:xmpmeta[\s\S]*?</x:xmpmeta>", text) or re.search(
        r"<\?xpacket begin=[\s\S]*?<\?xpacket end=['\"][rw]['\"]\?>",
        text,
    )
    version_match = re.search(r"^%PDF-(\d+\.\d+)", text)
    return {"version": version_match.group(1) if version_match else None, "info": info, "xmp": xmp_match.group(0) if xmp_match else None}


def _quicktime_date(seconds: int | float | None) -> str | None:
    if not seconds or seconds <= 0:
        return None
    return _dt.datetime.fromtimestamp(seconds - 2_082_844_800, tz=_dt.timezone.utc).isoformat()


def _read_box(data: bytes, start: int, end: int) -> dict[str, Any] | None:
    if start + 8 > end:
        return None
    size = struct.unpack(">I", data[start : start + 4])[0]
    box_type = _decode(data[start + 4 : start + 8], "latin-1")
    header = 8
    if size == 1:
        if start + 16 > end:
            return None
        size = struct.unpack(">Q", data[start + 8 : start + 16])[0]
        header = 16
    elif size == 0:
        size = end - start
    if size < header or start + size > end:
        return None
    return {"type": box_type, "start": start, "size": size, "headerSize": header, "end": start + size}


def _read_iso_bmff(data: bytes) -> dict[str, Any]:
    partial = len(data) > MAX_ISO_BMFF_BYTES
    sample = data[:MAX_ISO_BMFF_BYTES] if partial else data
    boxes = []
    movie_headers = []
    box_count = 0

    def parse(start: int, end: int, depth: int = 0) -> list[dict[str, Any]]:
        nonlocal box_count
        nodes = []
        cursor = start
        while cursor + 8 <= end and box_count < 5000:
            box = _read_box(sample, cursor, end)
            if not box:
                break
            box_count += 1
            node = {"type": box["type"], "offset": box["start"], "size": box["size"]}
            content = box["start"] + box["headerSize"]
            if box["type"] == "mvhd" and content + 20 <= box["end"]:
                version = sample[content]
                if version == 0:
                    created = struct.unpack(">I", sample[content + 4 : content + 8])[0]
                    modified = struct.unpack(">I", sample[content + 8 : content + 12])[0]
                    timescale = struct.unpack(">I", sample[content + 12 : content + 16])[0]
                    duration = struct.unpack(">I", sample[content + 16 : content + 20])[0]
                    movie_headers.append(
                        {
                            "creationTime": created,
                            "creationDate": _quicktime_date(created),
                            "modificationTime": modified,
                            "modificationDate": _quicktime_date(modified),
                            "timescale": timescale,
                            "duration": duration,
                            "durationSeconds": duration / timescale if timescale else None,
                        }
                    )
            is_container = box["type"] in {"moov", "trak", "mdia", "minf", "udta", "ilst", "moof", "traf", "mvex"}
            if is_container and depth < 8:
                node["children"] = parse(content, box["end"], depth + 1)
            elif box["type"] == "meta" and depth < 8:
                node["children"] = parse(content + 4, box["end"], depth + 1)
            nodes.append(node)
            cursor = box["end"]
        return nodes

    boxes = parse(0, len(sample))
    return {
        "readScope": f"parsed first {byte_size(len(sample))} of {byte_size(len(data))}" if partial else "complete file",
        "boxes": boxes,
        "movieHeaders": movie_headers,
    }


def inspect_metadata(data: bytes, filename: str = "", mime: str | None = None) -> dict[str, Any]:
    signature = _detect_signature(data, filename, mime)
    kind = signature["kind"]
    metadata: dict[str, Any] = {
        "file": {
            "name": filename or "uploaded asset",
            "type": mime or "",
            "size": len(data),
            "sizeLabel": byte_size(len(data)),
            "detectedKind": kind,
            "declaredKind": _extension(filename) or None,
            "signature": signature,
        }
    }
    errors: list[dict[str, str]] = []

    if kind == "image":
        try:
            image_metadata = _read_pil_image(data)
            if image_metadata:
                metadata["image"] = image_metadata
        except Exception as exc:
            errors.append({"section": "image", "message": f"图片元数据读取失败: {exc}"})
        try:
            png_metadata = _read_png(data)
            if png_metadata:
                metadata["png"] = png_metadata
        except Exception as exc:
            errors.append({"section": "png", "message": f"PNG chunk 读取失败: {exc}"})
        try:
            jpeg_metadata = _read_jpeg(data)
            if jpeg_metadata:
                metadata["jpeg"] = jpeg_metadata
        except Exception as exc:
            errors.append({"section": "jpeg", "message": f"JPEG segment 读取失败: {exc}"})
        try:
            webp_metadata = _read_webp(data)
            if webp_metadata:
                metadata["webp"] = webp_metadata
        except Exception as exc:
            errors.append({"section": "webp", "message": f"WebP chunk 读取失败: {exc}"})

    if kind == "pdf":
        try:
            metadata["pdf"] = _read_pdf(data)
        except Exception as exc:
            errors.append({"section": "pdf", "message": f"PDF 元数据读取失败: {exc}"})

    if kind == "iso-bmff":
        try:
            metadata["isoBmff"] = _read_iso_bmff(data)
        except Exception as exc:
            errors.append({"section": "isoBmff", "message": f"MP4/MOV 元数据读取失败: {exc}"})

    if kind == "json":
        text = _read_text_window(data).strip()
        try:
            metadata["json"] = {"parsed": json.loads(text)}
        except Exception as exc:
            metadata["json"] = {"parseError": str(exc), "textPreview": text[:20_000], "truncated": len(text) > 20_000}

    if kind == "xml":
        metadata["xml"] = {"text": _read_text_window(data)}

    if kind in {"text", "generic"}:
        text = _read_text_window(data)
        printable = CONTROL_CHARS_RE.sub("", text)
        ratio = len(printable) / len(text) if text else 0
        if ratio >= 0.7:
            metadata["text"] = {"printableRatio": ratio, "textPreview": text[:20_000], "truncated": len(text) > 20_000}
        else:
            metadata["text"] = {"printableRatio": ratio, "skipped": "binary-like text window"}

    if errors:
        metadata["errors"] = errors

    sanitized = _sanitize(metadata)
    ai_detection = analyze_ai_metadata(sanitized)
    sections = [
        {"name": name, "fieldCount": _count_leaves(value)}
        for name, value in sanitized.items()
        if value and (not isinstance(value, dict) or value)
    ]
    embedded_sections = [section for section in sections if section["name"] not in {"file", "browser", "errors"}]
    field_count = _count_leaves(sanitized)

    return {
        "hasMetadata": field_count > 0,
        "hasEmbeddedMetadata": len(embedded_sections) > 0,
        "metadata": sanitized,
        "aiDetection": ai_detection,
        "metadataSummary": {
            "sectionCount": len(sections),
            "embeddedSectionCount": len(embedded_sections),
            "fieldCount": field_count,
            "sections": sections,
            "preview": _flatten(sanitized),
            "errors": errors,
            "aiDetection": ai_detection,
        },
    }

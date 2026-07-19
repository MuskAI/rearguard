from __future__ import annotations

from dataclasses import dataclass
import io
import re
import zipfile
from xml.etree import ElementTree


PLAIN_TEXT_EXTENSIONS = {"txt", "md", "csv", "json", "log"}
WORD_EXTENSIONS = {"docx"}
UNSUPPORTED_BINARY_EXTENSIONS = {"pdf", "doc"}
MAX_DOCX_MEMBERS = 256
MAX_DOCX_TOTAL_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
MAX_DOCX_DOCUMENT_XML_BYTES = 8 * 1024 * 1024
MAX_DOCX_COMPRESSION_RATIO = 200.0
MAX_DOCX_XML_ELEMENTS = 200_000


class DocumentSafetyError(ValueError):
    pass


@dataclass
class ExtractedDocument:
    text: str
    note: str


def _normalize_text(value: str) -> str:
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _decode_plain_text(data: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _extract_docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) > MAX_DOCX_MEMBERS:
            raise DocumentSafetyError("DOCX contains too many archive members")
        total_uncompressed = sum(max(0, member.file_size) for member in members)
        if total_uncompressed > MAX_DOCX_TOTAL_UNCOMPRESSED_BYTES:
            raise DocumentSafetyError("DOCX expanded size exceeds the safety limit")
        for member in members:
            if member.flag_bits & 0x1:
                raise DocumentSafetyError("encrypted DOCX members are not supported")
            if member.file_size <= 0:
                continue
            ratio = member.file_size / max(1, member.compress_size)
            if ratio > MAX_DOCX_COMPRESSION_RATIO:
                raise DocumentSafetyError("DOCX compression ratio exceeds the safety limit")
        try:
            document = archive.getinfo("word/document.xml")
        except KeyError:
            raise
        if document.file_size > MAX_DOCX_DOCUMENT_XML_BYTES:
            raise DocumentSafetyError("DOCX document XML exceeds the safety limit")
        with archive.open(document) as source:
            xml_bytes = source.read(MAX_DOCX_DOCUMENT_XML_BYTES + 1)
        if len(xml_bytes) > MAX_DOCX_DOCUMENT_XML_BYTES:
            raise DocumentSafetyError("DOCX document XML exceeds the safety limit")
    root = ElementTree.fromstring(xml_bytes)
    parts: list[str] = []
    for index, element in enumerate(root.iter(), start=1):
        if index > MAX_DOCX_XML_ELEMENTS:
            raise DocumentSafetyError("DOCX XML element count exceeds the safety limit")
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "t" and element.text:
            parts.append(element.text)
        elif tag in {"p", "tr"}:
            parts.append("\n")
        elif tag == "tab":
            parts.append("\t")
    return "".join(parts)


def extract_text(filename: str, data: bytes) -> ExtractedDocument:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext in PLAIN_TEXT_EXTENSIONS:
        text = _normalize_text(_decode_plain_text(data))
        return ExtractedDocument(text=text, note="已提取纯文本正文")

    if ext in WORD_EXTENSIONS:
        try:
            text = _normalize_text(_extract_docx_text(data))
        except DocumentSafetyError:
            return ExtractedDocument(text="", note="DOCX 文件超出安全解析限制")
        except (KeyError, zipfile.BadZipFile, ElementTree.ParseError, RuntimeError):
            return ExtractedDocument(text="", note="DOCX 文件解析失败")
        if not text:
            return ExtractedDocument(text="", note="DOCX 文件未提取到可分析正文")
        return ExtractedDocument(text=text, note="已从 DOCX 提取正文")

    if ext in UNSUPPORTED_BINARY_EXTENSIONS:
        return ExtractedDocument(text="", note=f"当前未支持 {ext.upper()} 正文抽取")

    decoded = _normalize_text(_decode_plain_text(data))
    if decoded:
        return ExtractedDocument(text=decoded, note="已按通用文本方式提取正文")
    return ExtractedDocument(text="", note="未提取到可分析正文")

from __future__ import annotations

from dataclasses import dataclass
import io
import re
import zipfile
from xml.etree import ElementTree


PLAIN_TEXT_EXTENSIONS = {"txt", "md", "csv", "json", "log"}
WORD_EXTENSIONS = {"docx"}
UNSUPPORTED_BINARY_EXTENSIONS = {"pdf", "doc"}


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
        xml_bytes = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml_bytes)
    parts: list[str] = []
    for element in root.iter():
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

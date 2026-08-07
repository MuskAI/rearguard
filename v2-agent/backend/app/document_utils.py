from __future__ import annotations

from dataclasses import dataclass
import io
import multiprocessing
import os
import re
import sys
import zipfile
from xml.etree import ElementTree

from pypdf import PdfReader
from pypdf.errors import PdfReadError


PLAIN_TEXT_EXTENSIONS = {"txt", "md", "csv", "json", "log"}
WORD_EXTENSIONS = {"docx"}
PDF_EXTENSIONS = {"pdf"}
UNSUPPORTED_BINARY_EXTENSIONS = {"doc"}
MAX_DOCX_MEMBERS = 256
MAX_DOCX_TOTAL_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
MAX_DOCX_DOCUMENT_XML_BYTES = 8 * 1024 * 1024
MAX_DOCX_COMPRESSION_RATIO = 200.0
MAX_DOCX_XML_ELEMENTS = 200_000
MAX_PDF_INPUT_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES = 24
MAX_PDF_PAGE_CHARACTERS = 4_000
MAX_PDF_TOTAL_CHARACTERS = 12_000
PDF_PARSE_WALL_SECONDS = float(os.getenv("JIANZHEN_PDF_PARSE_WALL_SECONDS", "6"))
PDF_PARSE_CPU_SECONDS = int(os.getenv("JIANZHEN_PDF_PARSE_CPU_SECONDS", "4"))
PDF_PARSE_MEMORY_BYTES = int(os.getenv("JIANZHEN_PDF_PARSE_MEMORY_BYTES", str(512 * 1024 * 1024)))


class DocumentSafetyError(ValueError):
    pass


class EncryptedPdfError(ValueError):
    pass


class PdfParseTimeoutError(ValueError):
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


def _sample_page_indices(page_count: int, page_limit: int) -> list[int]:
    if page_count <= page_limit:
        return list(range(page_count))
    if page_limit <= 1:
        return [0]
    return sorted({round(index * (page_count - 1) / (page_limit - 1)) for index in range(page_limit)})


def _extract_pdf_text_untrusted(
    data: bytes,
    *,
    max_pages: int,
    max_page_characters: int,
    max_total_characters: int,
) -> tuple[str, int, int, bool, int]:
    reader = PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted:
        raise EncryptedPdfError("encrypted PDF is not supported")
    page_count = len(reader.pages)
    page_indices = _sample_page_indices(page_count, max_pages)
    page_character_budget = min(
        max_page_characters,
        max(256, max_total_characters // max(1, len(page_indices))),
    )
    parts: list[str] = []
    total_characters = 0
    truncated = page_count > max_pages
    failed_pages = 0
    processed_pages = 0
    for index in page_indices:
        processed_pages += 1
        try:
            page_text = _normalize_text(reader.pages[index].extract_text() or "")
        except (PdfReadError, KeyError, ValueError, TypeError, AttributeError, RecursionError):
            failed_pages += 1
            continue
        if not page_text:
            continue
        if len(page_text) > page_character_budget:
            page_text = page_text[:page_character_budget]
            truncated = True
        remaining = max_total_characters - total_characters
        if remaining <= 0:
            truncated = True
            break
        if len(page_text) > remaining:
            page_text = page_text[:remaining]
            truncated = True
        parts.append(page_text)
        total_characters += len(page_text)
        if total_characters >= max_total_characters:
            truncated = True
            break
    return _normalize_text("\n\n".join(parts)), page_count, processed_pages, truncated, failed_pages


def _apply_pdf_resource_limits(cpu_seconds: int, memory_bytes: int) -> None:
    try:
        import resource
    except ImportError:
        return
    if cpu_seconds > 0:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
    if sys.platform.startswith("linux") and memory_bytes > 0:
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    if hasattr(resource, "RLIMIT_NOFILE"):
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        descriptor_limit = min(64, hard if hard >= 0 else 64)
        resource.setrlimit(resource.RLIMIT_NOFILE, (descriptor_limit, descriptor_limit))


def _pdf_parse_worker(connection, data: bytes, limits: tuple[int, int, int, int, int]) -> None:
    max_pages, max_page_characters, max_total_characters, cpu_seconds, memory_bytes = limits
    try:
        _apply_pdf_resource_limits(cpu_seconds, memory_bytes)
        result = _extract_pdf_text_untrusted(
            data,
            max_pages=max_pages,
            max_page_characters=max_page_characters,
            max_total_characters=max_total_characters,
        )
        connection.send(("ok", result))
    except EncryptedPdfError as exc:
        connection.send(("encrypted", str(exc)))
    except DocumentSafetyError as exc:
        connection.send(("safety", str(exc)))
    except BaseException as exc:
        try:
            connection.send(("error", type(exc).__name__))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


def _stop_process(process) -> None:
    if process.pid is None:
        return
    if not process.is_alive():
        process.join(timeout=0.1)
        return
    process.terminate()
    process.join(timeout=0.5)
    if process.is_alive():
        process.kill()
        process.join(timeout=0.5)


def _extract_pdf_text(data: bytes) -> tuple[str, int, int, bool, int]:
    if len(data) > MAX_PDF_INPUT_BYTES:
        raise DocumentSafetyError("PDF input exceeds the safety limit")
    context = multiprocessing.get_context("spawn")
    receive_connection, send_connection = context.Pipe(duplex=False)
    limits = (
        MAX_PDF_PAGES,
        MAX_PDF_PAGE_CHARACTERS,
        MAX_PDF_TOTAL_CHARACTERS,
        PDF_PARSE_CPU_SECONDS,
        PDF_PARSE_MEMORY_BYTES,
    )
    process = context.Process(
        target=_pdf_parse_worker,
        args=(send_connection, data, limits),
        name="huijian-pdf-parser",
        daemon=True,
    )
    try:
        process.start()
        send_connection.close()
        if not receive_connection.poll(max(0.05, PDF_PARSE_WALL_SECONDS)):
            raise PdfParseTimeoutError("PDF parsing exceeded the wall-time limit")
        try:
            kind, payload = receive_connection.recv()
        except EOFError as exc:
            raise DocumentSafetyError("PDF parser exited before returning a result") from exc
        if kind == "ok":
            return payload
        if kind == "encrypted":
            raise EncryptedPdfError(payload)
        if kind == "safety":
            raise DocumentSafetyError(payload)
        raise PdfReadError(f"isolated PDF parser failed: {payload}")
    finally:
        receive_connection.close()
        send_connection.close()
        _stop_process(process)


def analysis_excerpt(text: str, max_characters: int = 4_000) -> str:
    normalized = _normalize_text(text)
    if len(normalized) <= max_characters:
        return normalized
    separator = "\n\n[中间内容已抽样]\n\n"
    available = max(3, max_characters - len(separator) * 2)
    head_length = available // 2
    middle_length = available // 4
    tail_length = available - head_length - middle_length
    middle_start = max(0, (len(normalized) - middle_length) // 2)
    return (
        normalized[:head_length]
        + separator
        + normalized[middle_start:middle_start + middle_length]
        + separator
        + normalized[-tail_length:]
    )[:max_characters]


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

    if ext in PDF_EXTENSIONS:
        try:
            text, page_count, processed_pages, truncated, failed_pages = _extract_pdf_text(data)
        except EncryptedPdfError:
            return ExtractedDocument(text="", note="加密 PDF 暂不支持解析")
        except PdfParseTimeoutError:
            return ExtractedDocument(text="", note="PDF 解析超时，已安全终止")
        except DocumentSafetyError:
            return ExtractedDocument(text="", note="PDF 文件超出安全解析限制")
        except (PdfReadError, ValueError, TypeError, OSError, RecursionError):
            return ExtractedDocument(text="", note="PDF 文件解析失败")
        if not text:
            return ExtractedDocument(text="", note="PDF 未提取到可分析正文；扫描版 PDF 暂不支持 OCR")
        note = f"已从 PDF 提取 {processed_pages}/{page_count} 页正文"
        if truncated:
            note += "（达到安全解析上限，正文已截断）"
        elif failed_pages:
            note += f"（{failed_pages} 页未能解析）"
        return ExtractedDocument(text=text, note=note)

    if ext in UNSUPPORTED_BINARY_EXTENSIONS:
        return ExtractedDocument(text="", note=f"当前未支持 {ext.upper()} 正文抽取")

    decoded = _normalize_text(_decode_plain_text(data))
    if decoded:
        return ExtractedDocument(text=decoded, note="已按通用文本方式提取正文")
    return ExtractedDocument(text="", note="未提取到可分析正文")

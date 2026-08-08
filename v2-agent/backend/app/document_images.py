from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import multiprocessing
import os
import posixpath
from pathlib import PurePosixPath
import sys
import time
from typing import Literal
from urllib.parse import unquote, urlsplit
import warnings as python_warnings
import zipfile
from xml.etree import ElementTree

from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PdfReadError


__all__ = [
    "DocumentExtraction",
    "DocumentImageAsset",
    "DocumentImageError",
    "extract_document_images",
]


ErrorCode = Literal["unsupported", "invalid", "limit"]

MAX_INPUT_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES = 200
MAX_IMAGES = 500
MAX_TOTAL_IMAGE_BYTES = 256 * 1024 * 1024
MAX_SINGLE_IMAGE_BYTES = 24 * 1024 * 1024
MAX_IMAGE_PIXELS = 64_000_000
MAX_DOCX_MEMBERS = 2_048
MAX_DOCX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_DOCX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_DOCX_COMPRESSION_RATIO = 200.0
MAX_XML_BYTES = 16 * 1024 * 1024
MAX_XML_ELEMENTS = 250_000
EXTRACTION_WALL_SECONDS = float(os.getenv("JIANZHEN_DOCUMENT_IMAGE_WALL_SECONDS", "60"))
PDF_CPU_SECONDS = int(os.getenv("JIANZHEN_DOCUMENT_IMAGE_CPU_SECONDS", "30"))
PDF_MEMORY_BYTES = int(
    os.getenv("JIANZHEN_DOCUMENT_IMAGE_MEMORY_BYTES", str(1024 * 1024 * 1024))
)

_RELATIONSHIPS_NAMESPACE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
_IMAGE_RELATIONSHIP_SUFFIX = "/image"
_RASTER_MIME_BY_FORMAT = {
    "BMP": "image/bmp",
    "GIF": "image/gif",
    "ICO": "image/x-icon",
    "JPEG": "image/jpeg",
    "JPEG2000": "image/jp2",
    "PNG": "image/png",
    "TIFF": "image/tiff",
    "WEBP": "image/webp",
}
_RASTER_SUFFIXES = {
    ".bmp",
    ".gif",
    ".ico",
    ".jfif",
    ".jp2",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}
_ALLOWED_ZIP_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}


class DocumentImageError(ValueError):
    """A stable, user-safe failure raised while extracting document images."""

    def __init__(self, code: ErrorCode, message: str):
        if code not in {"unsupported", "invalid", "limit"}:
            raise ValueError(f"unknown document image error code: {code}")
        self.code: ErrorCode = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class DocumentImageAsset:
    ordinal: int
    data: bytes
    mime: str
    width: int
    height: int
    sha256: str
    source_kind: str
    page_number: int | None
    part_path: str | None
    occurrence_index: int
    duplicate_of: int | None


@dataclass(frozen=True, slots=True)
class DocumentExtraction:
    filename: str
    page_count: int | None
    assets: list[DocumentImageAsset]
    warnings: list[str]


@dataclass(frozen=True, slots=True)
class _Limits:
    max_pdf_pages: int
    max_images: int
    max_total_image_bytes: int
    max_single_image_bytes: int
    max_image_pixels: int
    wall_seconds: float
    pdf_cpu_seconds: int
    pdf_memory_bytes: int


def _limits() -> _Limits:
    return _Limits(
        max_pdf_pages=MAX_PDF_PAGES,
        max_images=MAX_IMAGES,
        max_total_image_bytes=MAX_TOTAL_IMAGE_BYTES,
        max_single_image_bytes=MAX_SINGLE_IMAGE_BYTES,
        max_image_pixels=MAX_IMAGE_PIXELS,
        wall_seconds=EXTRACTION_WALL_SECONDS,
        pdf_cpu_seconds=PDF_CPU_SECONDS,
        pdf_memory_bytes=PDF_MEMORY_BYTES,
    )


def _raise(code: ErrorCode, message: str) -> None:
    raise DocumentImageError(code, message)


def _deadline(limits: _Limits) -> float:
    return time.monotonic() + max(0.0, limits.wall_seconds)


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        _raise("limit", "document image extraction exceeded the time limit")


def _append_warning(items: list[str], message: str) -> None:
    if message not in items:
        items.append(message)


def _inspect_raster(
    data: bytes,
    *,
    limits: _Limits,
    claimed_name: str,
) -> tuple[str, int, int] | None:
    if len(data) > limits.max_single_image_bytes:
        _raise("limit", "an embedded image exceeds the byte limit")
    try:
        with python_warnings.catch_warnings():
            python_warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                image_format = (image.format or "").upper()
                if width <= 0 or height <= 0:
                    _raise("invalid", "an embedded image has invalid dimensions")
                if width * height > limits.max_image_pixels:
                    _raise("limit", "an embedded image exceeds the pixel limit")
                mime = _RASTER_MIME_BY_FORMAT.get(image_format)
                if mime is None:
                    return None
                image.verify()
    except DocumentImageError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise DocumentImageError("limit", "an embedded image exceeds the pixel limit") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        if PurePosixPath(claimed_name.lower()).suffix in _RASTER_SUFFIXES:
            _raise("invalid", "an embedded raster image is malformed")
        return None
    return mime, width, height


class _AssetCollector:
    def __init__(self, limits: _Limits, deadline: float):
        self.limits = limits
        self.deadline = deadline
        self.assets: list[DocumentImageAsset] = []
        self.total_bytes = 0
        self.first_ordinal_by_sha: dict[str, int] = {}

    def add(
        self,
        *,
        data: bytes,
        mime: str,
        width: int,
        height: int,
        source_kind: str,
        page_number: int | None,
        part_path: str | None,
        occurrence_index: int,
    ) -> None:
        _check_deadline(self.deadline)
        if len(self.assets) >= self.limits.max_images:
            _raise("limit", "document contains more images than allowed")
        next_total = self.total_bytes + len(data)
        if next_total > self.limits.max_total_image_bytes:
            _raise("limit", "extracted image bytes exceed the document limit")
        digest = hashlib.sha256(data).hexdigest()
        ordinal = len(self.assets) + 1
        duplicate_of = self.first_ordinal_by_sha.get(digest)
        if duplicate_of is None:
            self.first_ordinal_by_sha[digest] = ordinal
        self.assets.append(
            DocumentImageAsset(
                ordinal=ordinal,
                data=data,
                mime=mime,
                width=width,
                height=height,
                sha256=digest,
                source_kind=source_kind,
                page_number=page_number,
                part_path=part_path,
                occurrence_index=occurrence_index,
                duplicate_of=duplicate_of,
            )
        )
        self.total_bytes = next_total


def _apply_pdf_resource_limits(limits: _Limits) -> None:
    try:
        import resource
    except ImportError:
        return
    if limits.pdf_cpu_seconds > 0:
        resource.setrlimit(
            resource.RLIMIT_CPU,
            (limits.pdf_cpu_seconds, limits.pdf_cpu_seconds + 1),
        )
    if sys.platform.startswith("linux") and limits.pdf_memory_bytes > 0:
        resource.setrlimit(
            resource.RLIMIT_AS,
            (limits.pdf_memory_bytes, limits.pdf_memory_bytes),
        )
    if hasattr(resource, "RLIMIT_NOFILE"):
        _soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        descriptor_limit = min(64, hard if hard >= 0 else 64)
        resource.setrlimit(resource.RLIMIT_NOFILE, (descriptor_limit, descriptor_limit))


def _extract_pdf_untrusted(filename: str, data: bytes, limits: _Limits) -> DocumentExtraction:
    deadline = _deadline(limits)
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            _raise("unsupported", "encrypted PDF files are not supported")
        page_count = len(reader.pages)
    except DocumentImageError:
        raise
    except (PdfReadError, ValueError, TypeError, KeyError, OSError, RecursionError) as exc:
        raise DocumentImageError("invalid", "PDF structure is invalid") from exc
    if page_count > limits.max_pdf_pages:
        _raise("limit", "PDF page count exceeds the limit")

    collector = _AssetCollector(limits, deadline)
    extraction_warnings: list[str] = []
    for page_index in range(page_count):
        _check_deadline(deadline)
        page_number = page_index + 1
        try:
            page = reader.pages[page_index]
            image_keys = list(page.images.keys())
        except (PdfReadError, ValueError, TypeError, KeyError, OSError, AttributeError, RecursionError):
            _append_warning(
                extraction_warnings,
                f"PDF page {page_number} image resources could not be inspected",
            )
            continue
        failed_objects = 0
        for occurrence_index, image_key in enumerate(image_keys, start=1):
            _check_deadline(deadline)
            try:
                image_file = page.images[image_key]
                image_data = bytes(image_file.data)
                inspected = _inspect_raster(
                    image_data,
                    limits=limits,
                    claimed_name=image_file.name,
                )
            except DocumentImageError:
                raise
            except (PdfReadError, ValueError, TypeError, KeyError, OSError, AttributeError, RecursionError):
                failed_objects += 1
                continue
            if inspected is None:
                failed_objects += 1
                continue
            mime, width, height = inspected
            collector.add(
                data=image_data,
                mime=mime,
                width=width,
                height=height,
                source_kind="pdf_embedded",
                page_number=page_number,
                part_path=None,
                occurrence_index=occurrence_index,
            )
        if failed_objects:
            _append_warning(
                extraction_warnings,
                f"PDF page {page_number} contains {failed_objects} unsupported or malformed image object(s)",
            )
    return DocumentExtraction(
        filename=filename,
        page_count=page_count,
        assets=collector.assets,
        warnings=extraction_warnings,
    )


def _pdf_worker(connection, filename: str, data: bytes, limits: _Limits) -> None:
    try:
        _apply_pdf_resource_limits(limits)
        connection.send(("ok", _extract_pdf_untrusted(filename, data, limits)))
    except DocumentImageError as exc:
        connection.send(("error", (exc.code, str(exc))))
    except BaseException:
        try:
            connection.send(("error", ("invalid", "PDF image extraction failed")))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


def _stop_process(process: multiprocessing.Process) -> None:
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


def _extract_pdf(filename: str, data: bytes, limits: _Limits) -> DocumentExtraction:
    context = multiprocessing.get_context("spawn")
    receive_connection, send_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_pdf_worker,
        args=(send_connection, filename, data, limits),
        name="huijian-document-image-pdf",
        daemon=True,
    )
    try:
        process.start()
        send_connection.close()
        if not receive_connection.poll(max(0.001, limits.wall_seconds)):
            _raise("limit", "PDF image extraction exceeded the time limit")
        try:
            status, payload = receive_connection.recv()
        except EOFError as exc:
            raise DocumentImageError("invalid", "PDF image extractor exited unexpectedly") from exc
        if status == "ok":
            return payload
        code, message = payload
        raise DocumentImageError(code, message)
    finally:
        receive_connection.close()
        send_connection.close()
        _stop_process(process)


def _safe_zip_name(name: str) -> str:
    if not name or "\x00" in name or "\\" in name:
        _raise("invalid", "DOCX contains an unsafe archive path")
    if name.startswith("/") or name.startswith("//"):
        _raise("invalid", "DOCX contains an unsafe archive path")
    parts = PurePosixPath(name).parts
    if any(part in {"", ".", ".."} for part in parts):
        _raise("invalid", "DOCX contains an unsafe archive path")
    if parts and ":" in parts[0]:
        _raise("invalid", "DOCX contains an unsafe archive path")
    return "/".join(parts)


def _read_zip_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    byte_limit: int,
    deadline: float,
) -> bytes:
    _check_deadline(deadline)
    try:
        with archive.open(info, "r") as source:
            data = source.read(byte_limit + 1)
    except (RuntimeError, NotImplementedError, OSError, zipfile.BadZipFile) as exc:
        raise DocumentImageError("invalid", "DOCX archive member could not be read") from exc
    if len(data) > byte_limit:
        _raise("limit", "DOCX archive member exceeds the read limit")
    return data


def _parse_xml(data: bytes, *, deadline: float) -> ElementTree.Element:
    _check_deadline(deadline)
    if len(data) > MAX_XML_BYTES:
        _raise("limit", "DOCX XML part exceeds the size limit")
    upper_prefix = data[:4096].upper()
    if b"<!DOCTYPE" in upper_prefix or b"<!ENTITY" in upper_prefix:
        _raise("invalid", "DOCX XML declarations are unsafe")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise DocumentImageError("invalid", "DOCX contains malformed XML") from exc
    for index, _element in enumerate(root.iter(), start=1):
        if index > MAX_XML_ELEMENTS:
            _raise("limit", "DOCX XML element count exceeds the limit")
    return root


def _source_part_for_relationships(rels_path: str) -> str | None:
    path = PurePosixPath(rels_path)
    if path == PurePosixPath("_rels/.rels"):
        return None
    if path.parent.name != "_rels" or not path.name.endswith(".rels"):
        _raise("invalid", "DOCX contains an invalid relationships path")
    source_name = path.name[:-5]
    source_parent = path.parent.parent
    return str(source_parent / source_name)


def _resolve_image_target(source_part: str, target: str) -> str:
    decoded = unquote(target)
    split = urlsplit(decoded)
    if split.scheme or split.netloc or split.query or split.fragment:
        _raise("invalid", "DOCX image relationship target is invalid")
    if "\\" in decoded or "\x00" in decoded:
        _raise("invalid", "DOCX image relationship target is unsafe")
    target_path = split.path
    if any(part == ".." for part in PurePosixPath(target_path).parts):
        _raise("invalid", "DOCX image relationship contains path traversal")
    if target_path.startswith("/"):
        resolved = posixpath.normpath(target_path.lstrip("/"))
    else:
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target_path))
    if not resolved.startswith("word/media/"):
        _raise("invalid", "DOCX image relationship points outside word/media")
    return resolved


def _source_kind(part_path: str | None) -> str:
    if part_path == "word/document.xml":
        return "docx_body"
    basename = PurePosixPath(part_path or "").name.lower()
    if basename.startswith("header"):
        return "docx_header"
    if basename.startswith("footer"):
        return "docx_footer"
    return "docx_part"


def _part_sort_key(part_path: str) -> tuple[int, str]:
    if part_path == "word/document.xml":
        return (0, part_path)
    basename = PurePosixPath(part_path).name.lower()
    if basename.startswith("header"):
        return (1, part_path)
    if basename.startswith("footer"):
        return (2, part_path)
    return (3, part_path)


def _audit_docx(
    archive: zipfile.ZipFile,
    *,
    deadline: float,
) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > MAX_DOCX_MEMBERS:
        _raise("limit", "DOCX archive contains too many members")
    normalized: dict[str, zipfile.ZipInfo] = {}
    normalized_casefold: set[str] = set()
    expanded_bytes = 0
    for info in infos:
        _check_deadline(deadline)
        name = _safe_zip_name(info.filename.rstrip("/"))
        folded = name.casefold()
        if folded in normalized_casefold:
            _raise("invalid", "DOCX archive contains duplicate member names")
        normalized_casefold.add(folded)
        normalized[name] = info
        if info.flag_bits & 0x1:
            _raise("unsupported", "encrypted DOCX members are not supported")
        if info.compress_type not in _ALLOWED_ZIP_COMPRESSION:
            _raise("unsupported", "DOCX uses an unsupported ZIP compression method")
        if info.file_size < 0 or info.compress_size < 0:
            _raise("invalid", "DOCX archive contains invalid member sizes")
        if info.file_size > MAX_DOCX_MEMBER_BYTES:
            _raise("limit", "DOCX archive member exceeds the size limit")
        expanded_bytes += info.file_size
        if expanded_bytes > MAX_DOCX_EXPANDED_BYTES:
            _raise("limit", "DOCX expanded size exceeds the limit")
        if info.file_size:
            ratio = info.file_size / max(1, info.compress_size)
            if ratio > MAX_DOCX_COMPRESSION_RATIO:
                _raise("limit", "DOCX compression ratio exceeds the limit")

        lower_name = name.lower()
        if lower_name.endswith("vbaproject.bin") or "/activex/" in f"/{lower_name}":
            _raise("unsupported", "macro-enabled DOCX content is not supported")
        if lower_name.startswith("word/embeddings/"):
            _raise("unsupported", "embedded OLE objects are not supported")

    required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
    if not required.issubset(normalized):
        _raise("invalid", "file is not a valid DOCX container")
    return normalized


def _extract_docx(filename: str, data: bytes, limits: _Limits) -> DocumentExtraction:
    deadline = _deadline(limits)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
    except zipfile.BadZipFile as exc:
        raise DocumentImageError("invalid", "DOCX ZIP container is invalid") from exc

    with archive:
        members = _audit_docx(archive, deadline=deadline)
        content_types_data = _read_zip_member(
            archive,
            members["[Content_Types].xml"],
            byte_limit=MAX_XML_BYTES,
            deadline=deadline,
        )
        lowered_content_types = content_types_data.lower()
        if b"macroenabled" in lowered_content_types or b"vba" in lowered_content_types:
            _raise("unsupported", "macro-enabled DOCX content is not supported")
        if b"oleobject" in lowered_content_types:
            _raise("unsupported", "embedded OLE objects are not supported")
        _parse_xml(content_types_data, deadline=deadline)

        relationships_by_part: dict[str, dict[str, str]] = {}
        for rels_path in sorted(name for name in members if name.endswith(".rels")):
            rels_data = _read_zip_member(
                archive,
                members[rels_path],
                byte_limit=MAX_XML_BYTES,
                deadline=deadline,
            )
            rels_root = _parse_xml(rels_data, deadline=deadline)
            source_part = _source_part_for_relationships(rels_path)
            image_relationships: dict[str, str] = {}
            for relationship in rels_root.iter():
                if relationship.tag.rsplit("}", 1)[-1] != "Relationship":
                    continue
                target_mode = relationship.attrib.get("TargetMode", "")
                relationship_type = relationship.attrib.get("Type", "")
                if target_mode.lower() == "external":
                    _raise("unsupported", "external DOCX relationships are not supported")
                lowered_type = relationship_type.lower()
                if lowered_type.endswith(("/oleobject", "/package")):
                    _raise("unsupported", "embedded OLE objects are not supported")
                if source_part is None or not relationship_type.endswith(_IMAGE_RELATIONSHIP_SUFFIX):
                    continue
                relationship_id = relationship.attrib.get("Id", "")
                target = relationship.attrib.get("Target", "")
                if not relationship_id or not target:
                    _raise("invalid", "DOCX image relationship is incomplete")
                resolved = _resolve_image_target(source_part, target)
                if resolved not in members:
                    _raise("invalid", "DOCX image relationship target is missing")
                image_relationships[relationship_id] = resolved
            if source_part is not None and image_relationships:
                if source_part not in members:
                    _raise("invalid", "DOCX relationship source part is missing")
                relationships_by_part[source_part] = image_relationships

        media_paths = sorted(name for name in members if name.startswith("word/media/"))
        media_data: dict[str, bytes] = {}
        media_info: dict[str, tuple[str, int, int] | None] = {}
        extraction_warnings: list[str] = []
        for media_path in media_paths:
            raw = _read_zip_member(
                archive,
                members[media_path],
                byte_limit=limits.max_single_image_bytes,
                deadline=deadline,
            )
            inspected = _inspect_raster(raw, limits=limits, claimed_name=media_path)
            media_data[media_path] = raw
            media_info[media_path] = inspected
            if inspected is None:
                _append_warning(
                    extraction_warnings,
                    f"DOCX media {PurePosixPath(media_path).name} is not a supported raster image",
                )

        collector = _AssetCollector(limits, deadline)
        referenced_media: set[str] = set()
        for part_path in sorted(relationships_by_part, key=_part_sort_key):
            part_xml = _read_zip_member(
                archive,
                members[part_path],
                byte_limit=MAX_XML_BYTES,
                deadline=deadline,
            )
            part_root = _parse_xml(part_xml, deadline=deadline)
            image_relationships = relationships_by_part[part_path]
            occurrence_index = 0
            for element in part_root.iter():
                _check_deadline(deadline)
                for attribute_name, relationship_id in element.attrib.items():
                    namespace, _, local_name = attribute_name.rpartition("}")
                    if namespace.lstrip("{") != _RELATIONSHIPS_NAMESPACE:
                        continue
                    if local_name not in {"embed", "id", "link"}:
                        continue
                    media_path = image_relationships.get(relationship_id)
                    if media_path is None:
                        continue
                    occurrence_index += 1
                    referenced_media.add(media_path)
                    inspected = media_info[media_path]
                    if inspected is None:
                        continue
                    mime, width, height = inspected
                    collector.add(
                        data=media_data[media_path],
                        mime=mime,
                        width=width,
                        height=height,
                        source_kind=_source_kind(part_path),
                        page_number=None,
                        part_path=part_path,
                        occurrence_index=occurrence_index,
                    )

        unreferenced_index = 0
        for media_path in media_paths:
            if media_path in referenced_media:
                continue
            inspected = media_info[media_path]
            if inspected is None:
                continue
            unreferenced_index += 1
            mime, width, height = inspected
            collector.add(
                data=media_data[media_path],
                mime=mime,
                width=width,
                height=height,
                source_kind="docx_media",
                page_number=None,
                part_path=None,
                occurrence_index=unreferenced_index,
            )
            _append_warning(
                extraction_warnings,
                "DOCX contains unreferenced raster media; it was retained",
            )

    return DocumentExtraction(
        filename=filename,
        page_count=None,
        assets=collector.assets,
        warnings=extraction_warnings,
    )


def extract_document_images(filename: str, data: bytes) -> DocumentExtraction:
    """Extract raster image occurrences from a PDF or DOCX under explicit limits."""

    if not isinstance(filename, str) or not filename.strip():
        _raise("invalid", "filename is required")
    extension = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    if extension in {".doc", ".docm"}:
        _raise("unsupported", f"{extension} files are not supported")
    if extension not in {".pdf", ".docx"}:
        _raise("unsupported", "only PDF and DOCX image extraction is supported")
    if not isinstance(data, bytes) or not data:
        _raise("invalid", "document data must be non-empty bytes")
    if len(data) > MAX_INPUT_BYTES:
        _raise("limit", "document input exceeds the byte limit")

    limits = _limits()
    if extension == ".pdf":
        if data.find(b"%PDF-", 0, min(len(data), 1024)) < 0:
            _raise("invalid", "PDF magic header is missing")
        return _extract_pdf(filename, data, limits)

    if data[:4] not in {b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"}:
        _raise("invalid", "DOCX ZIP magic header is missing")
    return _extract_docx(filename, data, limits)

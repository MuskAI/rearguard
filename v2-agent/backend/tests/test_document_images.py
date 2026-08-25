from __future__ import annotations

import io
from pathlib import Path
import sys
import zipfile

from PIL import Image
from pypdf import PdfReader, PdfWriter
import pytest
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import document_images
from app.document_images import DocumentImageError, extract_document_images


_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def _image_bytes(image_format: str, color: tuple[int, int, int], size=(19, 13)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, image_format)
    return output.getvalue()


def _pdf_with_image(image_format: str) -> bytes:
    image = _image_bytes(image_format, (31, 87, 191))
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=(240, 180))
    document.drawImage(ImageReader(io.BytesIO(image)), 20, 20, width=190, height=130)
    document.save()
    return output.getvalue()


def _drawing_xml(*relationship_ids: str) -> str:
    refs = "".join(f'<a:blip r:embed="{relationship_id}"/>' for relationship_id in relationship_ids)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<w:body>{refs}</w:body></w:document>"
    )


def _part_xml(root_name: str, *relationship_ids: str) -> str:
    refs = "".join(f'<a:blip r:embed="{relationship_id}"/>' for relationship_id in relationship_ids)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:{root_name} xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"{refs}</w:{root_name}>"
    )


def _image_rels(*targets: str, extra: str = "") -> str:
    relationships = "".join(
        f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="{target}"/>'
        for index, target in enumerate(targets, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{relationships}{extra}</Relationships>"
    )


def _docx(entries: dict[str, bytes | str], *, compression=zipfile.ZIP_DEFLATED) -> bytes:
    base: dict[str, bytes | str] = {
        "[Content_Types].xml": _CONTENT_TYPES,
        "_rels/.rels": _ROOT_RELS,
        "word/document.xml": _drawing_xml(),
    }
    base.update(entries)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, value in base.items():
            archive.writestr(name, value)
    return output.getvalue()


def _assert_error(code: str, filename: str, data: bytes) -> DocumentImageError:
    with pytest.raises(DocumentImageError) as exc_info:
        extract_document_images(filename, data)
    assert exc_info.value.code == code
    return exc_info.value


def test_public_api_and_pdf_jpeg_extraction():
    extracted = extract_document_images("photo.pdf", _pdf_with_image("JPEG"))

    assert extracted.filename == "photo.pdf"
    assert extracted.page_count == 1
    assert extracted.warnings == []
    assert len(extracted.assets) == 1
    asset = extracted.assets[0]
    assert asset.ordinal == 1
    assert asset.mime == "image/jpeg"
    assert (asset.width, asset.height) == (19, 13)
    assert asset.source_kind == "pdf_embedded"
    assert asset.page_number == 1
    assert asset.part_path is None
    assert asset.occurrence_index == 1
    assert asset.duplicate_of is None
    assert len(asset.sha256) == 64
    assert asset.data.startswith(b"\xff\xd8")


def test_pdf_flate_image_is_returned_as_png():
    payload = _pdf_with_image("PNG")
    page = PdfReader(io.BytesIO(payload)).pages[0]
    filters = [str(value) for value in page["/Resources"]["/XObject"].get_object().values()]
    assert any("FlateDecode" in value for value in filters)

    extracted = extract_document_images("flate.pdf", payload)

    assert len(extracted.assets) == 1
    assert extracted.assets[0].mime == "image/png"
    assert extracted.assets[0].data.startswith(b"\x89PNG\r\n\x1a\n")


def test_pdf_alpha_image_retains_soft_mask_relationship_metadata():
    image = io.BytesIO()
    Image.new("RGBA", (48, 36), (20, 90, 180, 96)).save(image, "PNG")
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=(240, 180))
    document.drawImage(
        ImageReader(io.BytesIO(image.getvalue())),
        20,
        20,
        width=190,
        height=130,
        mask="auto",
    )
    document.save()

    extracted = extract_document_images("alpha.pdf", output.getvalue())

    assert len(extracted.assets) == 1
    asset = extracted.assets[0]
    assert asset.pdf_object_id is not None
    assert asset.pdf_smask_object_id is not None
    assert asset.pdf_is_soft_mask is False
    assert asset.pdf_is_image_mask is False
    assert asset.pdf_color_space == "/DeviceRGB"
    assert asset.pdf_bits_per_component == 8


def test_pdf_rejects_malformed_encrypted_and_oversized_inputs(monkeypatch):
    _assert_error("invalid", "broken.pdf", b"%PDF-1.7\nnot a PDF")

    reader = PdfReader(io.BytesIO(_pdf_with_image("JPEG")))
    writer = PdfWriter()
    writer.add_page(reader.pages[0])
    writer.encrypt("secret")
    encrypted = io.BytesIO()
    writer.write(encrypted)
    _assert_error("unsupported", "private.pdf", encrypted.getvalue())

    monkeypatch.setattr(document_images, "MAX_INPUT_BYTES", 32)
    _assert_error("limit", "large.pdf", _pdf_with_image("JPEG"))


def test_pdf_page_and_image_count_limits(monkeypatch):
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=(120, 120))
    image = _image_bytes("PNG", (1, 2, 3), size=(5, 5))
    for _index in range(2):
        document.drawImage(ImageReader(io.BytesIO(image)), 10, 10, width=80, height=80)
        document.showPage()
    document.save()

    monkeypatch.setattr(document_images, "MAX_PDF_PAGES", 1)
    _assert_error("limit", "pages.pdf", output.getvalue())

    monkeypatch.setattr(document_images, "MAX_PDF_PAGES", 10)
    monkeypatch.setattr(document_images, "MAX_IMAGES", 1)
    _assert_error("limit", "images.pdf", output.getvalue())


def test_docx_extracts_body_header_footer_and_keeps_duplicate_occurrences():
    shared = _image_bytes("PNG", (12, 100, 220))
    other = _image_bytes("PNG", (240, 80, 10))
    payload = _docx(
        {
            "word/document.xml": _drawing_xml("rId1", "rId1"),
            "word/_rels/document.xml.rels": _image_rels("media/shared.png"),
            "word/header1.xml": _part_xml("hdr", "rId1"),
            "word/_rels/header1.xml.rels": _image_rels("media/shared.png"),
            "word/footer1.xml": _part_xml("ftr", "rId1"),
            "word/_rels/footer1.xml.rels": _image_rels("media/other.png"),
            "word/media/shared.png": shared,
            "word/media/other.png": other,
        }
    )

    extracted = extract_document_images("evidence.docx", payload)

    assert extracted.filename == "evidence.docx"
    assert extracted.page_count is None
    assert extracted.warnings == []
    assert [asset.source_kind for asset in extracted.assets] == [
        "docx_body",
        "docx_body",
        "docx_header",
        "docx_footer",
    ]
    assert [asset.part_path for asset in extracted.assets] == [
        "word/document.xml",
        "word/document.xml",
        "word/header1.xml",
        "word/footer1.xml",
    ]
    assert [asset.occurrence_index for asset in extracted.assets] == [1, 2, 1, 1]
    assert [asset.duplicate_of for asset in extracted.assets] == [None, 1, 1, None]


def test_docx_retains_unreferenced_media_with_warning():
    payload = _docx({"word/media/orphan.png": _image_bytes("PNG", (4, 5, 6))})

    extracted = extract_document_images("orphan.docx", payload)

    assert len(extracted.assets) == 1
    assert extracted.assets[0].source_kind == "docx_media"
    assert extracted.assets[0].part_path is None
    assert extracted.warnings == ["DOCX contains unreferenced raster media; it was retained"]


def test_docx_rejects_zip_bomb_path_traversal_macro_ole_and_external_rel():
    bomb = _docx({"word/media/bomb.png": b"A" * 2_000_000})
    _assert_error("limit", "bomb.docx", bomb)

    traversal = _docx({"../outside.png": _image_bytes("PNG", (1, 1, 1))})
    _assert_error("invalid", "traversal.docx", traversal)

    macro = _docx({"word/vbaProject.bin": b"macro"})
    _assert_error("unsupported", "macro.docx", macro)

    ole = _docx({"word/embeddings/oleObject1.bin": b"ole"})
    _assert_error("unsupported", "ole.docx", ole)

    external = (
        '<Relationship Id="rExternal" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
        'Target="https://example.com/tracker" TargetMode="External"/>'
    )
    external_docx = _docx(
        {"word/_rels/document.xml.rels": _image_rels(extra=external)}
    )
    _assert_error("unsupported", "external.docx", external_docx)


def test_docx_enforces_image_pixel_and_occurrence_limits(monkeypatch):
    image = _image_bytes("PNG", (30, 40, 50), size=(20, 20))
    payload = _docx(
        {
            "word/document.xml": _drawing_xml("rId1", "rId1"),
            "word/_rels/document.xml.rels": _image_rels("media/image.png"),
            "word/media/image.png": image,
        }
    )

    monkeypatch.setattr(document_images, "MAX_IMAGE_PIXELS", 100)
    _assert_error("limit", "pixels.docx", payload)

    monkeypatch.setattr(document_images, "MAX_IMAGE_PIXELS", 1_000)
    monkeypatch.setattr(document_images, "MAX_IMAGES", 1)
    _assert_error("limit", "occurrences.docx", payload)


@pytest.mark.parametrize("filename", ["legacy.doc", "macro.docm", "image.png"])
def test_unsupported_extensions_have_stable_error_code(filename):
    _assert_error("unsupported", filename, b"not used")


def test_container_magic_and_relationship_traversal_are_rejected():
    _assert_error("invalid", "wrong.pdf", _docx({}))
    _assert_error("invalid", "wrong.docx", _pdf_with_image("PNG"))

    payload = _docx(
        {
            "word/document.xml": _drawing_xml("rId1"),
            "word/_rels/document.xml.rels": _image_rels("../media/image.png"),
            "word/media/image.png": _image_bytes("PNG", (1, 2, 3)),
        }
    )
    _assert_error("invalid", "rels-traversal.docx", payload)

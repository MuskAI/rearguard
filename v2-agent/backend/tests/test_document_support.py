from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import io
import sys
import time
import zipfile

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import detector, document_utils  # noqa: E402
import pytest


def _docx_bytes(text: str) -> bytes:
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return buf.getvalue()


def _pdf_bytes(*pages: str) -> bytes:
    output = io.BytesIO()
    document = canvas.Canvas(output)
    for text in pages:
        document.drawString(72, 760, text)
        document.showPage()
    document.save()
    return output.getvalue()


def test_docx_document_uses_extracted_text_for_vlm(monkeypatch):
    captured = {}

    def fake_vlm(text: str):
        captured["text"] = text
        return {
            "verdict": "real",
            "confidence": 0.22,
            "dimensions": [{"key": "aigc_text", "label": "AIGC文本检测", "score": 0.22, "result": "像人工撰写"}],
            "regions": [],
            "explanation": "文本表达自然。",
            "modelVersion": "stub",
            "source": "vlm",
        }

    monkeypatch.setattr(detector, "analyze_text_vlm", fake_vlm)

    result = detector.analyze("document", "sample.docx", _docx_bytes("这是 docx 正文内容。"))

    assert captured["text"] == "这是 docx 正文内容。"
    assert result["source"] == "vlm"
    assert "已从 DOCX 提取正文" in result["explanation"]


def test_pdf_document_uses_extracted_text_for_vlm(monkeypatch):
    captured = {}

    def fake_vlm(text: str):
        captured["text"] = text
        return {
            "verdict": "real",
            "confidence": 0.18,
            "dimensions": [],
            "regions": [],
            "explanation": "Text appears human-authored.",
            "modelVersion": "stub",
            "source": "vlm",
        }

    monkeypatch.setattr(detector, "analyze_text_vlm", fake_vlm)
    result = detector.analyze("document", "sample.pdf", _pdf_bytes("Human-authored PDF content."))

    assert "Human-authored PDF content." in captured["text"]
    assert result["source"] == "vlm"
    assert "已从 PDF 提取 1/1 页正文" in result["explanation"]


def test_malformed_pdf_returns_unavailable_without_mock_result():
    with pytest.raises(detector.DetectionUnavailableError) as exc_info:
        detector.analyze("document", "sample.pdf", b"%PDF-1.4 fake document bytes")

    assert "PDF 文件解析失败" in str(exc_info.value)
    assert "未生成真实性结论" in str(exc_info.value)


def test_encrypted_pdf_is_rejected_without_attempting_decryption():
    reader = PdfReader(io.BytesIO(_pdf_bytes("private")))
    writer = PdfWriter()
    writer.add_page(reader.pages[0])
    writer.encrypt("secret")
    output = io.BytesIO()
    writer.write(output)

    extracted = document_utils.extract_text("encrypted.pdf", output.getvalue())

    assert extracted.text == ""
    assert extracted.note == "加密 PDF 暂不支持解析"


def test_pdf_page_count_is_bounded_and_disclosed(monkeypatch):
    monkeypatch.setattr(document_utils, "MAX_PDF_PAGES", 1)

    extracted = document_utils.extract_text("two-pages.pdf", _pdf_bytes("first page", "second page"))

    assert "first page" in extracted.text
    assert "second page" not in extracted.text
    assert extracted.note == "已从 PDF 提取 1/2 页正文（达到安全解析上限，正文已截断）"


def test_pdf_highly_compressible_text_has_bounded_output():
    extracted = document_utils.extract_text("compressed.pdf", _pdf_bytes("A" * 200_000))

    assert 0 < len(extracted.text) <= document_utils.MAX_PDF_PAGE_CHARACTERS
    assert "正文已截断" in extracted.note


def test_pdf_parser_timeout_terminates_isolated_process(monkeypatch):
    monkeypatch.setattr(document_utils, "PDF_PARSE_WALL_SECONDS", 0)
    started = time.monotonic()

    extracted = document_utils.extract_text("timeout.pdf", _pdf_bytes("bounded timeout"))

    assert time.monotonic() - started < 2
    assert extracted.text == ""
    assert extracted.note == "PDF 解析超时，已安全终止"


def test_concurrent_malformed_pdfs_finish_without_blocking_worker_threads():
    payload = b"%PDF-1.4\n" + (b"broken-page-tree" * 10_000)
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _index: document_utils.extract_text("broken.pdf", payload), range(4)))

    assert time.monotonic() - started < 12
    assert all(result.text == "" for result in results)
    assert all(result.note == "PDF 文件解析失败" for result in results)


def test_analysis_excerpt_samples_beginning_middle_and_end():
    text = "A" * 5_000 + "MIDDLE" + "B" * 5_000 + "ENDING"

    excerpt = document_utils.analysis_excerpt(text, 4_000)

    assert len(excerpt) <= 4_000
    assert excerpt.startswith("A")
    assert "MIDDLE" in excerpt
    assert excerpt.endswith("ENDING")


def test_docx_zip_bomb_is_rejected_before_expansion():
    xml = b"A" * (document_utils.MAX_DOCX_DOCUMENT_XML_BYTES + 1)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)

    extracted = document_utils.extract_text("bomb.docx", output.getvalue())

    assert extracted.text == ""
    assert extracted.note == "DOCX 文件超出安全解析限制"


def test_docx_member_count_has_a_hard_limit():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("word/document.xml", "<document />")
        for index in range(document_utils.MAX_DOCX_MEMBERS):
            archive.writestr(f"word/extra-{index}.xml", "x")

    extracted = document_utils.extract_text("many-members.docx", output.getvalue())

    assert extracted.text == ""
    assert extracted.note == "DOCX 文件超出安全解析限制"


def test_image_model_failure_never_returns_a_mock_result(monkeypatch):
    monkeypatch.setattr(detector, "analyze_image_vlm", lambda _data: None)

    with pytest.raises(detector.DetectionUnavailableError, match="未生成真实性结论"):
        detector.analyze("image", "sample.png", b"not-an-image")


def test_unsupported_media_never_returns_a_mock_result():
    with pytest.raises(detector.DetectionUnavailableError, match="尚未部署"):
        detector.analyze("video", "sample.mp4", b"video")

from pathlib import Path
import io
import sys
import zipfile


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


def test_pdf_document_without_extractable_text_returns_unavailable():
    with pytest.raises(detector.DetectionUnavailableError) as exc_info:
        detector.analyze("document", "sample.pdf", b"%PDF-1.4 fake document bytes")

    assert "当前未支持 PDF 正文抽取" in str(exc_info.value)
    assert "未生成真实性结论" in str(exc_info.value)


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

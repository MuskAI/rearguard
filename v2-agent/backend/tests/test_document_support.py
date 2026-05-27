from pathlib import Path
import io
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import detector  # noqa: E402


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


def test_pdf_document_falls_back_with_clear_note():
    result = detector.analyze("document", "sample.pdf", b"%PDF-1.4 fake document bytes")

    assert result["source"] == "mock"
    assert "当前未支持 PDF 正文抽取" in result["explanation"]

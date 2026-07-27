#!/usr/bin/env python3
import json
import os
from pathlib import Path


ROOT = Path(os.environ.get("AIGC_REPO_ROOT") or Path(__file__).resolve().parents[1]).resolve()
MANIFEST = Path(
    os.environ.get("AIGC_PAPER_MANIFEST")
    or ROOT / "reports/ppt-work/paper_manifest.json"
).resolve()
OUT_DIR = Path(
    os.environ.get("AIGC_PAPER_TEXT_DIR")
    or ROOT / "reports/ppt-work/text"
).resolve()


def extract_pdf_text(path: Path, max_pages: int | None = None) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    chunks = []
    limit = len(reader.pages) if max_pages is None else min(len(reader.pages), max_pages)
    for i in range(limit):
        chunks.append(f"\n\n--- page {i + 1} ---\n")
        chunks.append(reader.pages[i].extract_text() or "")
    return "".join(chunks)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    index = []
    for item in manifest["items"]:
        pdf_path = item.get("pdfPath")
        if not pdf_path:
            index.append(
                {
                    "index": item["index"],
                    "title": item["title"],
                    "textPath": "",
                    "status": "missing_pdf",
                }
            )
            continue
        src = Path(pdf_path)
        out = OUT_DIR / f"{item['index']:02d}.txt"
        try:
            text = extract_pdf_text(src)
            text = text.encode("utf-8", "replace").decode("utf-8")
            out.write_text(text, encoding="utf-8")
            index.append(
                {
                    "index": item["index"],
                    "title": item["title"],
                    "textPath": str(out),
                    "chars": len(text),
                    "status": "ok",
                }
            )
        except Exception as exc:
            index.append(
                {
                    "index": item["index"],
                    "title": item["title"],
                    "textPath": "",
                    "status": "error",
                    "error": repr(exc),
                }
            )
    (OUT_DIR / "text_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ok = sum(1 for row in index if row["status"] == "ok")
    print(json.dumps({"ok": ok, "total": len(index), "out": str(OUT_DIR)}))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

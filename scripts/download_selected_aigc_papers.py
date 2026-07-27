#!/usr/bin/env python3
import gzip
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

try:
    from lxml import html
except Exception:
    html = None


ROOT = Path(os.environ.get("AIGC_REPO_ROOT") or Path(__file__).resolve().parents[1]).resolve()
MANIFEST = Path(
    os.environ.get("AIGC_PAPER_MANIFEST")
    or ROOT / "reports/aigc-selected-pdf-download-manifest-2026-06-19.json"
).resolve()
CACHE = Path(
    os.environ.get("AIGC_PAPER_CACHE") or ROOT / "reports/output/skim-cache"
).resolve()
DEST = Path(
    os.environ.get("AIGC_PAPER_OUTPUT_DIR")
    or ROOT / "reports/output/selected-papers"
).resolve()
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36"

MANUAL_PDF_URLS = {
    "can we build a monolithic model for fake image detection sica": "https://arxiv.org/pdf/2602.06676",
    "dgs net distillation guided gradient surgery for clip fine tuning in ai generated image detection": "https://arxiv.org/pdf/2511.13108",
    "dissect and prune enhancing robustness in ai generated image detection": "https://arxiv.org/pdf/2606.10309",
    "dna uncovering universal latent forgery knowledge": "https://arxiv.org/pdf/2601.22515",
    "forensicconcept transferable forensic concepts for aigi detection": "https://arxiv.org/pdf/2606.07034",
    "genshield unified detection and artifact correction for ai generated images": "https://arxiv.org/pdf/2605.16122",
    "omnivl guard towards unified vision language forgery detection and grounding via balanced rl": "https://arxiv.org/pdf/2602.10687",
    "order within chaos capturing intrinsic energy anomalies for ai manipulated image forgery localization": "https://arxiv.org/pdf/2606.02178",
    "pgc peak guided calibration for generalizable ai generated image detection": "https://arxiv.org/pdf/2605.21207",
    "tranx adapter bridging artifacts and semantics within mllms for robust ai generated image detection": "https://arxiv.org/pdf/2602.21716",
    "where detectors fail probing generative space for generalizable ai generated image detection": "https://arxiv.org/pdf/2605.24906",
    "automated in the wild data collection for continual ai generated image detection": "https://arxiv.org/pdf/2605.02567",
}


def safe_read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def cache_pdf_path(url):
    return CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".pdfbin")


def is_pdf(data, content_type=""):
    head = data[:16].lstrip()
    return head.startswith(b"%PDF") or "application/pdf" in (content_type or "").lower()


def fetch(url, timeout=35, max_bytes=80_000_000):
    req = Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/pdf,application/json,*/*",
        },
    )
    with urlopen(req, timeout=timeout) as r:
        data = r.read(max_bytes)
        encoding = r.headers.get("content-encoding", "")
        if encoding.lower() == "gzip" or data.startswith(b"\x1f\x8b"):
            data = gzip.decompress(data)
        return data, r.headers.get("content-type", ""), r.geturl()


def decode_html(data):
    return data.decode("utf-8", "ignore")


def is_ojs_galley_view(url):
    return "ojs.aaai.org" in url and re.search(r"/article/view/\d+/\d+(?:$|[?#])", url)


def normalize_ojs_pdf_url(url):
    if is_ojs_galley_view(url):
        return url.replace("/article/view/", "/article/download/", 1)
    return url


def looks_like_pdf_candidate(url, label="", attrs=""):
    blob = (url + " " + label + " " + attrs).lower()
    parsed = urlparse(urljoin("https://example.com", url))
    path = parsed.path.lower()
    if path.endswith(".pdf") or ".pdf" in parsed.query.lower():
        return True
    if "openreview.net/pdf" in blob:
        return True
    if "/article/download/" in path:
        return True
    if is_ojs_galley_view(url):
        return True
    return False


def pdf_links_from_html(page_url, data):
    text = decode_html(data)
    links = []
    if html is not None:
        try:
            doc = html.fromstring(text)
            for meta in doc.xpath("//meta"):
                name = (meta.get("name") or meta.get("property") or "").lower()
                content = meta.get("content") or ""
                if name == "citation_pdf_url" and content:
                    links.append(urljoin(page_url, content))
            for a in doc.xpath("//a"):
                href = a.get("href") or ""
                label = " ".join(a.text_content().split()).lower()
                attrs = " ".join(
                    str(a.get(k) or "") for k in ("class", "type", "aria-label", "title")
                ).lower()
                candidate = urljoin(page_url, href)
                if looks_like_pdf_candidate(candidate, label, attrs):
                    links.append(normalize_ojs_pdf_url(candidate))
        except Exception:
            pass
    for content in re.findall(
        r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
        text,
        flags=re.I,
    ):
        links.append(urljoin(page_url, content))
    for href in re.findall(r'href=["\']([^"\']+)["\']', text, flags=re.I):
        candidate = urljoin(page_url, href)
        if looks_like_pdf_candidate(candidate):
            links.append(normalize_ojs_pdf_url(candidate))
    return list(dict.fromkeys(x for x in links if x and not x.startswith("javascript:")))


def openreview_pdf_by_title(title):
    api = "https://api2.openreview.net/notes?content.title=" + quote(title)
    try:
        data, ctype, final_url = fetch(api, timeout=25, max_bytes=4_000_000)
        payload = json.loads(data.decode("utf-8", "ignore"))
        notes = payload.get("notes") or []
        if not notes:
            return ""
        # Prefer exact normalized title if several notes are returned.
        target = normalize(title)
        for note in notes:
            content = note.get("content") or {}
            note_title = content.get("title")
            if isinstance(note_title, dict):
                note_title = note_title.get("value")
            note_id = note.get("id")
            if note_id and normalize(note_title or "") == target:
                return "https://openreview.net/pdf?id=" + note_id
        note_id = notes[0].get("id")
        return "https://openreview.net/pdf?id=" + note_id if note_id else ""
    except Exception:
        return ""


def normalize(text):
    text = (text or "").lower().replace("&", "and")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def candidate_pdf_urls(item):
    urls = []
    if item.get("pdf_url"):
        urls.append(item["pdf_url"])
    page = item.get("page_url") or ""
    title = item.get("requested_title") or item.get("matched_title") or ""
    manual = MANUAL_PDF_URLS.get(normalize(title))
    if manual:
        urls.append(manual)
    if page:
        if "openreview.net/forum" in page:
            parsed = urlparse(page)
            m = re.search(r"[?&]id=([^&]+)", parsed.query)
            if m:
                urls.append("https://openreview.net/pdf?id=" + m.group(1))
        if "dl.acm.org/doi/" in page:
            doi = page.split("/doi/", 1)[-1]
            urls.append("https://dl.acm.org/doi/pdf/" + doi)
            urls.append("https://dl.acm.org/doi/epdf/" + doi)
        if "icml.cc/virtual/" in page:
            found = openreview_pdf_by_title(title)
            if found:
                urls.append(found)
    return list(dict.fromkeys(urls))


def resolve_from_page(item):
    page = item.get("page_url") or ""
    if not page:
        return []
    try:
        data, ctype, final_url = fetch(page, timeout=30, max_bytes=8_000_000)
        return pdf_links_from_html(final_url or page, data)
    except Exception:
        return []


def save_pdf_from_url(url, dest):
    cached = cache_pdf_path(url)
    if cached.exists() and cached.stat().st_size > 1000:
        data = cached.read_bytes()
        if is_pdf(data):
            shutil.copy2(cached, dest)
            return "copied-cache"
    data, ctype, final_url = fetch(url)
    if not is_pdf(data, ctype):
        raise ValueError(f"not a pdf: content_type={ctype} final_url={final_url} head={data[:40]!r}")
    dest.write_bytes(data)
    cached.write_bytes(data)
    return "downloaded"


def main():
    manifest = safe_read_json(MANIFEST)
    CACHE.mkdir(parents=True, exist_ok=True)
    DEST.mkdir(parents=True, exist_ok=True)
    results = []
    for item in manifest["items"]:
        out = {
            "requested_index": item.get("requested_index"),
            "title": item.get("requested_title"),
            "filename": item.get("filename"),
            "status": "pending",
            "pdf_url": "",
            "error": "",
        }
        if not item.get("matched"):
            out["status"] = "unmatched"
            results.append(out)
            continue
        dest = DEST / item["filename"]
        if dest.exists() and dest.stat().st_size > 1000:
            out.update(status="exists", pdf_url=item.get("pdf_url", ""))
            results.append(out)
            continue
        candidates = candidate_pdf_urls(item)
        candidates.extend(resolve_from_page(item))
        seen = set()
        ok = False
        last_error = ""
        for url in candidates:
            if not url or url in seen:
                continue
            seen.add(url)
            try:
                status = save_pdf_from_url(url, dest)
                out.update(status=status, pdf_url=url, bytes=dest.stat().st_size)
                ok = True
                break
            except Exception as exc:
                last_error = f"{url}: {exc}"
        if not ok:
            out.update(status="failed", error=last_error or "no candidate pdf url")
        results.append(out)
        print(f"[{out['status']}] {out['requested_index']:02d} {out['title']}", flush=True)
        time.sleep(0.15)
    report = {
        "destination": str(DEST),
        "requested_count": len(results),
        "success_count": sum(1 for r in results if r["status"] in {"copied-cache", "downloaded", "exists"}),
        "failed_count": sum(1 for r in results if r["status"] == "failed"),
        "results": results,
    }
    report_path = DEST / "download-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ["destination", "requested_count", "success_count", "failed_count"]}, ensure_ascii=False, indent=2))
    return 0 if report["failed_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

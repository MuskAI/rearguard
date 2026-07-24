from __future__ import annotations

import base64
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote
from werkzeug.exceptions import UnprocessableEntity

from imagedetection.decision_labels import binary_final_label
from . import evidence_manifest
from .report_pdf import image_report_pdf as _render_image_report_pdf
from .report_pdf import video_report_pdf


def _safe_text(value: object, default: str = "—") -> str:
    text = str(value or "").strip()
    return text if text else default


def _fmt_time(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "—"
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return raw


def _html_page(title: str, accent: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(title)}</title>
  <style>
    :root {{
      --bg: #f7f7f2;
      --card: #ffffff;
      --text: #16324a;
      --muted: #5e716d;
      --line: #ccd8d3;
      --accent: {accent};
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: "PingFang SC", "Microsoft YaHei", sans-serif; background: var(--bg); color: var(--text); }}
    .page {{ max-width: 980px; margin: 0 auto; padding: 32px 20px 48px; }}
    .hero, .card {{ background: var(--card); border: 1px solid var(--line); border-radius: 8px; }}
    .hero {{ padding: 26px; margin-bottom: 18px; }}
    .card {{ padding: 18px; margin-top: 18px; }}
    .eyebrow {{ color: var(--accent); font-size: 12px; font-weight: 700; letter-spacing: 0; text-transform: uppercase; }}
    h1 {{ margin: 10px 0 8px; font-size: 32px; line-height: 1.15; }}
    h2 {{ margin: 0 0 12px; font-size: 18px; }}
    p {{ margin: 0; line-height: 1.75; }}
    .pill {{ display: inline-flex; align-items: center; padding: 8px 12px; border-radius: 6px; background: color-mix(in srgb, var(--accent) 12%, white); color: var(--accent); font-weight: 700; margin-top: 10px; }}
    .grid {{ display: grid; grid-template-columns: 1.15fr 0.85fr; gap: 18px; }}
    .meta-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 16px; }}
    .meta-grid > div {{ border: 1px solid var(--line); border-radius: 6px; padding: 12px 14px; background: #f7faf8; }}
    .meta-grid span {{ display: block; font-size: 12px; color: var(--muted); margin-bottom: 6px; }}
    .meta-grid strong {{ display: block; font-size: 14px; word-break: break-word; }}
    .preview {{ width: 100%; border-radius: 8px; border: 1px solid var(--line); background: #edf3ef; display: block; object-fit: cover; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ padding: 10px 8px; border-top: 1px solid var(--line); text-align: left; vertical-align: top; }}
    thead th {{ color: var(--muted); font-weight: 600; border-top: 0; padding-top: 0; }}
    .right {{ text-align: right; }}
    .footnote {{ margin-top: 16px; font-size: 12px; color: var(--muted); line-height: 1.7; }}
    @media (max-width: 860px) {{
      .grid, .meta-grid {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 28px; }}
    }}
    @media print {{
      body {{ background: #fff; }}
      .page {{ max-width: none; padding: 0; }}
    }}
  </style>
</head>
<body>
  <main class="page">{body}</main>
</body>
</html>"""


def image_report_filename(itemid: int | str) -> str:
    return f"huijian-image-report-{itemid}.pdf"


def video_report_filename(itemid: int | str) -> str:
    return f"huijian-video-report-{itemid}.pdf"


def freeze_image_evidence_snapshot(
    item: Mapping[str, Any],
    model_run: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze the first server-authoritative image evidence snapshot.

    Detection completion code can call this helper after the persisted record,
    original file, model-run audit, and EXIF row are durable. It intentionally
    accepts no client result payload.
    """
    return evidence_manifest.get_or_create_signed_image_manifest(
        item,
        model_run=model_run,
    )


def _signed_image_report(
    item: Mapping[str, Any],
    *,
    source_path: str | Path | None = None,
    model_run: Mapping[str, Any] | None = None,
    generated_at: datetime | str | None = None,
    signing_key: str | bytes | None = None,
    snapshot_root: str | Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        envelope = evidence_manifest.get_or_create_signed_image_manifest(
            item,
            source_path=source_path,
            model_run=model_run,
            generated_at=generated_at,
            key=signing_key,
            snapshot_root=snapshot_root,
        )
    except evidence_manifest.EvidenceManifestError as exc:
        raise UnprocessableEntity(
            description=f"无法生成可验证的图像报告：{exc}"
        ) from exc
    manifest = envelope["manifest"]
    conclusion = manifest["conclusion"]
    source = manifest["source"]
    model = manifest["model"]
    signature = envelope["signature"]
    evidence_text = manifest["evidence_summary"]["text"]
    structured = manifest.get("structured_evidence") or {}
    visible_watermark = structured.get("visible_watermark")
    capture = structured.get("capture_evidence") or {}
    metadata = structured.get("metadata") or {}
    human_manifest = "\n".join([
        evidence_text,
        "",
        "签名完整性清单（以下字段均由服务端签名）",
        f"任务ID：{manifest['task_id']}",
        f"记录ID：{manifest['record_id']}",
        f"原文件SHA-256：{source['sha256']}",
        f"模型版本：{model['version']}",
        f"策略版本：{manifest['policy_version']}",
        f"清单生成时间：{manifest['generated_at']}",
        f"签名算法：{signature['algorithm']} · 密钥标识：{signature['key_id']}",
        f"清单签名：{signature['value']}",
    ])

    # The rendering payload is rebuilt from signed server facts. In particular,
    # no filename, verdict, score, model version, hash, or explanation supplied in
    # the caller's result dictionary is promoted to authoritative report evidence.
    risk_score = conclusion.get("risk_score_percent")
    authoritative_result = {
        "itemid": item.get("itemid"),
        "final_label": conclusion["label"],
        "probability": None if risk_score is None else float(risk_score) / 100.0,
        "detector_probability": None if risk_score is None else float(risk_score) / 100.0,
        "confidence": conclusion["confidence"],
        "decisionStatus": conclusion.get("decision_status") or "review_only",
        "explanation": human_manifest,
        "filename": item.get("filename", ""),
        "file_size": item.get("file_size", ""),
        "img_format": item.get("img_format", ""),
        "resolution": item.get("resolution", ""),
        "visual_issues": [],
        "all_metadata": metadata if metadata.get("present") else {},
        "capture_evidence": capture,
        "visibleWatermark": visible_watermark,
        "evidenceCompleteness": bool(
            isinstance(visible_watermark, Mapping)
            and visible_watermark.get("supported") is True
        ),
    }
    return envelope, authoritative_result


def _html_envelope_block(envelope: Mapping[str, Any]) -> str:
    manifest = envelope["manifest"]
    source = manifest["source"]
    model = manifest["model"]
    signature = envelope["signature"]
    encoded = base64.urlsafe_b64encode(evidence_manifest.canonical_json(envelope)).decode("ascii")
    return f"""
        <section class="card" aria-labelledby="evidence-manifest-title">
          <h2 id="evidence-manifest-title">服务端完整性清单</h2>
          <table>
            <tbody>
              <tr><td>任务 / 记录 ID</td><td>{escape(manifest['task_id'])} / {escape(manifest['record_id'])}</td></tr>
              <tr><td>原文件 SHA-256</td><td style="word-break:break-all;">{escape(source['sha256'])}</td></tr>
              <tr><td>模型 / 策略版本</td><td>{escape(model['version'])} / {escape(manifest['policy_version'])}</td></tr>
              <tr><td>生成时间</td><td>{escape(manifest['generated_at'])}</td></tr>
              <tr><td>服务端 HMAC 完整性封印</td><td style="word-break:break-all;">{escape(signature['value'])}</td></tr>
            </tbody>
          </table>
          <div class="footnote">算法：{escape(signature['algorithm'])}；密钥标识：{escape(signature['key_id'])}。该封印需要平台持有的共享密钥才能验证，第三方不能仅凭本报告独立验签，也不属于可靠电子签名；仅用于平台服务端完整性校验，不替代司法鉴定或人工复核。</div>
          <script type="application/vnd.huijian.evidence+base64">{encoded}</script>
        </section>
    """


def image_report_content(
    item: dict,
    result: dict,
    *,
    source_path: str | Path | None = None,
    model_run: Mapping[str, Any] | None = None,
    generated_at: datetime | str | None = None,
    signing_key: str | bytes | None = None,
    snapshot_root: str | Path | None = None,
) -> str:
    del result
    envelope, result = _signed_image_report(
        item,
        source_path=source_path,
        model_run=model_run,
        generated_at=generated_at,
        signing_key=signing_key,
        snapshot_root=snapshot_root,
    )
    review_only = result.get("decisionStatus") != "verdict"
    probability = None if review_only else round(float(result.get("probability", 0) or 0) * 100, 1)
    confidence = _safe_text(result.get("confidence"), "")
    # The model decision contract is the single source of truth. Do not apply
    # a second hard-coded 35%-75% boundary here, which can contradict the
    # signed threshold and leave a verdict labeled as review-only.
    requires_review = review_only
    final_label = binary_final_label(result.get("final_label"), item.get("fake"))
    accent = "#b36a12" if requires_review else ("#d9573f" if "AI" in str(final_label) else "#1b8f7a")
    image_url = escape(_safe_text(result.get("image_url"), ""))
    preview = f'<img class="preview" src="{image_url}" alt="{escape(_safe_text(result.get("filename")))}" />' if image_url else '<div class="preview" style="min-height:260px;"></div>'
    capture = result.get("capture_evidence") if isinstance(result.get("capture_evidence"), dict) else {}
    visible = result.get("visibleWatermark") if isinstance(result.get("visibleWatermark"), Mapping) else None
    if visible is None or visible.get("supported") is not True:
        visible_summary = "检测服务不可用，本次水印证据不完整"
    elif visible.get("detected"):
        visible_summary = f"检出 {len(visible.get('hits') or [])} 处可复核水印线索"
    else:
        visible_summary = "已完成检测，本次未检出"
    capture_items = "".join(
        f"<li><strong>{escape(_safe_text(entry.get('label')))}</strong>：{escape(_safe_text(entry.get('value')))}</li>"
        for entry in (capture.get("evidence") or [])[:6]
        if isinstance(entry, dict)
    ) or "<li>未形成可用的拍摄流程元数据线索</li>"
    return _html_page(
        f"慧鉴 AI 图像鉴伪报告 {item.get('itemid')}",
        accent,
        f"""
        <section class="hero">
          <div class="eyebrow">Huijian AI Image Report</div>
          <h1>图像鉴伪报告</h1>
          <p>报告编号：IMG-{item.get('itemid')}。该报告用于留存图像鉴伪结论、核心元信息与简洁分析说明。</p>
          <div class="pill">{escape(_safe_text(final_label))} · {"未发布自动风险分数" if review_only else f"{probability}%"}</div>
          <div class="grid" style="margin-top:18px;">
            <div>
              <div class="meta-grid">
                <div><span>文件名</span><strong>{escape(_safe_text(result.get("filename")))}</strong></div>
                <div><span>时间</span><strong>{escape(_fmt_time(item.get("createtime")))}</strong></div>
                <div><span>置信度</span><strong>{escape(_safe_text(result.get("confidence")))}</strong></div>
                <div><span>文件格式</span><strong>{escape(_safe_text(result.get("img_format")))}</strong></div>
                <div><span>分辨率</span><strong>{escape(_safe_text(result.get("resolution")))}</strong></div>
                <div><span>文件大小</span><strong>{escape(_safe_text(result.get("file_size")))}</strong></div>
              </div>
              <div class="card">
                <h2>综合结论</h2>
                <p>{escape(_safe_text(result.get("explanation"), "暂无说明"))}</p>
              </div>
            </div>
            <div>{preview}</div>
          </div>
        </section>
        <section class="card">
          <h2>可疑点概览</h2>
          <table>
            <thead><tr><th>字段</th><th>内容</th></tr></thead>
            <tbody>
              <tr><td>视觉可疑点</td><td>{escape("；".join(result.get("visual_issues") or ["未完成视觉复核"]))}</td></tr>
              <tr><td>元数据</td><td>{escape("已提取，仅作辅助证据" if result.get("all_metadata") else "未提取到；缺失本身不代表伪造")}</td></tr>
              <tr><td>可见水印</td><td>{escape(visible_summary)}；仅作视觉定位，不单独决定真伪</td></tr>
              <tr><td>拍摄流程元数据</td><td>{escape(_safe_text(capture.get("summary"), "未形成可用拍摄流程线索"))}</td></tr>
            </tbody>
          </table>
          <h2 style="margin-top:18px;">拍摄流程线索 · {escape(_safe_text(capture.get("levelText"), "无"))}</h2>
          <ul>{capture_items}</ul>
          <div class="footnote">视觉水印可以被复制或覆盖；普通 EXIF 可以被编辑。只有通过签名校验的内容凭证属于密码学来源证据。</div>
          <div class="footnote">说明：本报告仅作业务留档与人工复核辅助，不构成司法或监管最终鉴定结论。</div>
        </section>
        {_html_envelope_block(envelope)}
        """,
    )


def image_report_pdf(
    item: dict[str, Any],
    result: dict[str, Any],
    *,
    source_path: str | Path | None = None,
    model_run: Mapping[str, Any] | None = None,
    generated_at: datetime | str | None = None,
    signing_key: str | bytes | None = None,
    snapshot_root: str | Path | None = None,
) -> bytes:
    del result
    envelope, authoritative_result = _signed_image_report(
        item,
        source_path=source_path,
        model_run=model_run,
        generated_at=generated_at,
        signing_key=signing_key,
        snapshot_root=snapshot_root,
    )
    rendered = _render_image_report_pdf(item, authoritative_result)
    try:
        bound_envelope = evidence_manifest.bind_pdf_artifact(rendered, envelope, key=signing_key)
        return evidence_manifest.embed_envelope_in_pdf(rendered, bound_envelope)
    except evidence_manifest.EvidenceManifestError as exc:
        raise UnprocessableEntity(
            description=f"无法生成可验证的图像报告：{exc}"
        ) from exc


def verify_image_report(pdf: bytes, *, signing_key: str | bytes | None = None) -> bool:
    return evidence_manifest.verify_pdf_report(pdf, key=signing_key)


def video_report_content(item: dict, result: dict) -> str:
    review_only = result.get("decisionStatus") != "verdict" or result.get("reviewRequired") is True
    raw_probability = result.get("fake_percentage")
    probability = None if review_only or raw_probability is None else round(float(raw_probability), 1)
    confidence = _safe_text(result.get("confidence"), "")
    requires_review = review_only or probability is None
    final_label = binary_final_label(result.get("final_label"), item.get("fake"))
    accent = "#b36a12" if requires_review else ("#d9573f" if "AI" in str(final_label) else "#1b8f7a")
    video_url = escape(_safe_text(result.get("video_url"), ""))
    preview = f'<video class="preview" src="{video_url}" controls></video>' if video_url else '<div class="preview" style="min-height:260px;"></div>'
    return _html_page(
        f"慧鉴 AI 视频鉴伪报告 {item.get('itemid')}",
        accent,
        f"""
        <section class="hero">
          <div class="eyebrow">Huijian AI Video Report</div>
          <h1>视频鉴伪报告</h1>
          <p>报告编号：VID-{item.get('itemid')}。该报告用于留存视频鉴伪结论、基础参数与简洁分析说明。</p>
          <div class="pill">{escape(_safe_text(final_label))} · {"未发布自动概率" if requires_review else f"{probability}%"}</div>
          <div class="grid" style="margin-top:18px;">
            <div>
              <div class="meta-grid">
                <div><span>文件名</span><strong>{escape(_safe_text(result.get("filename")))}</strong></div>
                <div><span>时间</span><strong>{escape(_fmt_time(item.get("createtime")))}</strong></div>
                <div><span>置信度</span><strong>{"不提供" if requires_review else escape(_safe_text(result.get("confidence")))}</strong></div>
                <div><span>时长</span><strong>{escape(_safe_text((result.get("meta") or {}).get("duration")))}</strong></div>
                <div><span>分辨率</span><strong>{escape(_safe_text((result.get("meta") or {}).get("resolution")))}</strong></div>
                <div><span>编码器</span><strong>{escape(_safe_text(result.get("encoder")))}</strong></div>
              </div>
              <div class="card">
                <h2>综合结论</h2>
                <p>{escape(_safe_text(result.get("explanation"), "暂无说明"))}</p>
              </div>
            </div>
            <div>{preview}</div>
          </div>
        </section>
        <section class="card">
          <h2>检测摘要</h2>
          <table>
            <thead><tr><th>字段</th><th>内容</th><th class="right">值</th></tr></thead>
            <tbody>
              <tr><td>AI 概率</td><td>综合伪造概率</td><td class="right">{"未发布" if requires_review else f"{probability}%"}</td></tr>
              <tr><td>真实概率</td><td>综合真实概率</td><td class="right">{"未发布" if requires_review else f"{round(float(result.get('real_percentage')), 1)}%"}</td></tr>
              <tr><td>帧数</td><td>返回的分析帧计数</td><td class="right">{escape(_safe_text(result.get("frame_count")))}</td></tr>
            </tbody>
          </table>
          <div class="footnote">说明：本报告仅作业务留档与人工复核辅助，不构成司法或监管最终鉴定结论。</div>
        </section>
        """,
    )

def attachment_header(filename: str) -> str:
    return f"attachment; filename*=UTF-8''{quote(filename)}"

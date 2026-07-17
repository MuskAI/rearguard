from __future__ import annotations

from datetime import datetime
from html import escape

from .pdf_report import build_report_pdf


VERDICT_META = {
    "real": {"label": "真实内容", "color": "#3fb6a8"},
    "suspected_fake": {"label": "疑似伪造", "color": "#d99a2b"},
    "highly_suspected_fake": {"label": "高度疑似伪造", "color": "#d8412f"},
}

TYPE_LABEL = {
    "image": "图像",
    "video": "视频",
    "audio": "音频",
    "document": "文档",
}


def _safe_text(value: object, default: str = "—") -> str:
    text = str(value or "").strip()
    return text if text else default


def _fmt_percent(value: object) -> str:
    try:
        return f"{round(float(value) * 100)}%"
    except Exception:
        return "—"


def _fmt_time(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "—"
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return raw


def _render_dimensions(result: dict) -> str:
    rows = []
    for item in result.get("dimensions", []) or []:
        label = escape(_safe_text(item.get("label")))
        detail = escape(_safe_text(item.get("result")))
        percent = _fmt_percent(item.get("score"))
        rows.append(
            f"""
            <tr>
              <td>{label}</td>
              <td>{detail}</td>
              <td class="right">{percent}</td>
            </tr>
            """
        )
    if not rows:
        rows.append('<tr><td colspan="3" class="muted">无维度数据</td></tr>')
    return "\n".join(rows)


def _render_regions(result: dict) -> str:
    rows = []
    for idx, item in enumerate(result.get("regions", []) or [], start=1):
        label = escape(_safe_text(item.get("label")))
        percent = _fmt_percent(item.get("score"))
        rows.append(
            f"""
            <tr>
              <td>#{idx}</td>
              <td>{label}</td>
              <td class="mono">{round(float(item.get("x", 0)) * 100, 1)}%, {round(float(item.get("y", 0)) * 100, 1)}%</td>
              <td class="mono">{round(float(item.get("w", 0)) * 100, 1)}% × {round(float(item.get("h", 0)) * 100, 1)}%</td>
              <td class="right">{percent}</td>
            </tr>
            """
        )
    if not rows:
        rows.append('<tr><td colspan="5" class="muted">未标注局部可疑区域</td></tr>')
    return "\n".join(rows)


def _render_optional_blocks(result: dict) -> str:
    blocks: list[str] = []
    synthid = result.get("synthid") or {}
    if synthid:
        blocks.append(
            f"""
            <section class="card">
              <h2>SynthID 水印取证</h2>
              <p>{escape(_safe_text(synthid.get("note")))}</p>
              <div class="meta-grid compact">
                <div><span>支持状态</span><strong>{'已启用' if synthid.get('supported') else '未启用'}</strong></div>
                <div><span>命中</span><strong>{'是' if synthid.get('detected') else '否'}</strong></div>
                <div><span>置信度</span><strong>{_fmt_percent(synthid.get('confidence'))}</strong></div>
              </div>
            </section>
            """
        )

    visible = result.get("visibleWatermark") or {}
    if visible:
        hits = []
        for idx, hit in enumerate(visible.get("hits", [])[:4], start=1):
            hits.append(
                f"""
                <tr>
                  <td>#{idx}</td>
                  <td>{escape(_safe_text(hit.get('provider'), '未知'))}</td>
                  <td>{escape(_safe_text(hit.get('method')))}</td>
                  <td class="mono">{round(float((hit.get('bbox') or {}).get('x', 0)) * 100, 1)}%, {round(float((hit.get('bbox') or {}).get('y', 0)) * 100, 1)}%</td>
                  <td class="right">{_fmt_percent(hit.get('confidence'))}</td>
                </tr>
                """
            )
        if not hits:
            hits.append('<tr><td colspan="5" class="muted">未返回可见水印命中详情</td></tr>')
        blocks.append(
            f"""
            <section class="card">
              <h2>可见 AI 水印检测</h2>
              <p>{escape(_safe_text(visible.get("note")))}</p>
              <div class="meta-grid compact">
                <div><span>命中</span><strong>{'是' if visible.get('detected') else '否'}</strong></div>
                <div><span>来源</span><strong>{escape(_safe_text(visible.get('provider'), '未知角标'))}</strong></div>
                <div><span>置信度</span><strong>{_fmt_percent(visible.get('confidence'))}</strong></div>
              </div>
              <table>
                <thead>
                  <tr><th>序号</th><th>来源</th><th>方法</th><th>位置</th><th class="right">置信度</th></tr>
                </thead>
                <tbody>{''.join(hits)}</tbody>
              </table>
            </section>
            """
        )

    return "\n".join(blocks)


def _render_forensics_block(forensics: dict | None) -> str:
    if not forensics:
        return ""
    items = []
    for item in forensics.get("items", []) or []:
        items.append(
            f"""
            <tr>
              <td>{escape(_safe_text(item.get('title')))}</td>
              <td>{escape(_safe_text(item.get('status')))}</td>
              <td>{escape(_safe_text(item.get('finding')))}</td>
            </tr>
            """
        )
    if not items:
        items.append('<tr><td colspan="3" class="muted">未附带取证分析条目</td></tr>')
    source = _safe_text(forensics.get("source"))
    model = _safe_text(forensics.get("modelVersion"))
    return f"""
    <section class="card">
      <h2>可解释性取证分析</h2>
      <p>{escape(_safe_text(forensics.get("summary"), "未附带综合取证总结"))}</p>
      <div class="meta-grid compact">
        <div><span>结论</span><strong>{escape(_safe_text(forensics.get("verdict")))}</strong></div>
        <div><span>置信度</span><strong>{_fmt_percent(forensics.get("confidence"))}</strong></div>
        <div><span>来源</span><strong>{escape(model if source == 'vlm' else source)}</strong></div>
      </div>
      <table>
        <thead>
          <tr><th>项目</th><th>状态</th><th>发现</th></tr>
        </thead>
        <tbody>{''.join(items)}</tbody>
      </table>
    </section>
    """


def _render_provenance_block(provenance: dict | None) -> str:
    if not provenance:
        return ""
    actions = provenance.get("actions", []) or []
    ingredients = provenance.get("ingredients", []) or []
    metadata_summary = provenance.get("metadataSummary") or {}
    ai_metadata = provenance.get("aiMetadata") or metadata_summary.get("aiDetection") or {}
    action_parts = []
    for item in actions:
        label = escape(_safe_text(item.get("action")))
        agent = _safe_text(item.get("softwareAgent"), "")
        agent_suffix = f"（{escape(agent)}）" if agent else ""
        action_parts.append(f"<li>{label}{agent_suffix}</li>")
    action_items = "".join(action_parts) or "<li class='muted'>未记录编辑动作</li>"

    ingredient_parts = []
    for item in ingredients:
        title = escape(_safe_text(item.get("title")))
        relationship = escape(_safe_text(item.get("relationship")))
        ingredient_parts.append(f"<li>{title} / {relationship}</li>")
    ingredient_items = "".join(ingredient_parts) or "<li class='muted'>未记录素材来源</li>"

    signal_items = []
    for item in (ai_metadata.get("signals") or [])[:8]:
        signal_items.append(
            "<li>"
            f"{escape(_safe_text(item.get('label')))}"
            f" / {escape(_safe_text(item.get('path')))}"
            f" / {escape(_safe_text(item.get('value')))}"
            "</li>"
        )
    signal_block = "".join(signal_items) or "<li class='muted'>未命中 AI 元数据线索</li>"

    preview_items = []
    for item in (metadata_summary.get("preview") or [])[:20]:
        preview_items.append(
            "<li>"
            f"{escape(_safe_text(item.get('path')))}：{escape(_safe_text(item.get('value')))}"
            "</li>"
        )
    preview_block = "".join(preview_items) or "<li class='muted'>未读取到可展示元数据</li>"

    return f"""
    <section class="card">
      <h2>内容凭证验证（C2PA）与元数据检测</h2>
      <div class="meta-grid compact">
        <div><span>凭证</span><strong>{'检测到' if provenance.get('hasCredentials') else '未检测到'}</strong></div>
        <div><span>签名校验</span><strong>{escape(_safe_text(provenance.get('validationState')))}</strong></div>
        <div><span>AI 声明</span><strong>{escape(_safe_text(provenance.get('isAiGenerated')))}</strong></div>
        <div><span>元数据 AI 线索</span><strong>{escape(_safe_text(provenance.get('metadataAiGenerated')))}</strong></div>
        <div><span>元数据评分</span><strong>{escape(_safe_text(ai_metadata.get('score'), '0'))}/100</strong></div>
        <div><span>元数据字段</span><strong>{escape(_safe_text(metadata_summary.get('fieldCount'), '0'))}</strong></div>
        <div><span>生成工具</span><strong>{escape(_safe_text(provenance.get('generator')))}</strong></div>
        <div><span>签发者</span><strong>{escape(_safe_text(provenance.get('issuer')))}</strong></div>
        <div><span>签名算法</span><strong>{escape(_safe_text(provenance.get('signatureAlg')))}</strong></div>
      </div>
      <div class="grid single-gap">
        <div>
          <h3>编辑动作</h3>
          <ul>{action_items}</ul>
        </div>
        <div>
          <h3>素材来源</h3>
          <ul>{ingredient_items}</ul>
        </div>
      </div>
      <div class="grid single-gap">
        <div>
          <h3>AI 元数据线索</h3>
          <ul>{signal_block}</ul>
        </div>
        <div>
          <h3>元数据预览</h3>
          <ul>{preview_block}</ul>
        </div>
      </div>
      <p class="footnote">SynthID 说明：{escape(_safe_text((provenance.get('synthid') or {}).get('note')))}</p>
    </section>
    """


def download_filename(result: dict) -> str:
    report_id = _safe_text(result.get("reportId"), "report")
    return f"huijian-report-{report_id}.pdf"


def build_report_html(result: dict, *, forensics: dict | None = None, provenance: dict | None = None) -> str:
    meta = VERDICT_META.get(result.get("verdict"), {"label": "未知结论", "color": "#4d423a"})
    file_meta = result.get("fileMeta", {}) or {}
    preview = file_meta.get("preview") or file_meta.get("thumbnail") or ""
    preview_html = (
        f'<img class="preview" src="{escape(str(preview), quote=True)}" alt="preview" />'
        if preview
        else '<div class="preview placeholder">无预览</div>'
    )
    report_id = escape(_safe_text(result.get("reportId")))
    task_id = escape(_safe_text(result.get("taskId")))
    file_name = escape(_safe_text(file_meta.get("name")))
    file_type = escape(TYPE_LABEL.get(file_meta.get("type"), _safe_text(file_meta.get("type"))))
    explanation = escape(_safe_text(result.get("explanation")))
    disclaimer = escape(_safe_text(result.get("disclaimer")))
    source = escape(_safe_text(result.get("source")))
    model = escape(_safe_text(result.get("modelVersion")))
    cache_hit = "是" if result.get("cacheHit") else "否"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>慧鉴 AI 鉴伪报告 {report_id}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5efe7;
      --card: #ffffff;
      --ink: #201813;
      --muted: #6f635c;
      --line: #ddd2c5;
      --accent: {meta["color"]};
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      background: var(--bg);
      color: var(--ink);
    }}
    .page {{
      max-width: 1040px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}
    .hero, .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 20px;
      box-shadow: 0 18px 50px rgba(32, 24, 19, 0.08);
    }}
    .hero {{
      padding: 28px;
      margin-bottom: 20px;
    }}
    .eyebrow {{
      color: var(--accent);
      font-size: 12px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      font-weight: 700;
    }}
    h1 {{
      margin: 10px 0 8px;
      font-size: 34px;
      line-height: 1.15;
    }}
    h2 {{
      margin: 0 0 12px;
      font-size: 18px;
    }}
    h3 {{
      margin: 0 0 10px;
      font-size: 15px;
    }}
    p {{
      margin: 0;
      line-height: 1.7;
    }}
    .hero-grid, .grid {{
      display: grid;
      gap: 18px;
    }}
    .hero-grid {{
      grid-template-columns: minmax(0, 1.25fr) minmax(280px, 0.95fr);
      align-items: start;
      margin-top: 20px;
    }}
    .grid {{
      grid-template-columns: 1fr 1fr;
    }}
    .grid.single-gap {{
      gap: 16px;
      margin-top: 16px;
    }}
    .card {{
      padding: 20px;
      margin-top: 18px;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border-radius: 999px;
      padding: 8px 14px;
      background: #fff7f4;
      border: 1px solid var(--line);
      color: var(--accent);
      font-weight: 700;
      margin-top: 8px;
    }}
    .preview {{
      width: 100%;
      border-radius: 16px;
      border: 1px solid var(--line);
      display: block;
      background: #f3ebe1;
      object-fit: cover;
    }}
    .placeholder {{
      min-height: 260px;
      display: grid;
      place-items: center;
      color: var(--muted);
    }}
    .meta-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin-top: 16px;
    }}
    .meta-grid.compact {{
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }}
    .meta-grid div {{
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px 14px;
      background: #fcf8f3;
    }}
    .meta-grid span {{
      display: block;
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 6px;
    }}
    .meta-grid strong {{
      font-size: 14px;
      word-break: break-word;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 14px;
      font-size: 13px;
    }}
    th, td {{
      border-top: 1px solid var(--line);
      padding: 10px 8px;
      text-align: left;
      vertical-align: top;
    }}
    thead th {{
      color: var(--muted);
      font-weight: 600;
      border-top: 0;
      padding-top: 0;
    }}
    .right {{ text-align: right; }}
    .mono {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
    }}
    .muted {{ color: var(--muted); }}
    .footnote {{
      margin-top: 18px;
      font-size: 12px;
      color: var(--muted);
      line-height: 1.7;
    }}
    ul {{
      margin: 0;
      padding-left: 18px;
      color: var(--ink);
      line-height: 1.7;
    }}
    li {{
      margin: 0 0 6px;
    }}
    @media print {{
      body {{ background: white; }}
      .page {{ max-width: none; padding: 0; }}
      .hero, .card {{ box-shadow: none; }}
    }}
    @media (max-width: 860px) {{
      .hero-grid, .grid, .meta-grid, .meta-grid.compact {{
        grid-template-columns: 1fr;
      }}
      h1 {{ font-size: 28px; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <section class="hero">
      <div class="eyebrow">Jianzhen Report</div>
      <h1>慧鉴 AI 数字内容鉴伪报告</h1>
      <p>报告号 {report_id}，任务号 {task_id}。本报告用于留存检测结果、主要证据维度与辅助取证说明。</p>
      <div class="pill">{meta["label"]} · 置信度 {_fmt_percent(result.get("confidence"))}</div>
      <div class="hero-grid">
        <div>
          <div class="meta-grid">
            <div><span>文件名</span><strong>{file_name}</strong></div>
            <div><span>文件类型</span><strong>{file_type}</strong></div>
            <div><span>文件大小</span><strong>{escape(_safe_text(file_meta.get("size")))}</strong></div>
            <div><span>图像分辨率</span><strong>{escape(_safe_text(file_meta.get("resolution")))}</strong></div>
            <div><span>模型版本</span><strong>{model}</strong></div>
            <div><span>结果来源</span><strong>{source}</strong></div>
            <div><span>缓存复用</span><strong>{cache_hit}</strong></div>
            <div><span>生成时间</span><strong>{escape(_fmt_time(result.get("createdAt")))}</strong></div>
          </div>
          <div class="card" style="margin-top:18px;">
            <h2>综合判定依据</h2>
            <p>{explanation}</p>
          </div>
        </div>
        <div>{preview_html}</div>
      </div>
    </section>

    <section class="grid">
      <section class="card">
        <h2>维度评分</h2>
        <table>
          <thead>
            <tr><th>维度</th><th>结论</th><th class="right">分数</th></tr>
          </thead>
          <tbody>{_render_dimensions(result)}</tbody>
        </table>
      </section>

      <section class="card">
        <h2>局部可疑区域</h2>
        <table>
          <thead>
            <tr><th>序号</th><th>标签</th><th>位置</th><th>尺寸</th><th class="right">分数</th></tr>
          </thead>
          <tbody>{_render_regions(result)}</tbody>
        </table>
      </section>
    </section>

    {_render_optional_blocks(result)}
    {_render_forensics_block(forensics)}
    {_render_provenance_block(provenance)}

    <section class="card">
      <h2>使用说明与限制</h2>
      <p>{disclaimer}</p>
      <div class="footnote">
        1. 本报告用于工程留档和人工复核辅助，不构成司法或监管意义上的最终鉴定结论。<br />
        2. 无水印、无凭证或未标注区域，不足以单独证明内容真实。<br />
        3. 若结果来源为备用链路，说明当前环境未调用真实模型，本报告仅表示备用分析结果。
      </div>
    </section>
  </main>
</body>
</html>
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from urllib.parse import quote


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
      --bg: #f5f7fb;
      --card: #ffffff;
      --text: #1f2937;
      --muted: #6b7280;
      --line: #d8dee9;
      --accent: {accent};
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: "PingFang SC", "Microsoft YaHei", sans-serif; background: var(--bg); color: var(--text); }}
    .page {{ max-width: 980px; margin: 0 auto; padding: 32px 20px 48px; }}
    .hero, .card {{ background: var(--card); border: 1px solid var(--line); border-radius: 18px; }}
    .hero {{ padding: 26px; margin-bottom: 18px; }}
    .card {{ padding: 18px; margin-top: 18px; }}
    .eyebrow {{ color: var(--accent); font-size: 12px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; }}
    h1 {{ margin: 10px 0 8px; font-size: 32px; line-height: 1.15; }}
    h2 {{ margin: 0 0 12px; font-size: 18px; }}
    p {{ margin: 0; line-height: 1.75; }}
    .pill {{ display: inline-flex; align-items: center; padding: 8px 14px; border-radius: 999px; background: color-mix(in srgb, var(--accent) 12%, white); color: var(--accent); font-weight: 700; margin-top: 10px; }}
    .grid {{ display: grid; grid-template-columns: 1.15fr 0.85fr; gap: 18px; }}
    .meta-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 16px; }}
    .meta-grid > div {{ border: 1px solid var(--line); border-radius: 14px; padding: 12px 14px; background: #fafbff; }}
    .meta-grid span {{ display: block; font-size: 12px; color: var(--muted); margin-bottom: 6px; }}
    .meta-grid strong {{ display: block; font-size: 14px; word-break: break-word; }}
    .preview {{ width: 100%; border-radius: 14px; border: 1px solid var(--line); background: #eef2f8; display: block; object-fit: cover; }}
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
    return f"realguard-image-report-{itemid}.html"


def video_report_filename(itemid: int | str) -> str:
    return f"realguard-video-report-{itemid}.html"


def image_report_content(item: dict, result: dict) -> str:
    final_label = result.get("final_label") or ("AI生成图像" if float(item.get("fake", 0) or 0) >= 50 else "真实图像")
    accent = "#d64242" if "AI" in str(final_label) else "#239c73"
    image_url = escape(_safe_text(result.get("image_url"), ""))
    preview = f'<img class="preview" src="{image_url}" alt="{escape(_safe_text(result.get("filename")))}" />' if image_url else '<div class="preview" style="min-height:260px;"></div>'
    probability = round(float(result.get("probability", 0) or 0) * 100, 1)
    return _html_page(
        f"RealGuard 图像鉴伪报告 {item.get('itemid')}",
        accent,
        f"""
        <section class="hero">
          <div class="eyebrow">RealGuard Image Report</div>
          <h1>图像鉴伪报告</h1>
          <p>报告编号：IMG-{item.get('itemid')}。该报告用于留存图像鉴伪结论、核心元信息与简洁分析说明。</p>
          <div class="pill">{escape(_safe_text(final_label))} · {probability}%</div>
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
              <tr><td>视觉可疑点</td><td>{escape("；".join(result.get("visual_issues") or ["未列出"]))}</td></tr>
              <tr><td>元数据</td><td>{escape("已提供" if result.get("all_metadata") else "未提供")}</td></tr>
            </tbody>
          </table>
          <div class="footnote">说明：本报告仅作业务留档与人工复核辅助，不构成司法或监管最终鉴定结论。</div>
        </section>
        """,
    )


def video_report_content(item: dict, result: dict) -> str:
    final_label = result.get("final_label") or "视频检测结果"
    accent = "#d64242" if "AI" in str(final_label) else "#239c73"
    video_url = escape(_safe_text(result.get("video_url"), ""))
    preview = f'<video class="preview" src="{video_url}" controls></video>' if video_url else '<div class="preview" style="min-height:260px;"></div>'
    return _html_page(
        f"RealGuard 视频鉴伪报告 {item.get('itemid')}",
        accent,
        f"""
        <section class="hero">
          <div class="eyebrow">RealGuard Video Report</div>
          <h1>视频鉴伪报告</h1>
          <p>报告编号：VID-{item.get('itemid')}。该报告用于留存视频鉴伪结论、基础参数与简洁分析说明。</p>
          <div class="pill">{escape(_safe_text(final_label))} · {round(float(result.get("fake_percentage", 0) or 0), 1)}%</div>
          <div class="grid" style="margin-top:18px;">
            <div>
              <div class="meta-grid">
                <div><span>文件名</span><strong>{escape(_safe_text(result.get("filename")))}</strong></div>
                <div><span>时间</span><strong>{escape(_fmt_time(item.get("createtime")))}</strong></div>
                <div><span>置信度</span><strong>{escape(_safe_text(result.get("confidence")))}</strong></div>
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
              <tr><td>AI 概率</td><td>综合伪造概率</td><td class="right">{round(float(result.get("fake_percentage", 0) or 0), 1)}%</td></tr>
              <tr><td>真实概率</td><td>综合真实概率</td><td class="right">{round(float(result.get("real_percentage", 0) or 0), 1)}%</td></tr>
              <tr><td>帧数</td><td>返回的分析帧计数</td><td class="right">{escape(_safe_text(result.get("frame_count")))}</td></tr>
            </tbody>
          </table>
          <div class="footnote">说明：本报告仅作业务留档与人工复核辅助，不构成司法或监管最终鉴定结论。</div>
        </section>
        """,
    )

def attachment_header(filename: str) -> str:
    return f"attachment; filename*=UTF-8''{quote(filename)}"

from __future__ import annotations

import base64
import binascii
import os
from io import BytesIO
from html import escape
from pathlib import Path
from threading import Lock
from typing import Any, Iterable

from PIL import Image as PILImage, ImageDraw, ImageOps
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


FONT_NAME = "HuijianCJK"
_FONT_LOCK = Lock()
_FONT_REGISTERED = False
_FONT_CANDIDATES = (
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
    ("/usr/share/fonts/truetype/arphic/uming.ttc", 0),
    ("/System/Library/Fonts/STHeiti Light.ttc", 0),
    ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
    ("/System/Library/Fonts/Supplemental/Songti.ttc", 0),
)
INK = colors.HexColor("#17333D")
MUTED = colors.HexColor("#657B82")
LINE = colors.HexColor("#CEDBD8")
SURFACE = colors.HexColor("#F2F7F5")
TEAL = colors.HexColor("#138D83")
RED = colors.HexColor("#D95B43")
AMBER = colors.HexColor("#C88724")


def _register_pdf_font() -> None:
    global _FONT_REGISTERED
    if _FONT_REGISTERED or FONT_NAME in pdfmetrics.getRegisteredFontNames():
        _FONT_REGISTERED = True
        return
    with _FONT_LOCK:
        if _FONT_REGISTERED or FONT_NAME in pdfmetrics.getRegisteredFontNames():
            _FONT_REGISTERED = True
            return
        configured = os.getenv("HUIJIAN_PDF_FONT_PATH", "").strip()
        candidates = list(_FONT_CANDIDATES)
        if configured:
            candidates.insert(0, (configured, int(os.getenv("HUIJIAN_PDF_FONT_INDEX", "0"))))
        for path, subfont_index in candidates:
            if not Path(path).is_file():
                continue
            try:
                pdfmetrics.registerFont(TTFont(FONT_NAME, path, subfontIndex=subfont_index))
            except Exception:
                continue
            _FONT_REGISTERED = True
            return
    raise RuntimeError(
        "无法生成中文 PDF：缺少可嵌入的 TrueType 中文字体。"
        "请安装 fonts-wqy-zenhei 或设置 HUIJIAN_PDF_FONT_PATH。"
    )


def _text(value: Any, default: str = "-") -> str:
    rendered = str(value if value is not None else "").strip()
    return rendered or default


def _percent(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if number <= 1:
        number *= 100
    return f"{number:.1f}%"


def _paragraph(value: Any, style: ParagraphStyle, default: str = "-") -> Paragraph:
    rendered = escape(_text(value, default)).replace("\n", "<br/>")
    return Paragraph(rendered, style)


def _styles() -> dict[str, ParagraphStyle]:
    return {
        "title": ParagraphStyle(
            "Title",
            fontName=FONT_NAME,
            fontSize=22,
            leading=29,
            textColor=INK,
            spaceAfter=3 * mm,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle",
            fontName=FONT_NAME,
            fontSize=9,
            leading=14,
            textColor=MUTED,
        ),
        "section": ParagraphStyle(
            "Section",
            fontName=FONT_NAME,
            fontSize=13,
            leading=18,
            textColor=INK,
            spaceBefore=5 * mm,
            spaceAfter=2.5 * mm,
        ),
        "body": ParagraphStyle(
            "Body",
            fontName=FONT_NAME,
            fontSize=9.5,
            leading=16,
            textColor=INK,
            alignment=TA_LEFT,
        ),
        "small": ParagraphStyle(
            "Small",
            fontName=FONT_NAME,
            fontSize=8,
            leading=12,
            textColor=MUTED,
        ),
        "table": ParagraphStyle(
            "Table",
            fontName=FONT_NAME,
            fontSize=8.5,
            leading=12,
            textColor=INK,
        ),
        "table_right": ParagraphStyle(
            "TableRight",
            fontName=FONT_NAME,
            fontSize=8.5,
            leading=12,
            textColor=INK,
            alignment=TA_RIGHT,
        ),
        "verdict": ParagraphStyle(
            "Verdict",
            fontName=FONT_NAME,
            fontSize=15,
            leading=20,
            textColor=colors.white,
            alignment=TA_CENTER,
        ),
    }


def _table(
    rows: list[list[Any]],
    widths: list[float],
    *,
    header: bool = True,
    compact: bool = False,
) -> Table:
    body_style = ParagraphStyle("Cell", fontName=FONT_NAME, fontSize=8.5, leading=12, textColor=INK)
    header_style = ParagraphStyle("CellHeader", fontName=FONT_NAME, fontSize=8.5, leading=12, textColor=MUTED)
    normalized = []
    for row_index, row in enumerate(rows):
        normalized.append([
            cell
            if hasattr(cell, "wrap")
            else _paragraph(cell, header_style if header and row_index == 0 else body_style)
            for cell in row
        ])
    table = Table(normalized, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    commands: list[tuple[Any, ...]] = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTNAME", (0, 0), (-1, -1), FONT_NAME),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("GRID", (0, 0), (-1, -1), 0.35, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5 if compact else 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5 if compact else 7),
    ]
    if header:
        commands.extend([
            ("BACKGROUND", (0, 0), (-1, 0), SURFACE),
            ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
        ])
    table.setStyle(TableStyle(commands))
    return table


def _meta_table(items: Iterable[tuple[str, Any]], styles: dict[str, ParagraphStyle]) -> Table:
    cells: list[Any] = []
    for label, value in items:
        cells.extend([
            _paragraph(label, styles["small"]),
            _paragraph(value, styles["table"]),
        ])
    if len(cells) % 4:
        cells.extend([_paragraph("", styles["small"]), _paragraph("", styles["table"])])
    rows = [cells[index:index + 4] for index in range(0, len(cells), 4)]
    table = _table(rows, [28 * mm, 58 * mm, 28 * mm, 58 * mm], header=False, compact=True)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SURFACE),
        ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
        ("TEXTCOLOR", (2, 0), (2, -1), MUTED),
    ]))
    return table


def _annotated_preview(result: dict[str, Any], styles: dict[str, ParagraphStyle]) -> list[Any]:
    file_meta = result.get("fileMeta") or {}
    preview = str(file_meta.get("preview") or file_meta.get("thumbnail") or "")
    if not preview.startswith("data:image/") or ";base64," not in preview:
        return []
    try:
        raw = base64.b64decode(preview.split(",", 1)[1], validate=False)
        with PILImage.open(BytesIO(raw)) as opened:
            oriented = ImageOps.exif_transpose(opened)
            if oriented.mode in {"RGBA", "LA"} or "transparency" in oriented.info:
                rgba = oriented.convert("RGBA")
                source = PILImage.new("RGB", rgba.size, "white")
                source.paste(rgba, mask=rgba.getchannel("A"))
            else:
                source = oriented.convert("RGB")
        source.thumbnail((1600, 1600), PILImage.Resampling.LANCZOS)
        visible = result.get("visibleWatermark") or {}
        draw = ImageDraw.Draw(source)
        line_width = max(3, round(max(source.size) / 260))
        for index, hit in enumerate((visible.get("hits") or [])[:12], start=1):
            bbox = hit.get("bbox") or {}
            x = max(0.0, min(float(bbox.get("x", 0)), 1.0))
            y = max(0.0, min(float(bbox.get("y", 0)), 1.0))
            w = max(0.0, min(float(bbox.get("w", 0)), 1.0 - x))
            h = max(0.0, min(float(bbox.get("h", 0)), 1.0 - y))
            if w <= 0 or h <= 0:
                continue
            box = (
                round(x * source.width),
                round(y * source.height),
                round((x + w) * source.width),
                round((y + h) * source.height),
            )
            color = (216, 65, 47) if hit.get("provider") != "yolo11x_watermark" else (211, 137, 33)
            draw.rectangle(box, outline=color, width=line_width)
            label_box = (box[0], max(0, box[1] - 20), box[0] + 22, box[1])
            draw.rectangle(label_box, fill=color)
            draw.text((label_box[0] + 6, label_box[1] + 3), str(index), fill="white")
        image_buffer = BytesIO()
        source.save(image_buffer, format="PNG", optimize=True)
        image_buffer.seek(0)
        max_width, max_height = 174 * mm, 92 * mm
        scale = min(max_width / source.width, max_height / source.height, 1.0)
        flowable = Image(image_buffer, width=source.width * scale, height=source.height * scale)
        flowable.hAlign = "CENTER"
        return [
            Spacer(1, 3 * mm),
            flowable,
            Spacer(1, 1.5 * mm),
            _paragraph("原图预览；可见水印命中已按检测坐标框选。", styles["small"]),
        ]
    except (ValueError, TypeError, OSError, binascii.Error):
        return []


def _page_footer(canvas, document) -> None:
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.line(18 * mm, 14 * mm, A4[0] - 18 * mm, 14 * mm)
    canvas.setFont(FONT_NAME, 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 9 * mm, "慧鉴AI 数字内容鉴伪报告")
    canvas.drawRightString(A4[0] - 18 * mm, 9 * mm, f"第 {document.page} 页")
    canvas.restoreState()


def build_report_pdf(
    result: dict[str, Any],
    *,
    forensics: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
) -> bytes:
    _register_pdf_font()
    styles = _styles()
    buffer = BytesIO()
    report_id = _text(result.get("reportId"), "report")
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=17 * mm,
        bottomMargin=20 * mm,
        title=f"慧鉴AI 鉴伪报告 {report_id}",
        author="慧鉴AI",
    )
    story: list[Any] = [
        _paragraph("慧鉴AI 数字内容鉴伪报告", styles["title"]),
        _paragraph(f"报告号 {report_id} · 任务号 {_text(result.get('taskId'))}", styles["subtitle"]),
        Spacer(1, 4 * mm),
    ]

    verdict = str(result.get("verdict") or "unknown")
    verdict_label = {
        "real": "更倾向真实",
        "suspected_fake": "疑似伪造",
        "highly_suspected_fake": "高度疑似伪造",
    }.get(verdict, "需要人工复核")
    verdict_color = TEAL if verdict == "real" else RED if verdict == "highly_suspected_fake" else AMBER
    verdict_table = Table(
        [[_paragraph(verdict_label, styles["verdict"]), _paragraph(f"AI 风险 / 置信度 {_percent(result.get('confidence'))}", styles["verdict"])]],
        colWidths=[82 * mm, 90 * mm],
    )
    verdict_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), verdict_color),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.extend([verdict_table, _paragraph("文件信息", styles["section"])])

    file_meta = result.get("fileMeta") or {}
    story.append(_meta_table([
        ("文件名", file_meta.get("name")),
        ("文件类型", file_meta.get("type")),
        ("文件大小", file_meta.get("size")),
        ("分辨率", file_meta.get("resolution")),
        ("模型版本", result.get("modelVersion")),
        ("结果来源", result.get("source")),
        ("生成时间", result.get("createdAt")),
        ("缓存复用", "是" if result.get("cacheHit") else "否"),
    ], styles))
    story.extend(_annotated_preview(result, styles))
    story.extend([
        _paragraph("综合判定依据", styles["section"]),
        _paragraph(result.get("explanation"), styles["body"], "未提供详细判定依据。"),
    ])

    visible = result.get("visibleWatermark") or {}
    if visible:
        story.append(_paragraph("可见水印证据", styles["section"]))
        story.append(_meta_table([
            ("检测状态", "检出" if visible.get("detected") else "未检出"),
            ("最高置信度", _percent(visible.get("confidence"))),
            ("识别来源", visible.get("provider")),
            ("检测耗时", f"{visible.get('elapsedMs')} ms" if visible.get("elapsedMs") else "-"),
        ], styles))
        hit_rows = [["序号", "水印类型", "置信度", "坐标与尺寸"]]
        for index, hit in enumerate((visible.get("hits") or [])[:12], start=1):
            bbox = hit.get("bbox") or {}
            hit_rows.append([
                str(index),
                _text(hit.get("label") or hit.get("provider")),
                _percent(hit.get("confidence")),
                f"x {_percent(bbox.get('x'))} · y {_percent(bbox.get('y'))} · w {_percent(bbox.get('w'))} · h {_percent(bbox.get('h'))}",
            ])
        if len(hit_rows) == 1:
            hit_rows.append(["-", "未返回定位框", "-", "-"])
        story.append(_table(hit_rows, [14 * mm, 54 * mm, 25 * mm, 79 * mm]))
        story.extend([Spacer(1, 2 * mm), _paragraph(visible.get("note"), styles["small"], "-")])

    dimensions = result.get("dimensions") or []
    if dimensions:
        story.append(_paragraph("证据维度", styles["section"]))
        rows = [["维度", "结果", "分数"]]
        for item in dimensions[:12]:
            rows.append([_text(item.get("label")), _text(item.get("result")), _percent(item.get("score"))])
        story.append(_table(rows, [43 * mm, 100 * mm, 29 * mm]))

    regions = result.get("regions") or []
    if regions:
        story.append(_paragraph("局部风险区域", styles["section"]))
        rows = [["序号", "标签", "位置", "分数"]]
        for index, item in enumerate(regions[:12], start=1):
            rows.append([
                str(index),
                _text(item.get("label")),
                f"x {_percent(item.get('x'))} · y {_percent(item.get('y'))}",
                _percent(item.get("score")),
            ])
        story.append(_table(rows, [14 * mm, 56 * mm, 73 * mm, 29 * mm]))

    synthid = result.get("synthid") or {}
    if synthid:
        story.append(_paragraph("SynthID 频谱取证", styles["section"]))
        story.append(_meta_table([
            ("检测状态", "检出" if synthid.get("detected") else "疑似" if synthid.get("possiblyDetected") else "未检出"),
            ("最高匹配", _percent(synthid.get("confidence"))),
            ("证据等级", synthid.get("evidenceLevel")),
            ("模型档案", synthid.get("modelProfile")),
        ], styles))
        story.extend([Spacer(1, 2 * mm), _paragraph(synthid.get("note"), styles["small"], "-")])

    if forensics:
        story.append(_paragraph("取证图谱分析", styles["section"]))
        story.append(_paragraph(forensics.get("summary"), styles["body"], "未附带取证图谱总结。"))
        rows = [["项目", "状态", "发现"]]
        for item in (forensics.get("items") or [])[:14]:
            rows.append([_text(item.get("title")), _text(item.get("status")), _text(item.get("finding"))])
        if len(rows) > 1:
            story.extend([Spacer(1, 2 * mm), _table(rows, [42 * mm, 25 * mm, 105 * mm])])

    if provenance:
        story.append(_paragraph("内容凭证与来源", styles["section"]))
        story.append(_meta_table([
            ("内容凭证", "已发现" if provenance.get("hasCredentials") else "未发现"),
            ("签名状态", provenance.get("validationState")),
            ("生成工具", provenance.get("generator")),
            ("签发者", provenance.get("issuer")),
            ("AI 声明", "有" if provenance.get("isAiGenerated") is True else "无" if provenance.get("isAiGenerated") is False else "未声明"),
            ("签名算法", provenance.get("signatureAlg")),
        ], styles))
        capture = provenance.get("captureEvidence") or (provenance.get("metadataSummary") or {}).get("captureEvidence") or {}
        if capture:
            story.extend([
                Spacer(1, 3 * mm),
                _paragraph("实拍来源证据", styles["section"]),
                _meta_table([
                    ("证据等级", capture.get("levelText")),
                    ("支持实拍", "是" if capture.get("supportsRealCapture") else "否"),
                    ("链路评分", _percent(capture.get("score"))),
                    ("分析版本", capture.get("version")),
                ], styles),
                Spacer(1, 2 * mm),
                _paragraph(capture.get("summary"), styles["body"], "未形成可用实拍证据。"),
            ])
            rows = [["证据项", "脱敏结果", "强度"]]
            for item in (capture.get("evidence") or [])[:8]:
                rows.append([_text(item.get("label")), _text(item.get("value")), _text(item.get("strength"))])
            for item in (capture.get("conflicts") or [])[:4]:
                rows.append([_text(item.get("label")), _text(item.get("value")), "冲突"])
            if len(rows) > 1:
                story.extend([Spacer(1, 2 * mm), _table(rows, [38 * mm, 105 * mm, 29 * mm])])

    story.extend([
        _paragraph("使用说明", styles["section"]),
        _paragraph(result.get("disclaimer"), styles["body"], "本报告仅用于辅助复核，不替代专业机构或人工最终判断。"),
        Spacer(1, 2 * mm),
        _paragraph("无水印、无凭证或未标注区域，不足以单独证明内容真实。", styles["small"]),
    ])

    document.build(story, onFirstPage=_page_footer, onLaterPages=_page_footer)
    return buffer.getvalue()

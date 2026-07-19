from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from threading import Lock
from typing import Any, Iterable

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from xml.sax.saxutils import escape


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


def _percent(value: Any, *, fraction: bool = False) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if fraction:
        number *= 100
    return f"{number:.1f}%"


def _styles() -> dict[str, ParagraphStyle]:
    return {
        "title": ParagraphStyle("Title", fontName=FONT_NAME, fontSize=22, leading=29, textColor=INK),
        "subtitle": ParagraphStyle("Subtitle", fontName=FONT_NAME, fontSize=9, leading=14, textColor=MUTED),
        "section": ParagraphStyle(
            "Section", fontName=FONT_NAME, fontSize=13, leading=18, textColor=INK,
            spaceBefore=5 * mm, spaceAfter=2.5 * mm,
        ),
        "body": ParagraphStyle("Body", fontName=FONT_NAME, fontSize=9.5, leading=16, textColor=INK, alignment=TA_LEFT),
        "small": ParagraphStyle("Small", fontName=FONT_NAME, fontSize=8, leading=12, textColor=MUTED),
        "table": ParagraphStyle("Table", fontName=FONT_NAME, fontSize=8.5, leading=12, textColor=INK),
        "verdict": ParagraphStyle(
            "Verdict", fontName=FONT_NAME, fontSize=15, leading=20,
            textColor=colors.white, alignment=TA_CENTER,
        ),
    }


def _p(value: Any, style: ParagraphStyle, default: str = "-") -> Paragraph:
    return Paragraph(escape(_text(value, default)).replace("\n", "<br/>"), style)


def _table(rows: list[list[Any]], widths: list[float], *, header: bool = True) -> Table:
    body_style = ParagraphStyle("Cell", fontName=FONT_NAME, fontSize=8.5, leading=12, textColor=INK)
    header_style = ParagraphStyle("CellHeader", fontName=FONT_NAME, fontSize=8.5, leading=12, textColor=MUTED)
    normalized = []
    for row_index, row in enumerate(rows):
        normalized.append([
            cell
            if hasattr(cell, "wrap")
            else _p(cell, header_style if header and row_index == 0 else body_style)
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
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
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
        cells.extend([_p(label, styles["small"]), _p(value, styles["table"])])
    if len(cells) % 4:
        cells.extend([_p("", styles["small"]), _p("", styles["table"])])
    rows = [cells[index:index + 4] for index in range(0, len(cells), 4)]
    table = _table(rows, [28 * mm, 58 * mm, 28 * mm, 58 * mm], header=False)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SURFACE),
        ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
        ("TEXTCOLOR", (2, 0), (2, -1), MUTED),
    ]))
    return table


def _footer(canvas, document) -> None:
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.line(18 * mm, 14 * mm, A4[0] - 18 * mm, 14 * mm)
    canvas.setFont(FONT_NAME, 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 9 * mm, "慧鉴AI 鉴伪报告")
    canvas.drawRightString(A4[0] - 18 * mm, 9 * mm, f"第 {document.page} 页")
    canvas.restoreState()


def _build_report(
    *,
    report_id: str,
    title: str,
    final_label: str,
    probability: float,
    confidence: Any,
    metadata: list[tuple[str, Any]],
    explanation: Any,
    summary_rows: list[list[Any]],
    decision_status: str = "verdict",
    visible: dict[str, Any] | None = None,
    capture: dict[str, Any] | None = None,
) -> bytes:
    _register_pdf_font()
    styles = _styles()
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=17 * mm,
        bottomMargin=20 * mm,
        title=f"慧鉴AI {title} {report_id}",
        author="慧鉴AI",
    )
    is_fake = any(token in final_label for token in ("AI", "伪造", "风险", "篡改", "翻拍", "深伪"))
    needs_review = "复核" in final_label or confidence == "低"
    verdict_color = AMBER if needs_review else RED if is_fake else TEAL
    story: list[Any] = [
        _p(f"慧鉴AI {title}", styles["title"]),
        _p(f"报告编号 {report_id}", styles["subtitle"]),
        Spacer(1, 4 * mm),
    ]
    score_summary = (
        "未发布自动风险分数 · 待人工复核"
        if decision_status != "verdict"
        else f"{'AI 生成风险' if 'AI生成' in final_label else '综合异常风险'} {probability:.1f}% · 置信度 {_text(confidence)}"
    )
    verdict_table = Table(
        [[_p(final_label, styles["verdict"]), _p(score_summary, styles["verdict"])]],
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
    story.extend([
        verdict_table,
        _p("文件信息", styles["section"]),
        _meta_table(metadata, styles),
        _p("综合判定依据", styles["section"]),
        _p(explanation, styles["body"], "暂无详细说明。"),
    ])

    if capture:
        signed_camera_capture = "signed_camera_capture" in (capture.get("groups") or [])
        story.append(_p("拍摄流程元数据线索", styles["section"]))
        story.append(_meta_table([
            ("线索等级", capture.get("levelText")),
            ("线索一致性", _percent(capture.get("score"), fraction=True)),
            ("密码学来源验证", "已验证相机凭证" if signed_camera_capture else "未验证"),
            ("分析版本", capture.get("version")),
        ], styles))
        story.extend([Spacer(1, 2 * mm), _p(capture.get("summary"), styles["body"], "未形成可用拍摄流程线索。")])
        capture_rows = [["证据项", "脱敏结果", "强度"]]
        for item in (capture.get("evidence") or [])[:8]:
            capture_rows.append([
                item.get("label"),
                item.get("value"),
                {"strong": "强", "medium": "中", "weak": "弱"}.get(item.get("strength"), item.get("strength")),
            ])
        for item in (capture.get("conflicts") or [])[:4]:
            capture_rows.append([item.get("label"), item.get("value"), "冲突"])
        if len(capture_rows) > 1:
            story.extend([Spacer(1, 2 * mm), _table(capture_rows, [32 * mm, 112 * mm, 28 * mm])])
        for note in (capture.get("limitations") or [])[:2]:
            story.extend([Spacer(1, 1.5 * mm), _p(note, styles["small"])])

    if visible:
        story.append(_p("可见水印定位线索", styles["section"]))
        story.append(_meta_table([
            ("检测状态", "检出" if visible.get("detected") else "未检出"),
            ("最高置信度", _percent(visible.get("confidence"), fraction=True)),
            ("识别来源", visible.get("provider")),
            ("定位数量", len(visible.get("hits") or [])),
            ("判定权限", "仅作视觉定位，不单独决定真伪"),
        ], styles))
        rows = [["序号", "线索类型", "置信度", "归属状态", "位置"]]
        for index, hit in enumerate((visible.get("hits") or [])[:12], start=1):
            bbox = hit.get("bbox") or {}
            attributed = (
                hit.get("evidenceRole") == "visual_attribution"
                or hit.get("method") == "remove_ai_watermarks_registry"
            )
            evidence_role = "平台标记归属" if attributed else "通用视觉定位"
            rows.append([
                str(index),
                evidence_role,
                _percent(hit.get("confidence"), fraction=True),
                _text(hit.get("label") or hit.get("provider")) if attributed else "未作来源确认",
                f"x {_percent(bbox.get('x'), fraction=True)} · y {_percent(bbox.get('y'), fraction=True)}",
            ])
        if len(rows) > 1:
            story.extend([Spacer(1, 2 * mm), _table(rows, [12 * mm, 38 * mm, 24 * mm, 44 * mm, 54 * mm])])
        story.extend([Spacer(1, 2 * mm), _p(visible.get("note"), styles["small"], "-")])
        story.extend([Spacer(1, 1.5 * mm), _p("视觉水印可以被复制、覆盖或二次传播；只有通过签名校验的内容凭证才属于密码学来源证据。", styles["small"])])

    story.append(_p("检测摘要", styles["section"]))
    story.append(_table(summary_rows, [42 * mm, 100 * mm, 30 * mm]))
    story.extend([
        _p("使用说明", styles["section"]),
        _p("本报告用于业务留档与人工复核辅助，不构成司法或监管意义上的最终鉴定结论。", styles["body"]),
        Spacer(1, 2 * mm),
        _p("无水印、无凭证或未标注区域，不足以单独证明内容真实。", styles["small"]),
    ])
    document.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()


def image_report_pdf(item: dict[str, Any], result: dict[str, Any]) -> bytes:
    decision_status = str(result.get("decisionStatus") or "review_only")
    review_only = decision_status != "verdict"
    probability = 0.0 if review_only else float(result.get("probability", 0) or 0) * 100
    confidence = "不适用" if review_only else _text(result.get("confidence"), "")
    requires_review = review_only or 35 < probability < 75 or confidence == "低"
    base_label = _text(
        result.get("final_label"),
        "AI生成图像" if float(item.get("fake", 0) or 0) >= 50 else "真实图像",
    )
    final_label = "需人工复核" if review_only else (
        f"{base_label}（需人工复核）" if requires_review and base_label != "需人工复核" else base_label
    )
    issues = "；".join(str(value) for value in (result.get("visual_issues") or []) if str(value).strip())
    return _build_report(
        report_id=f"IMG-{item.get('itemid')}",
        title="图像鉴伪报告",
        final_label=final_label,
        probability=probability,
        confidence=confidence,
        metadata=[
            ("文件名", result.get("filename")),
            ("检测时间", item.get("createtime")),
            ("文件格式", result.get("img_format")),
            ("分辨率", result.get("resolution")),
            ("文件大小", result.get("file_size")),
            ("任务编号", item.get("itemid")),
        ],
        explanation=result.get("explanation"),
        summary_rows=[
            ["字段", "内容", "状态"],
            ["视觉可疑点", issues or "未提取到明确视觉可疑点", "已完成"],
            ["元数据", "已提取，仅作辅助证据" if result.get("all_metadata") else "未提取到；缺失本身不代表伪造", "辅助"],
            ["拍摄流程元数据", (result.get("capture_evidence") or {}).get("summary") or "未形成可用拍摄流程线索", (result.get("capture_evidence") or {}).get("levelText") or "无"],
        ],
        decision_status=decision_status,
        visible=result.get("visibleWatermark") if isinstance(result.get("visibleWatermark"), dict) else None,
        capture=result.get("capture_evidence") if isinstance(result.get("capture_evidence"), dict) else None,
    )


def video_report_pdf(item: dict[str, Any], result: dict[str, Any]) -> bytes:
    probability = 0.0
    confidence = "不适用"
    final_label = "需人工复核"
    meta = result.get("meta") or {}
    return _build_report(
        report_id=f"VID-{item.get('itemid')}",
        title="视频鉴伪报告",
        final_label=final_label,
        probability=probability,
        confidence=confidence,
        metadata=[
            ("文件名", result.get("filename")),
            ("检测时间", item.get("createtime")),
            ("时长", meta.get("duration")),
            ("分辨率", meta.get("resolution")),
            ("编码器", result.get("encoder")),
            ("任务编号", item.get("itemid")),
        ],
        explanation=result.get("explanation"),
        summary_rows=[
            ["字段", "内容", "值"],
            ["自动风险分数", "视频校准契约尚未完成", "未发布"],
            ["结论权限", "当前结果仅供人工复核", "review_only"],
            ["分析帧数", "返回的分析帧计数", _text(result.get("frame_count"))],
        ],
        decision_status="review_only",
    )

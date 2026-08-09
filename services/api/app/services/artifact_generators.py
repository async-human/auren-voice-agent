from __future__ import annotations

import csv
import html
import io
import re
from collections.abc import Sequence
from typing import Any

from docx import Document
from docx.shared import Inches, Pt
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from pptx import Presentation
from pptx.util import Inches as PptxInches
from pptx.util import Pt as PptxPt
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

SpreadsheetValue = str | int | float | bool | None


def render_document(title: str, content: str, output_format: str) -> bytes:
    if output_format == "txt":
        return f"{title}\n{'=' * len(title)}\n\n{content.strip()}\n".encode()
    if output_format == "md":
        return f"# {title}\n\n{content.strip()}\n".encode()
    if output_format == "html":
        return _render_html(title, content).encode()
    if output_format == "docx":
        return _render_docx(title, content)
    if output_format == "pdf":
        return _render_pdf(title, content)
    raise ValueError(f"Unsupported document format: {output_format}")


def render_spreadsheet(
    title: str,
    columns: list[str],
    rows: list[list[SpreadsheetValue]],
    output_format: str,
) -> bytes:
    safe_rows = [[_safe_spreadsheet_value(value) for value in row] for row in rows]
    if output_format == "csv":
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(columns)
        writer.writerows(safe_rows)
        return stream.getvalue().encode("utf-8-sig")
    if output_format != "xlsx":
        raise ValueError(f"Unsupported spreadsheet format: {output_format}")

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = _worksheet_title(title)
    sheet.freeze_panes = "A2"
    sheet.append(columns)
    for row in safe_rows:
        sheet.append(row)
    header_fill = PatternFill("solid", fgColor="153B38")
    for cell in sheet[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center")
    sheet.auto_filter.ref = sheet.dimensions
    for index, column in enumerate(columns, start=1):
        values = [str(column), *(str(row[index - 1]) for row in safe_rows if index <= len(row))]
        sheet.column_dimensions[get_column_letter(index)].width = min(
            48,
            max(10, max((len(value) for value in values), default=10) + 2),
        )
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def render_presentation(title: str, slides: Sequence[dict[str, Any]]) -> bytes:
    presentation = Presentation()
    presentation.slide_width = PptxInches(13.333)
    presentation.slide_height = PptxInches(7.5)

    title_slide = presentation.slides.add_slide(presentation.slide_layouts[0])
    title_slide.shapes.title.text = title
    if len(title_slide.placeholders) > 1:
        title_slide.placeholders[1].text = "Prepared by Auren"

    for item in slides:
        slide = presentation.slides.add_slide(presentation.slide_layouts[1])
        slide.shapes.title.text = str(item.get("title") or "Section")
        frame = slide.placeholders[1].text_frame
        frame.clear()
        bullets = item.get("bullets") or []
        for index, bullet in enumerate(bullets):
            paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            paragraph.text = str(bullet)
            paragraph.level = 0
            paragraph.font.size = PptxPt(22)
        notes = item.get("notes")
        if notes:
            slide.notes_slide.notes_text_frame.text = str(notes)

    stream = io.BytesIO()
    presentation.save(stream)
    return stream.getvalue()


def _render_docx(title: str, content: str) -> bytes:
    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.7)
    section.bottom_margin = Inches(0.7)
    section.left_margin = Inches(0.8)
    section.right_margin = Inches(0.8)
    document.core_properties.title = title
    document.add_heading(title, level=0)
    for kind, value in _content_blocks(content):
        if kind == "heading":
            level, text = value
            document.add_heading(text, level=min(level, 3))
        elif kind == "bullet":
            document.add_paragraph(value, style="List Bullet")
        else:
            document.add_paragraph(value)
    styles = document.styles
    styles["Normal"].font.name = "Aptos"
    styles["Normal"].font.size = Pt(10.5)
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def _render_pdf(title: str, content: str) -> bytes:
    stream = io.BytesIO()
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "AurenBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=10.2,
        leading=15,
        alignment=TA_LEFT,
        spaceAfter=7,
    )
    heading = ParagraphStyle(
        "AurenHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        textColor="#153B38",
        spaceBefore=10,
        spaceAfter=6,
    )
    story: list[Any] = [Paragraph(html.escape(title), styles["Title"]), Spacer(1, 5 * mm)]
    for kind, value in _content_blocks(content):
        if kind == "heading":
            _level, text = value
            story.append(Paragraph(html.escape(text), heading))
        elif kind == "bullet":
            story.append(Paragraph(f"• {html.escape(value)}", body))
        else:
            story.append(Paragraph(html.escape(value).replace("\n", "<br/>"), body))
    document = SimpleDocTemplate(
        stream,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=17 * mm,
        bottomMargin=17 * mm,
        title=title,
    )
    document.build(story)
    return stream.getvalue()


def _render_html(title: str, content: str) -> str:
    parts = [
        "<!doctype html><html><head><meta charset=\"utf-8\">",
        f"<title>{html.escape(title)}</title>",
        "<style>body{max-width:820px;margin:48px auto;padding:0 24px;font:16px/1.65 system-ui;color:#18211f}h1,h2,h3{color:#153b38}li{margin:.35em 0}</style>",
        "</head><body>",
        f"<h1>{html.escape(title)}</h1>",
    ]
    in_list = False
    for kind, value in _content_blocks(content):
        if kind != "bullet" and in_list:
            parts.append("</ul>")
            in_list = False
        if kind == "heading":
            level, text = value
            parts.append(f"<h{min(level + 1, 4)}>{html.escape(text)}</h{min(level + 1, 4)}>")
        elif kind == "bullet":
            if not in_list:
                parts.append("<ul>")
                in_list = True
            parts.append(f"<li>{html.escape(value)}</li>")
        else:
            parts.append(f"<p>{html.escape(value).replace(chr(10), '<br>')}</p>")
    if in_list:
        parts.append("</ul>")
    parts.append("</body></html>")
    return "".join(parts)


def _content_blocks(content: str) -> list[tuple[str, Any]]:
    blocks: list[tuple[str, Any]] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append(("paragraph", "\n".join(paragraph).strip()))
            paragraph.clear()

    for raw_line in content.splitlines():
        line = raw_line.strip()
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        bullet = re.match(r"^[-*]\s+(.+)$", line)
        if heading:
            flush()
            blocks.append(("heading", (len(heading.group(1)), heading.group(2).strip())))
        elif bullet:
            flush()
            blocks.append(("bullet", bullet.group(1).strip()))
        elif not line:
            flush()
        else:
            paragraph.append(line)
    flush()
    return blocks


def _safe_spreadsheet_value(value: SpreadsheetValue) -> SpreadsheetValue:
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _worksheet_title(title: str) -> str:
    cleaned = re.sub(r"[\\/*?:\[\]]", " ", title).strip() or "Report"
    return cleaned[:31]

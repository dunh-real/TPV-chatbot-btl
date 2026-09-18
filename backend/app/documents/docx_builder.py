"""Xuất báo cáo ra file .docx.

Hai đường:
    có file mẫu  -> điền vào {{placeholder}}; thể thức kế thừa nguyên vẹn từ mẫu
    không có mẫu -> dựng bằng code, tự đặt phông/cỡ/lề theo Nghị định 30

Ưu tiên đường thứ nhất: mẫu đã được cán bộ văn thư duyệt thì mọi chi tiết trình
bày (canh chỉnh, tab, khoảng cách) đúng sẵn, code không phải dựng lại và không sợ
lệch chuẩn.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")
SECTIONS_PLACEHOLDER = "sections"

FONT = "Times New Roman"
SIZE_PT = 13
TITLE_SIZE_PT = 14
MARGINS_MM = {"top": 22, "bottom": 22, "left": 32, "right": 17}
# Giãn dòng và cách đoạn phải nằm trong ngưỡng của chính rule engine (nd30.yaml):
# văn bản hệ thống sinh ra mà không qua nổi bộ soát của hệ thống thì vô lý.
LINE_SPACING = 1.3
SPACE_PT = 3


def _apply_spacing(paragraph) -> None:
    """Đặt giãn dòng và cách đoạn thẳng lên đoạn, không để kế thừa mơ hồ."""
    from docx.shared import Pt

    pf = paragraph.paragraph_format
    pf.line_spacing = LINE_SPACING
    pf.space_before = Pt(SPACE_PT)
    pf.space_after = Pt(SPACE_PT)


@dataclass(slots=True)
class RenderedTable:
    columns: list[str]
    rows: list[list[str]]


@dataclass(slots=True)
class RenderedSection:
    id: str
    title: str
    paragraphs: list[str] = field(default_factory=list)
    table: RenderedTable | None = None
    image: bytes | None = None          # PNG biểu đồ, do code vẽ
    image_caption: str = ""


@dataclass(slots=True)
class DocumentPayload:
    """Nội dung đã chốt, sẵn sàng đổ ra file - không còn chỗ nào cho LLM chen vào."""

    meta: dict[str, Any] = field(default_factory=dict)
    sections: list[RenderedSection] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Điền vào file mẫu
# --------------------------------------------------------------------------- #
def _replace_in_paragraph(paragraph, values: dict[str, str]) -> None:
    """Thay placeholder trong một đoạn, gộp run để không bị Word cắt vụn chuỗi."""
    text = paragraph.text
    if "{{" not in text:
        return

    replaced = PLACEHOLDER_RE.sub(lambda m: str(values.get(m.group(1), "")), text)
    if replaced == text:
        return

    if paragraph.runs:
        paragraph.runs[0].text = replaced
        for run in paragraph.runs[1:]:
            run.text = ""
    else:
        paragraph.add_run(replaced)


def _insert_paragraph_after(paragraph, text: str = "", *, bold=False, size_pt=None,
                            align=None, italic=False):
    """python-docx chỉ cho thêm vào cuối, nên phải chèn tay vào cây XML."""
    import copy

    from docx.shared import Pt
    from docx.text.paragraph import Paragraph

    new_element = copy.deepcopy(paragraph._p)
    for child in list(new_element):
        if not child.tag.endswith("}pPr"):
            new_element.remove(child)
    paragraph._p.addnext(new_element)

    new_paragraph = Paragraph(new_element, paragraph._parent)
    if text:
        run = new_paragraph.add_run(text)
        run.font.name = FONT
        run.font.size = Pt(size_pt or SIZE_PT)
        run.font.bold = bold
        run.font.italic = italic
    if align is not None:
        new_paragraph.alignment = align
    _apply_spacing(new_paragraph)
    return new_paragraph


def _insert_table_after(paragraph, document, table: RenderedTable):
    import copy

    from docx.shared import Pt

    word_table = document.add_table(rows=1, cols=len(table.columns))
    word_table.style = "Table Grid"
    for index, column in enumerate(table.columns):
        cell = word_table.rows[0].cells[index]
        cell.text = ""
        run = cell.paragraphs[0].add_run(str(column))
        run.font.name, run.font.size, run.font.bold = FONT, Pt(SIZE_PT), True

    for row in table.rows:
        cells = word_table.add_row().cells
        for index, value in enumerate(row[: len(table.columns)]):
            cells[index].text = ""
            run = cells[index].paragraphs[0].add_run(str(value))
            run.font.name, run.font.size = FONT, Pt(SIZE_PT)

    # add_table đặt bảng ở cuối tài liệu -> di chuyển về đúng chỗ placeholder.
    moved = copy.deepcopy(word_table._tbl)
    word_table._tbl.getparent().remove(word_table._tbl)
    paragraph._p.addnext(moved)
    return moved


def _insert_image_after(paragraph, image_bytes: bytes, caption: str = ""):
    """Chèn ảnh biểu đồ ngay sau `paragraph`, kèm chú thích in nghiêng."""
    import io

    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Mm

    holder = _insert_paragraph_after(paragraph, "")
    holder.alignment = WD_ALIGN_PARAGRAPH.CENTER
    holder.add_run().add_picture(io.BytesIO(image_bytes), width=Mm(150))

    if caption:
        return _insert_paragraph_after(holder, caption, italic=True,
                                       align=WD_ALIGN_PARAGRAPH.CENTER)
    return holder


def fill_template(payload: DocumentPayload, template_path: Path, output_path: Path) -> Path:
    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    document = docx.Document(str(template_path))
    values = {k: ("" if v is None else str(v)) for k, v in payload.meta.items()}

    sections_anchor = None
    for paragraph in document.paragraphs:
        if f"{{{{{SECTIONS_PLACEHOLDER}}}}}" in paragraph.text:
            sections_anchor = paragraph
            continue
        _replace_in_paragraph(paragraph, values)

    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    _replace_in_paragraph(paragraph, values)

    if sections_anchor is None:
        logger.warning("Mẫu %s không có {{sections}} - nội dung sẽ nối vào cuối file",
                       template_path.name)
        _append_sections(document, payload.sections)
    else:
        # Chèn ngược từ dưới lên: mỗi lần chèn ngay sau mốc thì thứ tự giữ nguyên.
        cursor = sections_anchor
        for section in payload.sections:
            cursor = _insert_paragraph_after(cursor, section.title, bold=True)
            for text in section.paragraphs:
                cursor = _insert_paragraph_after(cursor, text,
                                                 align=WD_ALIGN_PARAGRAPH.JUSTIFY)
            if section.table is not None:
                _insert_table_after(cursor, document, section.table)
                cursor = _insert_paragraph_after(cursor, "")
                cursor = _insert_paragraph_after(cursor, "")
            if section.image:
                cursor = _insert_image_after(cursor, section.image, section.image_caption)
        sections_anchor._p.getparent().remove(sections_anchor._p)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(output_path))
    return output_path


# --------------------------------------------------------------------------- #
# Dựng từ code (khi template chưa có file mẫu)
# --------------------------------------------------------------------------- #
def _append_sections(document, sections: list[RenderedSection]) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    for section in sections:
        heading = document.add_paragraph()
        _apply_spacing(heading)
        run = heading.add_run(section.title)
        run.font.name, run.font.size, run.font.bold = FONT, Pt(SIZE_PT), True

        for text in section.paragraphs:
            paragraph = document.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            _apply_spacing(paragraph)
            run = paragraph.add_run(text)
            run.font.name, run.font.size = FONT, Pt(SIZE_PT)

        if section.image:
            import io

            from docx.enum.text import WD_ALIGN_PARAGRAPH
            from docx.shared import Mm

            holder = document.add_paragraph()
            holder.alignment = WD_ALIGN_PARAGRAPH.CENTER
            holder.add_run().add_picture(io.BytesIO(section.image), width=Mm(150))
            if section.image_caption:
                caption = document.add_paragraph()
                caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
                run = caption.add_run(section.image_caption)
                run.font.name, run.font.size, run.font.italic = FONT, Pt(SIZE_PT), True

        if section.table is not None:
            word_table = document.add_table(rows=1, cols=len(section.table.columns))
            word_table.style = "Table Grid"
            for index, column in enumerate(section.table.columns):
                cell = word_table.rows[0].cells[index]
                cell.text = ""
                run = cell.paragraphs[0].add_run(str(column))
                run.font.name, run.font.size, run.font.bold = FONT, Pt(SIZE_PT), True
            for row in section.table.rows:
                cells = word_table.add_row().cells
                for index, value in enumerate(row[: len(section.table.columns)]):
                    cells[index].text = ""
                    run = cells[index].paragraphs[0].add_run(str(value))
                    run.font.name, run.font.size = FONT, Pt(SIZE_PT)
            document.add_paragraph()


def build_from_scratch(payload: DocumentPayload, output_path: Path) -> Path:
    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Mm, Pt

    document = docx.Document()
    style = document.styles["Normal"]
    style.font.name, style.font.size = FONT, Pt(SIZE_PT)
    style.paragraph_format.line_spacing = 1.3

    section = document.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.top_margin = Mm(MARGINS_MM["top"])
    section.bottom_margin = Mm(MARGINS_MM["bottom"])
    section.left_margin = Mm(MARGINS_MM["left"])
    section.right_margin = Mm(MARGINS_MM["right"])

    meta = payload.meta

    def para(text, *, bold=False, size=SIZE_PT, align=None, italic=False):
        paragraph = document.add_paragraph()
        run = paragraph.add_run(str(text))
        run.font.name, run.font.size = FONT, Pt(size)
        run.font.bold, run.font.italic = bold, italic
        if align is not None:
            paragraph.alignment = align
        _apply_spacing(paragraph)
        return paragraph

    para(meta.get("noi_gui", ""), bold=True)
    para("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
    para("Độc lập - Tự do - Hạnh phúc", bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
    para(f"Số: {meta.get('so_ky_hieu', '')}")
    para(f"{meta.get('dia_danh', '')}, {meta.get('ngay_bao_cao', '')}",
         italic=True, align=WD_ALIGN_PARAGRAPH.RIGHT)
    para("BÁO CÁO", bold=True, size=TITLE_SIZE_PT, align=WD_ALIGN_PARAGRAPH.CENTER)
    para(meta.get("trich_yeu", ""), bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
    para(f"Kính gửi: {meta.get('noi_nhan', '')}", bold=True)
    if meta.get("can_cu"):
        para(meta["can_cu"], italic=True)

    _append_sections(document, payload.sections)

    para("Nơi nhận:", bold=True)
    para("- Như trên;")
    para("- Lưu: VT.")
    para(meta.get("chuc_vu_ky", ""), bold=True, align=WD_ALIGN_PARAGRAPH.RIGHT)
    para(meta.get("nguoi_ky", ""), bold=True, align=WD_ALIGN_PARAGRAPH.RIGHT)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(output_path))
    return output_path


def build_docx(
    payload: DocumentPayload, output_path: str | Path, template_path: str | Path | None = None
) -> Path:
    output_path = Path(output_path)
    if template_path:
        template = Path(template_path)
        if template.exists():
            return fill_template(payload, template, output_path)
        logger.warning("Không thấy mẫu %s - chuyển sang dựng bằng code", template)
    return build_from_scratch(payload, output_path)

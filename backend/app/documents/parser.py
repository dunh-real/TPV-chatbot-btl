"""Parse DOCX/PDF thành cây khối *có kèm thông tin định dạng*.

Khác với `app.rag.converter` (chỉ cần text để chunk), workflow 2 phải biết font,
cỡ chữ và lề trang - đó là dữ liệu cho rule engine. LLM không bao giờ nhìn thấy
những thuộc tính này, nên mọi kết luận về thể thức phải đến từ đây.

Ba mức thông tin tuỳ nguồn:
    docx        đầy đủ: font, cỡ, đậm, canh lề, lề trang, style
    pdf_digital gần đủ: font, cỡ, bbox; lề trang suy ra từ vùng chữ thực tế
    pdf_scan    chỉ có text sau OCR -> `has_format_info = False`
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

SourceFormat = Literal["docx", "pdf_digital", "pdf_scan", "text"]

EMU_PER_MM = 36000
PT_PER_MM = 72 / 25.4        # 1 mm = 2.8346 pt


@dataclass(slots=True)
class Block:
    """Một đoạn / ô bảng, kèm định dạng đọc được."""

    id: str                       # "P04" - địa chỉ dùng trong mọi phát hiện lỗi
    text: str
    kind: str = "paragraph"       # paragraph | table | heading
    font: str | None = None
    size_pt: float | None = None
    bold: bool = False
    alignment: str | None = None
    line_spacing: float | None = None     # bội số dòng; None khi kế thừa hoặc đặt cứng theo pt
    space_before_pt: float | None = None
    space_after_pt: float | None = None
    style: str | None = None
    page: int | None = None       # 1-based
    bbox: tuple[float, float, float, float] | None = None

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


@dataclass(slots=True)
class PageGeometry:
    """Khổ giấy và lề, quy về mm."""

    width_mm: float
    height_mm: float
    top_mm: float
    bottom_mm: float
    left_mm: float
    right_mm: float
    measured: bool = False        # True khi lề là ước lượng từ vùng chữ (PDF)


@dataclass(slots=True)
class DocumentStructure:
    blocks: list[Block] = field(default_factory=list)
    # Từng đoạn trong từng ô bảng, giữ riêng định dạng thật. Không nằm trong
    # `blocks` để mọi bước kiểm hiện có vẫn thấy bảng là một khối duy nhất.
    table_cells: list[Block] = field(default_factory=list)
    geometry: PageGeometry | None = None
    source_format: SourceFormat = "text"
    page_count: int = 0
    default_font: str | None = None
    default_size_pt: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_format_info(self) -> bool:
        """Rule engine chỉ kiểm tra được thể thức khi cờ này bật."""
        return self.source_format in ("docx", "pdf_digital")

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks if b.text.strip())

    def block(self, block_id: str) -> Block | None:
        return next((b for b in self.blocks if b.id == block_id), None)


# --------------------------------------------------------------------------- #
# DOCX
# --------------------------------------------------------------------------- #
def _effective(paragraph, attr: str):
    """Giá trị ĐANG CÓ HIỆU LỰC của một thuộc tính đoạn, không chỉ giá trị đặt tay.

    python-docx trả None khi thuộc tính được kế thừa từ style - mà định dạng qua
    style mới là cách làm đúng. Không lần theo chuỗi style thì mọi văn bản soạn
    tử tế đều bị báo "không kiểm được", còn văn bản chỉnh tay từng đoạn lại kiểm
    được: ngược hẳn.
    """
    value = getattr(paragraph.paragraph_format, attr, None)
    if value is not None:
        return value
    style = paragraph.style
    seen = 0
    while style is not None and seen < 10:      # chặn vòng lặp nếu style tự tham chiếu
        value = getattr(getattr(style, "paragraph_format", None), attr, None)
        if value is not None:
            return value
        style = getattr(style, "base_style", None)
        seen += 1
    return None


def _alignment_name(paragraph) -> str | None:
    """"JUSTIFY" / "CENTER"..., lấy cả khi đoạn kế thừa canh lề từ style."""
    value = _effective(paragraph, "alignment")
    return str(value).split(".")[-1].split(" ")[0] if value is not None else None


def _spacing(paragraph) -> tuple[float | None, float | None, float | None]:
    """Giãn dòng và khoảng cách trước/sau đoạn, quy về bội số và pt.

    `line_spacing` trả về bội số (1.15) khi đặt kiểu "Multiple", nhưng trả về
    `Length` khi người soạn đặt cứng theo pt. Trường hợp sau không quy ra bội số
    được nếu không biết cỡ chữ từng dòng, nên để None và báo là không kiểm được -
    hơn là suy ra một con số sai.
    """
    spacing = _effective(paragraph, "line_spacing")
    if spacing is not None and not isinstance(spacing, (int, float)):
        spacing = None
    before = _effective(paragraph, "space_before")
    after = _effective(paragraph, "space_after")
    return (
        float(spacing) if spacing is not None else None,
        before.pt if before is not None else None,
        after.pt if after is not None else None,
    )


def _docx_defaults(document) -> tuple[str | None, float | None]:
    """Font/cỡ mặc định của tài liệu (w:docDefaults) - run không khai báo thì kế thừa."""
    from docx.oxml.ns import qn

    try:
        defaults = document.styles.element.find(qn("w:docDefaults"))
        rpr = defaults.find(qn("w:rPrDefault")).find(qn("w:rPr"))
    except AttributeError:
        return None, None

    font = None
    size = None
    if (fonts := rpr.find(qn("w:rFonts"))) is not None:
        font = fonts.get(qn("w:ascii")) or fonts.get(qn("w:hAnsi"))
    if (sz := rpr.find(qn("w:sz"))) is not None and sz.get(qn("w:val")):
        size = float(sz.get(qn("w:val"))) / 2      # half-point -> point
    return font, size


def _resolve_run_format(run, paragraph, default_font, default_size):
    """Font/cỡ thực tế: run -> style của đoạn -> mặc định tài liệu."""
    font = run.font.name
    size = run.font.size.pt if run.font.size else None

    style = paragraph.style
    while style is not None and (font is None or size is None):
        if font is None and style.font.name:
            font = style.font.name
        if size is None and style.font.size:
            size = style.font.size.pt
        style = getattr(style, "base_style", None)

    return font or default_font, size if size is not None else default_size


def parse_docx(path: Path) -> DocumentStructure:
    import docx

    document = docx.Document(str(path))
    default_font, default_size = _docx_defaults(document)

    section = document.sections[0] if document.sections else None
    geometry = None
    if section is not None and section.page_width and section.page_height:
        geometry = PageGeometry(
            width_mm=section.page_width.mm,
            height_mm=section.page_height.mm,
            top_mm=section.top_margin.mm if section.top_margin else 0.0,
            bottom_mm=section.bottom_margin.mm if section.bottom_margin else 0.0,
            left_mm=section.left_margin.mm if section.left_margin else 0.0,
            right_mm=section.right_margin.mm if section.right_margin else 0.0,
        )

    blocks: list[Block] = []
    for paragraph in document.paragraphs:
        if not paragraph.text.strip():
            continue
        # Lấy định dạng của run đầu tiên có chữ - đại diện cho cả đoạn.
        run = next((r for r in paragraph.runs if r.text.strip()), None)
        line_spacing, space_before, space_after = _spacing(paragraph)
        font, size = (
            _resolve_run_format(run, paragraph, default_font, default_size)
            if run is not None
            else (default_font, default_size)
        )
        blocks.append(
            Block(
                id=f"P{len(blocks):02d}",
                text=paragraph.text.strip(),
                kind="heading" if paragraph.style.name.startswith("Heading") else "paragraph",
                font=font,
                size_pt=size,
                bold=bool(run.font.bold) if run is not None else False,
                alignment=_alignment_name(paragraph),
                line_spacing=line_spacing,
                space_before_pt=space_before,
                space_after_pt=space_after,
                style=paragraph.style.name,
            )
        )

    table_cells: list[Block] = []
    for table_index, table in enumerate(document.tables):
        for cell_index, paragraph in enumerate(
            p for row in table.rows for cell in row.cells for p in cell.paragraphs
        ):
            if not paragraph.text.strip():
                continue
            cell_run = next((r for r in paragraph.runs if r.text.strip()), None)
            cell_font, cell_size = (
                _resolve_run_format(cell_run, paragraph, default_font, default_size)
                if cell_run is not None
                else (default_font, default_size)
            )
            cell_ls, cell_sb, cell_sa = _spacing(paragraph)
            table_cells.append(Block(
                id=f"T{table_index:02d}C{cell_index:02d}",
                text=paragraph.text.strip(),
                kind="paragraph",
                font=cell_font,
                size_pt=cell_size,
                bold=bool(cell_run.font.bold) if cell_run is not None else False,
                alignment=_alignment_name(paragraph),
                line_spacing=cell_ls,
                space_before_pt=cell_sb,
                space_after_pt=cell_sa,
                style=paragraph.style.name,
            ))

        rows = [
            " | ".join(cell.text.strip() for cell in row.cells)
            for row in table.rows
        ]
        text = "\n".join(r for r in rows if r.strip(" |"))
        if not text:
            continue

        # Đọc định dạng thật của ô đầu tiên có chữ - lấy mặc định tài liệu sẽ
        # báo sai khi bảng dùng style riêng (Table Grid mặc định 11pt).
        font, size = default_font, default_size
        for row in table.rows:
            cell_run = next(
                (r for cell in row.cells for p in cell.paragraphs
                 for r in p.runs if r.text.strip()),
                None,
            )
            if cell_run is not None:
                paragraph = cell_run._parent
                font, size = _resolve_run_format(cell_run, paragraph, default_font, default_size)
                break

        blocks.append(
            Block(id=f"T{table_index:02d}", text=text, kind="table", font=font, size_pt=size)
        )

    return DocumentStructure(
        blocks=blocks,
        table_cells=table_cells,
        geometry=geometry,
        source_format="docx",
        page_count=0,
        default_font=default_font,
        default_size_pt=default_size,
    )


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #
def parse_pdf(path: Path) -> DocumentStructure:
    """PDF digital: lấy font/cỡ theo span, ước lượng lề từ vùng chữ thực tế."""
    import pymupdf

    document = pymupdf.open(str(path))
    try:
        blocks: list[Block] = []
        geometry: PageGeometry | None = None
        text_chars = 0

        for page_index, page in enumerate(document, start=1):
            page_dict = page.get_text("dict")
            content_bbox: list[float] | None = None

            for raw_block in page_dict["blocks"]:
                lines = raw_block.get("lines", [])
                if not lines:
                    continue
                spans = [s for line in lines for s in line["spans"] if s["text"].strip()]
                if not spans:
                    continue

                text = " ".join(
                    "".join(s["text"] for s in line["spans"]).strip() for line in lines
                ).strip()
                if not text:
                    continue
                text_chars += len(text)

                # Span dài nhất đại diện cho định dạng của khối.
                dominant = max(spans, key=lambda s: len(s["text"]))
                bbox = tuple(round(v, 1) for v in raw_block["bbox"])
                content_bbox = (
                    list(bbox) if content_bbox is None
                    else [min(content_bbox[0], bbox[0]), min(content_bbox[1], bbox[1]),
                          max(content_bbox[2], bbox[2]), max(content_bbox[3], bbox[3])]
                )

                blocks.append(
                    Block(
                        id=f"P{len(blocks):02d}",
                        text=text,
                        font=_clean_font_name(dominant["font"]),
                        size_pt=round(dominant["size"], 1),
                        bold=bool(dominant["flags"] & 2 ** 4),
                        page=page_index,
                        bbox=bbox,  # type: ignore[arg-type]
                    )
                )

            if geometry is None and content_bbox is not None:
                rect = page.rect
                geometry = PageGeometry(
                    width_mm=rect.width / PT_PER_MM,
                    height_mm=rect.height / PT_PER_MM,
                    top_mm=content_bbox[1] / PT_PER_MM,
                    bottom_mm=(rect.height - content_bbox[3]) / PT_PER_MM,
                    left_mm=content_bbox[0] / PT_PER_MM,
                    right_mm=(rect.width - content_bbox[2]) / PT_PER_MM,
                    measured=True,      # là vùng chữ thực tế, không phải lề khai báo
                )

        page_count = len(document)
    finally:
        document.close()

    # Rất ít chữ trên mỗi trang => PDF scan, không có gì để kiểm tra thể thức.
    is_scan = page_count > 0 and text_chars / page_count < 100
    return DocumentStructure(
        blocks=blocks,
        geometry=None if is_scan else geometry,
        source_format="pdf_scan" if is_scan else "pdf_digital",
        page_count=page_count,
    )


def _clean_font_name(name: str) -> str:
    """"ABCDEF+TimesNewRoman-Bold" -> "TimesNewRoman"."""
    if "+" in name:
        name = name.split("+", 1)[1]
    for suffix in ("-Bold", "-Italic", "-BoldItalic", ",Bold", ",Italic", "MT", "PSMT", "PS"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.strip()


def normalize_font(name: str | None) -> str:
    """So khớp tên font bỏ qua khoảng trắng và hoa thường: "Times New Roman" == "TimesNewRoman"."""
    return "".join((name or "").lower().split())


# --------------------------------------------------------------------------- #
def parse_document(path: str | Path) -> DocumentStructure:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return parse_docx(path)
    if suffix == ".pdf":
        return parse_pdf(path)
    if suffix in (".txt", ".md", ".markdown"):
        # Không có định dạng -> chỉ chạy được nhánh LLM.
        lines = [l.strip() for l in path.read_text(encoding="utf-8", errors="ignore").split("\n\n")]
        return DocumentStructure(
            blocks=[Block(id=f"P{i:02d}", text=t) for i, t in enumerate(l for l in lines if l)],
            source_format="text",
        )
    raise ValueError(f"Workflow 2 chỉ xử lý .docx, .pdf, .txt, .md - nhận được {path.suffix}")

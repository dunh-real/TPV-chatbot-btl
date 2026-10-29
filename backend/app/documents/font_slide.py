"""Ép một bộ chữ duy nhất lên toàn bộ file .pptx Presenton vừa xuất.

VÌ SAO PHẢI LÀM Ở ĐÂY

Presenton không cho chọn font: endpoint sinh slide chỉ nhận tên `template`, còn
các template dựng sẵn ghi cứng `var(--heading-font-family, Montserrat)` trong
bundle Next.js đã build. Trường `fonts` chỉ tồn tại trên template tự tạo - mà
dựng template riêng thì phải nạp một file .pptx mẫu và tốn thêm lượt gọi LLM.

File xuất ra là .pptx thật, nên đổi font ngay trên đó vừa rẻ vừa chắc: không phụ
thuộc phiên bản Presenton, không mất khi nâng cấp image, không tốn tiền API.

ĐỔI Ở BA CHỖ

Chỉ sửa từng đoạn chữ là hụt. Một ô chữ rỗng, một dòng mới người dùng gõ thêm,
hay một phần PowerPoint tự dựng lại sẽ rơi về font của theme. Nên đổi cả:

    1. từng run chữ - kể cả trong bảng và trong nhóm shape lồng nhau
    2. thuộc tính mặc định của mỗi đoạn (khi đoạn chưa có run nào)
    3. major/minor font của theme trong slide master - chỗ PowerPoint hỏi tới
       khi một shape không tự khai font

Mỗi chỗ ghi cả `latin` lẫn `ea` (East Asian): tiếng Việt có dấu, vài bộ dựng
slide xếp ký tự có dấu vào nhánh East Asian, để trống thì chữ có dấu hiện một
font còn chữ không dấu hiện font khác trên cùng một dòng.
"""

from __future__ import annotations

import logging
from io import BytesIO

from pptx import Presentation
from pptx.util import Pt

logger = logging.getLogger(__name__)

# Namespace DrawingML, cần để chạm vào phần theme mà python-pptx không bọc sẵn.
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _dat_font_run(run, ten_font: str) -> None:
    run.font.name = ten_font
    # python-pptx chỉ ghi `latin`. Ghi thêm `ea` và `cs` cho chữ có dấu.
    rpr = run.font._rPr
    if rpr is None:
        return
    for the in ("ea", "cs"):
        cu = rpr.find(f"{{{_NS_A}}}{the}")
        if cu is None:
            cu = rpr.makeelement(f"{{{_NS_A}}}{the}", {})
            rpr.append(cu)
        cu.set("typeface", ten_font)


def _dat_font_khung(text_frame, ten_font: str) -> None:
    for doan in text_frame.paragraphs:
        # Đoạn rỗng vẫn phải đặt: người dùng gõ thêm vào đó sẽ theo font này.
        if doan.font is not None:
            doan.font.name = ten_font
        for run in doan.runs:
            _dat_font_run(run, ten_font)


def _duyet_shape(shape, ten_font: str) -> None:
    """Đi vào mọi chỗ có chữ, kể cả nhóm lồng nhau và ô bảng."""
    if shape.shape_type is not None and shape.has_text_frame:
        _dat_font_khung(shape.text_frame, ten_font)

    if getattr(shape, "has_table", False) and shape.has_table:
        for hang in shape.table.rows:
            for o in hang.cells:
                _dat_font_khung(o.text_frame, ten_font)

    # Nhóm shape: python-pptx không tự đệ quy xuống.
    if getattr(shape, "shapes", None) is not None:
        for con in shape.shapes:
            _duyet_shape(con, ten_font)

    if getattr(shape, "has_chart", False) and shape.has_chart:
        try:
            shape.chart.font.name = ten_font
        except Exception as exc:  # noqa: BLE001 - biểu đồ lạ không làm hỏng cả file
            logger.debug("Không đổi được font biểu đồ: %s", exc)


def _dat_font_theme(master, ten_font: str) -> None:
    """Đổi major/minor font trong theme - font mặc định của cả slide master.

    Theme là một PART RIÊNG, không nằm trong XML của slide master, nên phải đi
    qua quan hệ `theme` để tới. Lấy `master.part.element` là ra đúng slideMaster
    và không tìm thấy nút `majorFont` nào - hỏng lặng lẽ.

    python-pptx không bọc part theme nên phải sửa thẳng XML của nó, và phải tự
    đánh dấu blob mới để lúc lưu nó ghi lại bản đã sửa.
    """
    theme_part = None
    for rel in master.part.rels.values():
        if not rel.is_external and rel.reltype.endswith("/theme"):
            theme_part = rel.target_part
            break
    if theme_part is None:
        return

    from lxml import etree

    try:
        goc = etree.fromstring(theme_part.blob)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Không đọc được theme: %s", exc)
        return

    doi = False
    for the in ("majorFont", "minorFont"):
        for nut in goc.iter(f"{{{_NS_A}}}{the}"):
            for con in ("latin", "ea", "cs"):
                el = nut.find(f"{{{_NS_A}}}{con}")
                if el is None:
                    el = etree.SubElement(nut, f"{{{_NS_A}}}{con}")
                el.set("typeface", ten_font)
                doi = True
    if doi:
        theme_part._blob = etree.tostring(
            goc, xml_declaration=True, encoding="UTF-8", standalone=True)


def doi_font(noi_dung: bytes, ten_font: str, *, co_chu_toi_thieu: float = 0.0) -> bytes:
    """Trả về bản .pptx đã đổi toàn bộ chữ sang `ten_font`.

    `co_chu_toi_thieu` > 0 thì nâng mọi cỡ chữ nhỏ hơn mức đó lên - Times New
    Roman đặc và thấp hơn Montserrat ở cùng số pt, nên chữ nhỏ dễ bị khó đọc khi
    chiếu. Để 0 là giữ nguyên cỡ.

    Lỗi ở khâu này KHÔNG được làm hỏng cả bộ slide: bộ slide đúng nhưng sai font
    vẫn dùng được, còn không có bộ slide nào thì không.
    """
    if not ten_font:
        return noi_dung
    try:
        pres = Presentation(BytesIO(noi_dung))
        for slide in pres.slides:
            for shape in slide.shapes:
                _duyet_shape(shape, ten_font)
            if slide.has_notes_slide:
                _dat_font_khung(slide.notes_slide.notes_text_frame, ten_font)
        for master in pres.slide_masters:
            for shape in master.shapes:
                _duyet_shape(shape, ten_font)
            for layout in master.slide_layouts:
                for shape in layout.shapes:
                    _duyet_shape(shape, ten_font)
            _dat_font_theme(master, ten_font)

        if co_chu_toi_thieu > 0:
            _nang_co_chu(pres, co_chu_toi_thieu)

        ra = BytesIO()
        pres.save(ra)
        return ra.getvalue()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Không đổi được font bộ slide sang %s: %s", ten_font, exc)
        return noi_dung


def _nang_co_chu(pres, toi_thieu: float) -> None:
    nguong = Pt(toi_thieu)
    for slide in pres.slides:
        for shape in slide.shapes:
            if not (shape.has_text_frame):
                continue
            for doan in shape.text_frame.paragraphs:
                for run in doan.runs:
                    if run.font.size is not None and run.font.size < nguong:
                        run.font.size = nguong

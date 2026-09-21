"""Ép font lên file .pptx Presenton xuất ra."""

from __future__ import annotations

import re
import zipfile
from io import BytesIO

import pytest

from app.documents.font_slide import doi_font

pptx = pytest.importorskip("pptx")
from pptx import Presentation  # noqa: E402
from pptx.util import Inches, Pt  # noqa: E402

FONT = "Times New Roman"


def _deck_mau() -> bytes:
    """Bộ slide có đủ những chỗ hay bị bỏ sót: ô chữ, bảng, nhóm shape."""
    pres = Presentation()
    slide = pres.slides.add_slide(pres.slide_layouts[6])

    hop = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
    hop.text_frame.text = "Tiêu đề có dấu tiếng Việt"
    hop.text_frame.paragraphs[0].runs[0].font.name = "Montserrat"

    bang = slide.shapes.add_table(2, 2, Inches(1), Inches(3), Inches(4), Inches(1)).table
    bang.cell(0, 0).text = "Đơn vị"
    bang.cell(1, 1).text = "9.485.000.000"

    ra = BytesIO()
    pres.save(ra)
    return ra.getvalue()


def _cac_font(noi_dung: bytes) -> set[str]:
    pres = Presentation(BytesIO(noi_dung))
    ten: set[str] = set()
    for slide in pres.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                for doan in shape.text_frame.paragraphs:
                    ten.update(r.font.name for r in doan.runs if r.font.name)
            if getattr(shape, "has_table", False) and shape.has_table:
                for hang in shape.table.rows:
                    for o in hang.cells:
                        for doan in o.text_frame.paragraphs:
                            ten.update(r.font.name for r in doan.runs if r.font.name)
    return ten


def test_doi_het_font_cua_chu_va_bang():
    ra = doi_font(_deck_mau(), FONT)
    assert _cac_font(ra) == {FONT}


def test_chu_co_dau_duoc_dat_ca_nhanh_east_asian():
    """Thiếu `ea` thì chữ có dấu và chữ không dấu hiện hai font khác nhau."""
    ra = doi_font(_deck_mau(), FONT)
    z = zipfile.ZipFile(BytesIO(ra))
    xml = z.read("ppt/slides/slide1.xml").decode("utf-8")
    assert f'<a:ea typeface="{FONT}"/>' in xml or f'typeface="{FONT}"' in xml
    assert "<a:ea" in xml


def test_doi_ca_font_theme():
    """Theme là part RIÊNG; sửa nhầm XML của slide master thì hỏng lặng lẽ."""
    ra = doi_font(_deck_mau(), FONT)
    z = zipfile.ZipFile(BytesIO(ra))
    ten_theme = next(n for n in z.namelist() if "theme" in n)
    xml = z.read(ten_theme).decode("utf-8")
    for nhom in ("majorFont", "minorFont"):
        khoi = re.search(nhom + r">.{0,200}", xml, re.S)
        assert khoi and FONT in khoi.group(0), f"{nhom} chưa đổi"


def test_file_van_mo_duoc_sau_khi_sua():
    ra = doi_font(_deck_mau(), FONT)
    assert zipfile.ZipFile(BytesIO(ra)).testzip() is None
    assert len(Presentation(BytesIO(ra)).slides._sldIdLst) == 1


def test_font_rong_thi_giu_nguyen_file():
    goc = _deck_mau()
    assert doi_font(goc, "") is goc


def test_file_hong_khong_lam_do_ca_bo_slide():
    """Bộ slide sai font vẫn dùng được; không có bộ slide nào thì không."""
    rac = b"khong phai pptx"
    assert doi_font(rac, FONT) == rac


def test_nang_co_chu_nho_len_muc_toi_thieu():
    pres = Presentation()
    slide = pres.slides.add_slide(pres.slide_layouts[6])
    hop = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
    hop.text_frame.text = "chữ nhỏ"
    hop.text_frame.paragraphs[0].runs[0].font.size = Pt(8)
    buf = BytesIO(); pres.save(buf)

    ra = Presentation(BytesIO(doi_font(buf.getvalue(), FONT, co_chu_toi_thieu=12)))
    co = ra.slides[0].shapes[0].text_frame.paragraphs[0].runs[0].font.size
    assert co == Pt(12)


def test_template_phai_nam_trong_bon_ten_presenton_chap_nhan():
    """Presenton trả 400 cho mọi template ngoài DEFAULT_TEMPLATES.

    Endpoint đọc layout trả về cả neo-modern, report... nên rất dễ tưởng dùng
    được; endpoint SINH slide thì chặn cứng. Đặt nhầm thì mọi yêu cầu tạo slide
    hỏng, mà lỗi chỉ lộ ra lúc chạy thật.
    """
    from app.core.config import Settings
    assert Settings().presenton_template in {"general", "modern", "standard", "swift"}

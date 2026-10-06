"""Converter: làm sạch Markdown và trích xuất giữ được bảng biểu."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.rag.converter import (
    DocumentConverter,
    bo_tieu_de_chay,
    cleanup_markdown,
    fix_broken_bold,
    go_markup_cong_thuc,
)
from app.rag.ocr import OCRService


# ------------------------------------------------------------- cleanup ---- #
def test_go_dau_sao_bi_vo_tren_tieng_viet():
    # pymupdf tách span theo font; chữ có dấu rơi sang span khác -> "**Đi**ề**u**"
    assert fix_broken_bold("# **Đi**ề**u 7. M**ứ**c ph**ụ") == "# Điều 7. Mức phụ"
    # In đậm thật (một cặp duy nhất) phải được giữ nguyên.
    assert fix_broken_bold("Đây là **quan trọng**") == "Đây là **quan trọng**"


def test_bo_so_trang_va_duong_ke_nhung_giu_dong_phan_cach_bang():
    raw = "Nội dung điều 1.\n- 12 -\n=====\n| A | B |\n|---|---|\n| 1 | 2 |"
    cleaned = cleanup_markdown(raw)
    assert "- 12 -" not in cleaned
    assert "=====" not in cleaned
    assert "|---|---|" in cleaned          # dòng phân cách của bảng KHÔNG bị xoá


def test_chuan_hoa_tieu_de_ngam():
    cleaned = cleanup_markdown("QUY ĐỊNH CHUNG\nNội dung.\n**Điều khoản thi hành**")
    assert "## QUY ĐỊNH CHUNG" in cleaned
    assert "### Điều khoản thi hành" in cleaned


def test_go_vo_markup_quanh_ky_hieu_toan():
    # PDF LaTeX in nghiêng mọi ký hiệu toán và bê nguyên thẻ <sup> sang Markdown.
    assert go_markup_cong_thuc("còn _H_<sup>¯</sup> _i_ là") == "còn H¯ i là"
    assert go_markup_cong_thuc("Sau _L_ bước boosting") == "Sau L bước boosting"
    assert go_markup_cong_thuc("learning rate 10<sup>_−_3</sup>") == "learning rate 10−3"


def test_khong_dung_vao_gach_duoi_trong_ten_bien():
    # "_" dính hai đầu vào chữ là một phần của tên, không phải dấu in nghiêng.
    assert go_markup_cong_thuc("biến ten_bien_nay") == "biến ten_bien_nay"


def test_bo_tieu_de_chay_lap_o_mep_moi_trang():
    trang = [f"Tên bài báo\n\nNội dung trang {i}." for i in range(1, 6)]
    ra = bo_tieu_de_chay(trang)
    assert all("Tên bài báo" not in t for t in ra)
    assert ra[0] == "Nội dung trang 1."


def test_cung_dong_do_nam_GIUA_trang_thi_la_noi_dung():
    # Chỉ mép trang mới là khuôn; lặp lại ở giữa trang là chữ thật của tài liệu.
    trang = [
        "\n\n".join([f"Mở đầu {i}", f"Câu A {i}", "Tên bài báo",
                      f"Câu B {i}", f"Kết {i}"])
        for i in range(1, 6)
    ]
    assert all("Tên bài báo" in t for t in bo_tieu_de_chay(trang))


def test_it_trang_qua_thi_khong_doan_tieu_de_chay():
    trang = ["Tên bài báo\n\nA", "Tên bài báo\n\nB"]
    assert bo_tieu_de_chay(trang) == trang


def test_giu_nguyen_can_cot_trong_bang():
    cleaned = cleanup_markdown("| Chức danh   | Mức   |\n|---|---|")
    assert "| Chức danh   | Mức   |" in cleaned


# ------------------------------------------------------- trích xuất file -- #
@pytest.fixture
def converter() -> DocumentConverter:
    return DocumentConverter(Settings(ocr_enabled=False))


def test_doc_file_markdown(tmp_path, converter):
    path = tmp_path / "quy_che.md"
    path.write_text("# Quy chế\n\nNội dung quy chế.", encoding="utf-8")
    assert "Quy chế" in converter.convert(path).text


def test_xlsx_thanh_bang_markdown(tmp_path, converter):
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "phucap.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in [["Chức danh", "Mức phụ cấp"], ["Giám đốc", 5_000_000]]:
        sheet.append(row)
    workbook.save(path)

    text = converter.convert(path).text
    assert "| Chức danh | Mức phụ cấp |" in text
    assert "Giám đốc" in text


def test_pdf_giu_bang_va_danh_dau_trang(tmp_path, converter):
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "bang.pdf"
    doc = pymupdf.open()
    css = ("@font-face {font-family: vn; src: url(/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf);}"
           "*{font-family: vn;} table{border-collapse:collapse} td,th{border:1px solid #000;padding:4px}")
    html = ("<h2>Điều 7. Mức phụ cấp</h2><table>"
            "<tr><th>Chức danh</th><th>Mức</th></tr>"
            "<tr><td>Giám đốc</td><td>5.000.000</td></tr></table>")
    doc.new_page().insert_htmlbox(pymupdf.Rect(50, 50, 550, 400), html, css=css,
                                  archive=pymupdf.Archive("/"))
    doc.save(path)
    doc.close()

    loaded = converter.convert(path)
    assert "Điều 7. Mức phụ cấp" in loaded.text
    assert "|Giám đốc|5.000.000|" in loaded.text.replace(" |", "|").replace("| ", "|")
    assert loaded.page_offsets == [0]


def test_dinh_dang_khong_ho_tro(tmp_path, converter):
    path = tmp_path / "a.zip"
    path.write_bytes(b"PK")
    with pytest.raises(ValueError, match="chưa hỗ trợ"):
        converter.convert(path)


# ------------------------------------------------------- phân loại trang -- #
def test_trang_digital_khong_bi_dua_di_ocr(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "digital.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Noi dung day du chu nghia " * 20, fontsize=11)
    doc.save(path)
    doc.close()

    assert OCRService(Settings()).classify_pages(path) == [False]


def test_trang_trang_rong_bi_coi_la_scan(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    doc.new_page()          # trang trắng: 0 ký tự, 0 font
    doc.save(path)
    doc.close()

    assert OCRService(Settings()).classify_pages(path) == [True]


def _pdf_scan_co_lop_tesseract(path, pymupdf, *, chu_vo_hinh: bool = True):
    """Dựng đúng hình dạng PDF scan đã chạy qua Tesseract: một ảnh phủ trọn
    trang, bên trên là lớp chữ vô hình (render mode 3)."""
    doc = pymupdf.open()
    page = doc.new_page()
    anh = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 1240, 1754), False)
    anh.set_rect(anh.irect, (255, 255, 255))
    page.insert_image(page.rect, pixmap=anh)
    page.insert_text(
        (72, 100), "Noi dung Tesseract doc sai be bet " * 20, fontsize=11,
        render_mode=3 if chu_vo_hinh else 0,
    )
    doc.save(path)
    doc.close()


def test_scan_co_lop_chu_vo_hinh_van_phai_ocr(tmp_path):
    """Lớp chữ Tesseract đủ dày để mọi tín hiệu đếm chữ báo "digital".

    Đây là ca đã lọt lưới: trang trích ra hàng nghìn ký tự nên không phiếu nào
    trong 3 tín hiệu đếm chữ chịu bầu, tài liệu trượt khỏi OCR và hệ thống đọc
    thẳng bản Tesseract sai. Bố cục trang mới là thứ nói đúng sự thật.
    """
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "scan_tesseract.pdf"
    _pdf_scan_co_lop_tesseract(path, pymupdf)

    assert OCRService(Settings()).classify_pages(path) == [True]


def test_anh_lon_nhung_chu_nhin_thay_duoc_thi_khong_ocr(tmp_path):
    """Trang digital có ảnh nền to vẫn là trang digital - đừng OCR phí.

    Phân biệt với ca trên chỉ bằng một điều: ở đây chữ được vẽ để người đọc
    nhìn thấy, nên nó là chữ thật chứ không phải lớp OCR chồng lên ảnh.
    """
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "digital_co_anh_nen.pdf"
    _pdf_scan_co_lop_tesseract(path, pymupdf, chu_vo_hinh=False)

    assert OCRService(Settings()).classify_pages(path) == [False]


def test_ocr_tat_thi_khong_goi_api(tmp_path):
    service = OCRService(Settings(ocr_enabled=False))
    assert service.enabled is False
    assert service.ocr_pages(tmp_path / "x.pdf", [0]) == {}


def test_anh_luon_can_ocr(tmp_path):
    path = tmp_path / "scan.png"
    path.write_bytes(b"\x89PNG")
    assert OCRService(Settings()).classify_pages(path) == [True]

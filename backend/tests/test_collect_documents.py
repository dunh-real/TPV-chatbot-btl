"""Lấy số liệu từ chính báo cáo đơn vị: đọc bảng, cộng lại, nói rõ nguồn.

Số thật của một báo cáo kiểm kê nằm trong BẢNG. Dò regex trên văn xuôi chỉ bắt
được con số tổng mà người viết nhắc lại - mà họ có thể nhắc sai, hoặc không nhắc.
"""

from __future__ import annotations

from app.documents.collect import UnitReport, aggregate_reports
from app.documents.extract_figures import extract_inventory, markdown_tables, parse_int

BANG_KIEM_KE = """Kết quả tổng hợp như sau:

|  |  |  |  |  |  |  |
| --- | --- | --- | --- | --- | --- | --- |
| STT | Danh mục trang thiết bị | ĐVT | Tổng SL | Hoạt động tốt | Cần bảo dưỡng / sửa chữa | Đề xuất thanh lý |
| 1 | Máy tính để bàn Workstation | Bộ | 18 | 16 | 02 | 00 |
| 2 | Máy tính xách tay | Chiếc | 15 | 14 | 01 | 00 |
| 3 | Hệ thống máy chủ nội bộ | Hệ | 03 | 03 | 00 | 00 |
|  | Tổng cộng |  | 36 | 33 | 03 | 00 |

Tổng số lượng trang thiết bị là 36 đơn vị.
"""


def test_doc_so_co_so_khong_dung_dau_va_dau_phan_cach():
    assert parse_int("02") == 2
    assert parse_int("1.234") == 1234
    assert parse_int("") is None
    assert parse_int("Chiếc") is None


def test_bo_dong_phan_cach_cua_markdown():
    tables = markdown_tables(BANG_KIEM_KE)
    assert len(tables) == 1
    # Dòng "| --- | --- |" bị loại; dòng ô rỗng của MarkItDown thì giữ (nó là
    # một hàng thật, chỉ là rỗng).
    assert not any(any(c.strip("-: ") == "" and c.strip() for c in row) for row in tables[0])


def test_doc_dung_tung_dong_va_dong_tong_cong():
    inv = extract_inventory(BANG_KIEM_KE)

    assert len(inv.rows) == 3                      # dòng "Tổng cộng" không tính là chủng loại
    assert inv.rows[0]["ten"].startswith("Máy tính để bàn")
    assert inv.rows[0]["tong"] == 18 and inv.rows[0]["can_xu_ly"] == 2
    assert inv.totals == {"tong": 36, "tot": 33, "can_xu_ly": 3, "thanh_ly": 0}
    assert inv.computed["tong"] == 36
    assert inv.total_mismatch == {}


def test_bao_cao_tu_mau_thuan_thi_neu_ra_chu_khong_lam_muot():
    """Dòng Tổng cộng ghi 99 nhưng cộng các dòng ra 36 - lỗi của chính báo cáo."""
    sai = BANG_KIEM_KE.replace("| Tổng cộng |  | 36 |", "| Tổng cộng |  | 99 |")
    inv = extract_inventory(sai)

    assert inv.total_mismatch["tong"] == (99, 36)


def test_bang_khong_phai_kiem_ke_thi_bo_qua():
    khac = """| Họ tên | Chức vụ |
| --- | --- |
| Nguyễn Văn A | Trưởng phòng |
"""
    assert extract_inventory(khac) is None


def _report(code: str, md: str) -> UnitReport:
    return UnitReport(file_path=f"/tmp/{code}.docx", ma_don_vi=code,
                      ten_don_vi=f"Đơn vị {code}", inventory=extract_inventory(md))


def test_cong_nhieu_bao_cao_va_giu_dau_vet_tung_file():
    agg = aggregate_reports([_report("DV01", BANG_KIEM_KE), _report("DV02", BANG_KIEM_KE)],
                            "2026-08")

    assert agg["metrics"]["total_equipment"]["value"] == 72      # 36 + 36
    assert agg["metrics"]["good"]["share_pct"] == 91.7
    assert [s["ma_don_vi"] for s in agg["sources"]] == ["DV01", "DV02"]
    assert len(agg["breakdown"]) == 6                            # 3 chủng loại x 2 đơn vị
    assert all(r["nguon_file"] for r in agg["breakdown"])        # mọi dòng chỉ được về file


def test_khong_co_ky_truoc_thi_khong_bia_ra_tang_giam():
    """Một báo cáo là ảnh chụp một thời điểm; không có gì để so sánh."""
    agg = aggregate_reports([_report("DV01", BANG_KIEM_KE)], "2026-08")
    metric = agg["metrics"]["total_equipment"]

    assert metric["prev"] is None and metric["delta"] is None and metric["delta_pct"] is None


def test_file_hong_duoc_ke_ra_chu_khong_im_lang_bo_qua():
    hong = UnitReport(file_path="/tmp/mat.docx", ma_don_vi="DV03", error="không tìm thấy file")
    agg = aggregate_reports([_report("DV01", BANG_KIEM_KE), hong], "2026-08")

    assert agg["failed"] == [{"file": "mat.docx", "ly_do": "không tìm thấy file"}]
    assert agg["scope"]["units_with_data"] == 1


def test_cot_ngay_thang_khong_bi_dem_thanh_so_luong():
    """"Bảo dưỡng gần nhất" là cột NGÀY; đếm nó thành thiết bị hỏng là sai hẳn."""
    co_cot_ngay = """| Tên trang bị | Số lượng | Tình trạng | Bảo dưỡng gần nhất |
| --- | --- | --- | --- |
| Máy chủ | 6 | Tốt | 2026-03-05 |
| Máy trạm | 60 | Tốt | 2026-04-01 |
"""
    inv = extract_inventory(co_cot_ngay)

    assert inv.computed["tong"] == 66
    assert "can_xu_ly" not in inv.computed        # không có cột đếm nào cho nó


def test_lech_rang_buoc_nghiep_vu_thi_bao_ra():
    """tốt + cần xử lý phải bằng tổng; bảng thiếu cột thì phải nói, không lấp liếm."""
    thieu_cot = """| Danh mục trang thiết bị | Số lượng | Đề xuất thanh lý |
| --- | --- | --- |
| Máy chủ | 6 | 1 |
"""
    agg = aggregate_reports([_report("DV01", thieu_cot)], "2026-08")
    assert agg["consistency"] and "Tổng trang bị 6" in agg["consistency"][0]["message"]

"""Thẻ thông tin văn bản: tách phần thể thức thành một chunk riêng khi ingest.

Chi tiết hay bị hỏi lẻ - ai ký, số mấy, ngày nào - nằm rải trong một văn bản dài
thì chunk chứa nó không *về* chúng, và cross-encoder chấm rất thấp. Thẻ ngắn này
thì về đúng những câu đó.

Ràng buộc phải giữ: tệp không phải văn bản hành chính thì KHÔNG dựng thẻ, nếu
không mọi tài liệu kỹ thuật trong kho đều có thêm một chunk rác.
"""

from __future__ import annotations

from app.rag.doc_card import build_card, logical_lines

# Đúng dạng MarkItDown sinh ra từ .docx: phần đầu và phần ký nằm trong bảng hai
# cột, các dòng trong một ô nối với nhau bằng khoảng trắng kép.
CONG_VAN_MD = """|  |  |
| --- | --- |
| CÔNG TY TNHH BÌNH PHÚC  Số: 18/CV-BP | CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM  Độc lập - Tự do - Hạnh phúc  *Hà Nội, ngày 18 tháng 9 năm 2026* |

*V/v: Kiểm kê và tổng hợp thông tin về nhân sự và trang thiết bị*

**Kính gửi:** Các phòng ban trực thuộc Công ty

Nội dung yêu cầu các đơn vị rà soát trang thiết bị trước ngày 25/9/2026.

|  |  |
| --- | --- |
| *Nơi nhận:*  - Như Kính gửi (để thực hiện); - Lưu: VT, HC-NS. | TỔNG GIÁM ĐỐC  Nguyễn Văn Phúc |
"""

BAO_CAO_MD = """### BÁO CÁO

Về kết quả thực hiện kiểm kê, tổng hợp tình hình nhân sự
và trang thiết bị tại Phòng Kỹ thuật

|  |  |
| --- | --- |
| PHÒNG KỸ THUẬT  Số: 09/BC-KT | *Hà Nội, ngày 19 tháng 9 năm 2026* |

**Kính gửi:** Ban Tổng Giám đốc
"""

TAI_LIEU_KY_THUAT_MD = """# Đặc tả hệ thống chatbot

## 1. Phạm vi
Hệ thống gồm ba thành phần: ingest, truy hồi và sinh câu trả lời.

## 2. Yêu cầu phi chức năng
Thời gian phản hồi dưới 3 giây.
"""


def test_no_hang_bang_thanh_tung_dong_dung_duoc():
    """Mẫu thể thức neo đầu dòng, mà MarkItDown nhét hết vào hàng bảng."""
    lines = logical_lines(CONG_VAN_MD)

    assert "Số: 18/CV-BP" in lines            # tách khỏi tên cơ quan cùng ô
    assert "TỔNG GIÁM ĐỐC" in lines           # tách khỏi họ tên ở dòng dưới
    assert "Nguyễn Văn Phúc" in lines
    assert not any(l.startswith("|") for l in lines)


def test_bo_dau_heading_markdown():
    """"### BÁO CÁO" phải thành "BÁO CÁO", nếu không mẫu tên loại trượt."""
    assert "BÁO CÁO" in logical_lines(BAO_CAO_MD)


def test_the_cong_van_du_thanh_phan():
    card = build_card(CONG_VAN_MD, "Cong_van_18")

    assert card is not None
    assert card.fields["so_ky_hieu"] == "18/CV-BP"
    assert card.fields["chu_ky"] == "TỔNG GIÁM ĐỐC Nguyễn Văn Phúc"
    assert "18 tháng 9 năm 2026" in card.fields["dia_danh_ngay"]
    assert "Kiểm kê" in card.fields["trich_yeu"]
    # Công văn không có tên loại - không được bịa ra.
    assert "ten_loai" not in card.fields


def test_nguoi_ky_gom_ca_chuc_danh_va_ho_ten():
    """Trả về mỗi "TỔNG GIÁM ĐỐC" thì không trả lời được câu "ai ký"."""
    card = build_card(CONG_VAN_MD)
    assert "Nguyễn Văn Phúc" in card.fields["chu_ky"]


def test_noi_nhan_thanh_danh_sach_sach():
    card = build_card(CONG_VAN_MD)
    assert card.fields["noi_nhan"] == "Như Kính gửi (để thực hiện); Lưu: VT, HC-NS"


def test_trich_yeu_bi_ngat_dong_van_ghep_lai_du():
    """Word ngắt dòng giữa mệnh đề; lấy mỗi dòng đầu thì câu cụt."""
    card = build_card(BAO_CAO_MD)

    assert card.fields["ten_loai"] == "BÁO CÁO"
    assert "trang thiết bị tại Phòng Kỹ thuật" in card.fields["trich_yeu"]


def test_khong_dung_the_cho_tai_lieu_khong_phai_van_ban_hanh_chinh():
    assert build_card(TAI_LIEU_KY_THUAT_MD, "Đặc tả hệ thống") is None


def test_the_mang_ten_tai_lieu_de_hoi_theo_ten_van_ban():
    card = build_card(CONG_VAN_MD, "Cong_van_chi_thi_kiem_ke")
    assert card.text.startswith("Cong_van_chi_thi_kiem_ke.")
    assert "Người ký: TỔNG GIÁM ĐỐC Nguyễn Văn Phúc" in card.text

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
    # Công văn KHÔNG in dòng tên loại, nhưng vẫn có tên loại trong thẻ - suy từ
    # mã "CV" của số ký hiệu. Bản đầu của test này chốt ngược lại
    # (`"ten_loai" not in card.fields`, lý do "không được bịa ra"); đổi ngày
    # 20/09/2026 vì hai lẽ:
    #
    #   1. Đọc mã loại trong số ký hiệu không phải bịa. Trong thể thức văn bản
    #      hành chính Việt Nam, mã đó CHÍNH LÀ loại văn bản - "18/CV-BP" nói nó
    #      là công văn cũng chắc chắn như một dòng tiêu đề.
    #   2. Thiếu trường này gây hại thật: hỏi "ai ký công văn chỉ thị kiểm kê"
    #      trả về thẻ này (đúng tài liệu, hạng 1) nằm cạnh thẻ của một BÁO CÁO
    #      có ghi rõ loại. Model không xác nhận được cái nào là công văn nên
    #      trả lời "không tìm thấy", dù người ký nằm ngay trong thẻ.
    #
    # Ranh giới cũ vẫn giữ: mã lạ thì bỏ trống, xem `test_ma_loai_la_thi_bo_trong`.
    assert card.fields["ten_loai"] == "Công văn"


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


# ---------------------------------------------- suy tên loại từ số ký hiệu -- #
def test_cong_van_khong_in_ten_loai_van_co_ten_loai():
    """Công văn thật KHÔNG in chữ "CÔNG VĂN" lên đầu - khác báo cáo, quyết định.

    Thiếu trường này thì hỏi "ai ký CÔNG VĂN chỉ thị kiểm kê" trả về một thẻ
    không tự khai mình là công văn, nằm cạnh một thẻ ghi rõ "Loại văn bản: BÁO
    CÁO" - model không dám khẳng định và trả lời "không tìm thấy", dù người ký
    nằm ngay trong thẻ. Đã gặp thật ngày 20/09/2026.
    """
    from app.rag.doc_card import build_card

    text = (
        "CÔNG TY TNHH BÌNH PHÚC  Số: 18/CV-BP\n"
        "Hà Nội, ngày 18 tháng 9 năm 2026\n"
        "V/v: Kiểm kê và tổng hợp thông tin về nhân sự\n"
        "Kính gửi: Các phòng ban trực thuộc\n"
        "TỔNG GIÁM ĐỐC\n"
        "Nguyễn Văn Phúc\n"
    )
    card = build_card(text, "Cong_van_chi_thi")

    assert card is not None
    assert card.fields["ten_loai"] == "Công văn"
    assert "Loại văn bản: Công văn" in card.text


def test_ten_loai_in_san_thi_khong_bi_de():
    """Văn bản tự in tên loại thì giữ nguyên chữ của nó, không thay bằng bảng tra."""
    from app.rag.doc_card import build_card

    text = ("BÁO CÁO\nSố: 09/BC-KT\nHà Nội, ngày 19 tháng 9 năm 2026\n"
            "Kính gửi: Ban Tổng Giám đốc\n")
    card = build_card(text, "Bao_cao")

    assert card.fields["ten_loai"] == "BÁO CÁO"


def test_ma_loai_la_thi_bo_trong():
    """Thà thiếu trường còn hơn ghi sai loại văn bản."""
    from app.rag.doc_card import _ten_loai_tu_ky_hieu

    assert _ten_loai_tu_ky_hieu("99/XYZ-AB") == ""
    assert _ten_loai_tu_ky_hieu("khong-phai-so-ky-hieu") == ""
    assert _ten_loai_tu_ky_hieu("") == ""


def test_suy_ten_loai_khong_lam_tep_thuong_thanh_van_ban():
    """Mã loại chỉ đọc lại một trường đã có, không được tính vào MIN_FIELDS.

    Tính vào thì một tệp chỉ tình cờ chứa chuỗi giống số ký hiệu cũng đủ điểm
    để sinh thẻ - đúng thứ MIN_FIELDS sinh ra để chặn.
    """
    from app.rag.doc_card import build_card

    assert build_card("Tham chiếu hợp đồng Số: 18/CV-BP trong phụ lục.", "ghi_chu") is None

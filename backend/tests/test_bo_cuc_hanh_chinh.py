"""Dựng lại bố cục hai cột / căn giữa cho phần đầu văn bản hành chính."""

from __future__ import annotations

from app.documents.bo_cuc_hanh_chinh import NGAN_COT, dung_bo_cuc, noi_dong_bi_ngat
from app.rag.converter import cleanup_markdown

# Đúng hình dạng OCR trả về cho một tờ đơn khởi kiện có đóng dấu "công văn đến".
DON_KHOI_KIEN = """TÒA ÁN NHÂN DÂN T. QUẢNG NAM
CÔNG VĂN ĐẾN
Số: 21/
Ngày: 21/01/09

NHNo&PTNT TỈNH QUẢNG NAM
Chi nhánh Tam Đàn
Số: 15/NHNo-TD

CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM
Độc lập – Tự do – Hạnh phúc
Tam Đàn, ngày 19 tháng 01 năm 2009

ĐƠN KHỞI KIỆN
Đòi tiền nợ vay tại chi nhánh NHNo&PTNT Tam Đàn

BÊN KHỞI KIỆN
Ngân hàng No&PTNT Việt Nam"""


def _dong(text: str) -> list[str]:
    return [d for d in text.split("\n") if d.strip()]


def test_co_quan_va_quoc_hieu_thanh_hai_cot():
    ra = dung_bo_cuc(DON_KHOI_KIEN)
    assert f"NHNo&PTNT TỈNH QUẢNG NAM {NGAN_COT} CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM" in ra
    assert f"Chi nhánh Tam Đàn {NGAN_COT} Độc lập – Tự do – Hạnh phúc" in ra
    assert f"Số: 15/NHNo-TD {NGAN_COT} Tam Đàn, ngày 19 tháng 01 năm 2009" in ra


def test_ten_van_ban_duoc_can_giua_cung_phu_de():
    ra = dung_bo_cuc(DON_KHOI_KIEN)
    khoi = ra.split("::: giua")[1].split(":::")[0]
    assert "ĐƠN KHỞI KIỆN" in khoi
    assert "Đòi tiền nợ vay" in khoi
    # Mục thật của văn bản KHÔNG được kéo vào khối căn giữa.
    assert "BÊN KHỞI KIỆN" not in khoi


def test_con_dau_tach_rieng_khong_thanh_cot_trai():
    """Dấu có "Số:" như cơ quan ban hành, nhưng phải đứng riêng.

    Nhầm dấu thành cơ quan ban hành là lỗi lặng: tiêu ngữ vẫn ra hai cột, chỉ là
    cột trái ghi tên toà án nhận đơn thay vì tên ngân hàng gửi đơn.
    """
    ra = dung_bo_cuc(DON_KHOI_KIEN)
    khoi_dau = ra.split("::: dau")[1].split(":::")[0]
    assert "CÔNG VĂN ĐẾN" in khoi_dau
    assert "TÒA ÁN NHÂN DÂN" in khoi_dau
    assert "TÒA ÁN" not in ra.split("::: tieu-ngu")[1].split(":::")[0]


def test_chi_co_dau_khong_co_co_quan_thi_cot_trai_de_trong():
    text = "TÒA ÁN TỈNH X\nCÔNG VĂN ĐẾN\nSố: 21/\nNgày: 21/01/09\n\n" \
           "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM\nĐộc lập - Tự do - Hạnh phúc"
    hai_cot = dung_bo_cuc(text).split("::: tieu-ngu")[1].split(":::")[0]
    assert "TÒA ÁN" not in hai_cot
    assert hai_cot.strip().startswith(NGAN_COT)      # cột trái rỗng


def test_khong_thay_quoc_hieu_thi_tra_nguyen_van():
    """Đoán bố cục cho thứ không phải văn bản hành chính còn tệ hơn để tuyến tính."""
    text = "Báo cáo tiến độ quý 3\n\nDoanh thu tăng 12%.\n\nChi phí giảm 4%."
    assert dung_bo_cuc(text) == text


def test_cleanup_khong_dung_vao_ben_trong_khoi_bo_cuc():
    """`cleanup_markdown` chạy sau, không được biến "ĐƠN KHỞI KIỆN" thành `##`.

    Gán heading bên trong khối căn giữa thì vừa vỡ khối, vừa mất căn giữa - và
    khối hai cột thì mất luôn dấu ngăn cột.
    """
    ra = cleanup_markdown(dung_bo_cuc(DON_KHOI_KIEN))
    assert "::: giua\nĐƠN KHỞI KIỆN" in ra
    assert "## ĐƠN KHỞI KIỆN" not in ra
    assert NGAN_COT in ra
    # Mục thật ngoài khối vẫn phải được nâng thành tiêu đề.
    assert "## BÊN KHỞI KIỆN" in ra


def test_quoc_hieu_khong_bao_gio_thanh_tieu_de():
    """Đường nạp kho không gọi `dung_bo_cuc`, nên chốt chặn phải nằm ở cleanup.

    `app.rag.chunking` cắt chunk theo heading: để quốc hiệu thành `##` thì mỗi
    tờ công văn bị cắt bậy ngay tại đó và lấy chính dòng đó làm nhãn chương.
    """
    ra = cleanup_markdown("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM\nNội dung công văn.")
    assert "## CỘNG HÒA" not in ra
    assert "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM" in ra


def test_ban_an_khong_co_so_hieu_canh_ten_co_quan_van_ra_hai_cot():
    """Bản án ghi số hiệu ở khối riêng bên dưới, không nằm cùng tên toà.

    Đòi hỏi khối cơ quan ban hành phải có "Số:" thì bỏ sót đúng loại tài liệu
    hay gặp nhất - cột trái trống trơn còn tên toà thì trôi lên trên.
    """
    text = ("TÒA ÁN NHÂN DÂN\nTỈNH QUẢNG NGÃI\n\n"
            "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM\nĐộc lập - Tự do - Hạnh phúc\n\n"
            "Bản án số: 13/2022/HC-ST\nNgày: 27-4-2022")
    hai_cot = dung_bo_cuc(text).split("::: tieu-ngu")[1].split(":::")[0]
    assert f"TÒA ÁN NHÂN DÂN {NGAN_COT} CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM" in hai_cot
    assert f"TỈNH QUẢNG NGÃI {NGAN_COT} Độc lập - Tự do - Hạnh phúc" in hai_cot


def test_doan_van_thuong_truoc_quoc_hieu_khong_bi_keo_thanh_cot_trai():
    """Chỉ tên cơ quan (viết hoa, ngắn) mới được làm cột trái."""
    text = ("Kính gửi các đơn vị trực thuộc về việc nộp báo cáo quý.\n\n"
            "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM\nĐộc lập - Tự do - Hạnh phúc")
    ra = dung_bo_cuc(text)
    assert "Kính gửi các đơn vị" not in ra.split("::: tieu-ngu")[1].split(":::")[0]
    assert "Kính gửi các đơn vị" in ra


def test_quoc_hieu_da_la_heading_san_cung_bi_ha_xuong():
    """Bộ trích xuất PDF tự gắn `#` cho chữ to/đậm, nên quốc hiệu tới nơi đã là
    heading rồi - chốt chặn chỉ đặt ở nhánh đoán chữ hoa thì hụt mất ca này."""
    ra = cleanup_markdown("## **CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM**\nNội dung.")
    assert not ra.startswith("#")
    assert "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM" in ra


def test_tieu_de_that_van_duoc_giu_nguyen_la_heading():
    ra = cleanup_markdown("## Điều 7. Mức phụ cấp\nNội dung điều 7.")
    assert "## Điều 7. Mức phụ cấp" in ra


# ------------------------------------------------- nối dòng bị ngắt ------- #
def test_noi_lai_cau_bi_ngat_giua_chung():
    text = ("Đại diện là chi nhánh Ngân hàng Nông nghiệp và Phát triển nông thôn\n"
            "(NHNo&PTNT) Tam Đàn, tỉnh Quảng Nam.")
    assert noi_dong_bi_ngat(text) == (
        "Đại diện là chi nhánh Ngân hàng Nông nghiệp và Phát triển nông thôn "
        "(NHNo&PTNT) Tam Đàn, tỉnh Quảng Nam."
    )


def test_khong_dinh_hai_truong_khac_nhau_lam_mot():
    """"Địa chỉ" và "Người đại diện" là hai trường - nối vào nhau là sai nghĩa."""
    text = ("Địa chỉ: Thôn Đàn Hạ, xã Tam Đàn, huyện Phú Ninh, tỉnh Quảng Nam.\n"
            "Người đại diện: Ông Lê Thạnh\n"
            "Chức vụ: Giám Đốc chi nhánh")
    assert noi_dong_bi_ngat(text).count("\n") == 2


def test_dong_ngan_khong_bi_keo_vao_dong_sau():
    """Dòng ngắn là dòng cố ý kết thúc, không phải dòng bị ngắt vì hết chỗ."""
    text = ("Ngân hàng No&PTNT Việt Nam\n"
            "đại diện bởi một người nào đó rất dài dòng cho đủ chiều ngang khối này")
    assert "\n" in noi_dong_bi_ngat(text)


def test_khong_noi_qua_ranh_gioi_khoi():
    text = "một dòng chưa kết thúc và đủ dài để chạm mép phải của khối\n\ntiếp theo"
    assert "\n\n" in noi_dong_bi_ngat(text)


def test_nhan_kem_muc_duoc_tach_cho_dong_bo():
    """"Kính gửi: - A;" rồi "- B." - hai mục cùng danh sách phải hiện cùng kiểu."""
    ra = noi_dong_bi_ngat("Kính gửi: - Toà án nhân dân tỉnh Quảng Nam;\n- Toà kinh tế.")
    assert ra == "Kính gửi:\n- Toà án nhân dân tỉnh Quảng Nam;\n- Toà kinh tế."


def test_cau_binh_thuong_co_dau_gach_khong_bi_tach():
    """Không có mục nào ở dưới thì đó là câu thường, đừng biến thành danh sách."""
    text = "Ghi chú: - chỉ là một dấu gạch trong câu."
    assert noi_dong_bi_ngat(text) == text


# ------------------------------------------------------------ bảng ------- #
BANG = (
    "| STT | Tên ngành | Mã ngành |\n"
    "| --- | --- | --- |\n"
    "| 1 | Mua bán hàng thuỷ sản | 4620 |\n"
    "| 2 | Kinh doanh dịch vụ khách sạn | 5510 |"
)


def _so_cot(md: str) -> set[int]:
    """Số dấu | trên mỗi hàng BẢNG. Bỏ qua dòng `|||` của khối tiêu ngữ - nó
    cũng mở đầu bằng | nhưng là dấu ngăn cột của khối hai cột, không phải bảng."""
    return {
        d.count("|") for d in md.split("\n")
        if d.strip().startswith("|") and NGAN_COT not in d
    }


def test_bang_khong_bi_noi_dong():
    assert noi_dong_bi_ngat(BANG) == BANG
    assert _so_cot(noi_dong_bi_ngat(BANG)) == {4}


def test_hang_bang_khong_nuot_dong_chu_ngay_sau():
    """Nối vào là có chữ nằm NGOÀI dấu | cuối, hàng lệch cột, vỡ cả bảng.

    Dòng sau bắt đầu bằng chữ thường nên mọi điều kiện nối khác đều thoả - chỉ
    riêng việc dòng trước là hàng bảng mới chặn được.
    """
    t = "| 18 | Cho thuê tài sản, xe ô tô | 7740, 7710 |\nghi chú thêm"
    assert noi_dong_bi_ngat(t) == t


def test_bang_qua_ca_chuoi_xu_ly_van_deu_cot():
    """OCR -> nối dòng -> dựng bố cục -> làm sạch, bảng phải nguyên vẹn."""
    trang = ("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM\nĐộc lập - Tự do - Hạnh phúc\n\n"
             "3. Ngành nghề kinh doanh:\n" + BANG + "\n\n4. Vốn điều lệ: 6.000.000.000 đồng")
    ra = cleanup_markdown(dung_bo_cuc(noi_dong_bi_ngat(trang)))
    assert _so_cot(ra) == {4}
    assert "| --- | --- | --- |" in ra

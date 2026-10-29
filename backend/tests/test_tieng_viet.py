"""Dựng lại khoảng trắng bị PDF LaTeX nuốt mất.

Hai phía phải cùng đúng thì mới dùng được: cắt ra chữ đọc được, VÀ không đụng
vào chữ vốn đã đúng. Vế sau khó hơn và là chỗ bài test này soi kỹ nhất - tách
nhầm "được" thành "đư ợc" thì hỏng nặng hơn hẳn việc bỏ sót một chữ dính.
"""

from __future__ import annotations

import pytest

from app.rag.tieng_viet import la_am_tiet, sua_dinh_chu, tach_am_tiet, von_tu_cua


# --------------------------------------------------------------- âm tiết -- #
@pytest.mark.parametrize("tu", [
    "tiếng", "được", "người", "khoảng", "huấn", "luyện", "nghiêng", "thuyết",
    "quyền", "quốc", "giếng", "gì", "ngoằn", "khuếch", "ưu", "yêu", "ý", "ở",
    "uống", "nghỉ", "trách", "kết", "bịp",
])
def test_am_tiet_viet_dung_thi_duoc_nhan(tu):
    assert la_am_tiet(tu)


@pytest.mark.parametrize("tu, vi_sao", [
    ("khảt", "vần tắc 'at' không mang được thanh hỏi"),
    ("trảch", "vần tắc 'ach' không mang được thanh hỏi"),
    ("đểt", "vần tắc 'et' không mang được thanh hỏi"),
    ("iếp", "vần 'iêp' không có phụ âm đầu thì phải viết 'yếp'"),
    ("khat", "vần tắc bắt buộc có thanh sắc hoặc nặng"),
    ("bộhọc", "hai dấu thanh trong một cụm là hai âm tiết dính nhau"),
    ("client", "không phải khuôn âm tiết tiếng Việt"),
])
def test_chuoi_khong_phai_am_tiet_thi_bi_loai(tu, vi_sao):
    assert not la_am_tiet(tu), vi_sao


# ------------------------------------------------------------ cắt âm tiết -- #
@pytest.mark.parametrize("dinh, roi", [
    ("bộhọc", "bộ học"),
    ("khảnăng", "khả năng"),
    ("sốbộhọc", "số bộ học"),
    ("tấtcảtrọngsố", "tất cả trọng số"),
    ("huấnluyện", "huấn luyện"),
    ("trảvềmô", "trả về mô"),
    ("Cảhai", "Cả hai"),
    ("vềkhoảng", "về khoảng"),
])
def test_cat_chu_dinh_thanh_am_tiet(dinh, roi):
    assert " ".join(tach_am_tiet(dinh)) == roi


@pytest.mark.parametrize("dinh, roi", [
    # Luật ngữ âm phải thắng cách cắt "sớm hơn" nhưng sai:
    ("khảthi", "khả thi"),      # không được ra "khảt hi"
    ("kếtiếp", "kế tiếp"),      # không được ra "kết iếp"
    ("đểthu", "để thu"),        # không được ra "đểt hu"
    ("trảchi", "trả chi"),      # không được ra "trảch i"
    ("bịphi", "bị phi"),        # không được ra "bịp hi"
])
def test_luat_ngu_am_gat_cach_cat_sai(dinh, roi):
    assert " ".join(tach_am_tiet(dinh)) == roi


@pytest.mark.parametrize("cum", [
    "entropytrọngsốnhãn",       # "entropy" vỡ vừa khít thành "en tro py"
    "AdaBoostduytrìmộtphân",
    "sốepoch",
])
def test_tu_nuoc_ngoai_dinh_vao_chu_viet_thi_de_nguyen(cum):
    """Cắt bậy đẻ ra một từ không có thật; để dính thì người đọc vẫn đoán ra."""
    assert tach_am_tiet(cum, von_tu_cua("duy nhất một phân lớp")) == [cum]


def test_mot_manh_khong_dau_le_loi_van_duoc_nhan():
    # Vế sau không dấu nhưng đứng một mình -> vẫn là chữ Việt thật.
    assert tach_am_tiet("khảthi") == ["khả", "thi"]
    assert tach_am_tiet("thểmang") == ["thể", "mang"]


def test_von_tu_tai_lieu_go_the_be_khi_ngu_am_hoa():
    """"tínhiệu" cắt được cả hai đường; chữ tài liệu đã dùng là bên thắng."""
    # Không có bằng chứng: phụ âm dồn về âm tiết sau, ra "tí nhiệu".
    assert tach_am_tiet("tínhiệu") == ["tí", "nhiệu"]
    # Tài liệu có "tín" và "hiệu" đứng rời ở chỗ khác -> chọn đúng.
    von = von_tu_cua("Bộ thu nhận tín hiệu điều khiển.")
    assert tach_am_tiet("tínhiệu", von) == ["tín", "hiệu"]


# ------------------------------------------------------- không phá chữ đúng -- #
CAU_DUNG = (
    "Điều 7 quy định mức phụ cấp được hưởng từ ngày 01/01/2026.",
    "Kết quả huấn luyện trên tập dữ liệu Fashion-MNIST và CIFAR-10.",
    "Người lao động nghỉ việc phải báo trước ba mươi ngày.",
    "Trường Đại học Bách khoa Hà Nội, Việt Nam.",
)


@pytest.mark.parametrize("cau", CAU_DUNG)
def test_cau_viet_dung_khong_bi_dong_toi(cau):
    assert sua_dinh_chu(cau) == cau


@pytest.mark.parametrize("tu", [
    "FedEABoost", "AdaBoost", "boosting", "Batman", "Créteil", "MNIST",
    "self-paced", "http://tpvtech.vn/tai-lieu", "H_i", "top5",
])
def test_chu_nuoc_ngoai_va_ky_hieu_giu_nguyen(tu):
    assert sua_dinh_chu(f"Xem {tu} ở trên.") == f"Xem {tu} ở trên."


def test_cat_tai_cho_noi_chu_thuong_voi_chu_hoa():
    assert sua_dinh_chu("dựa trên cơ sởFL truyền thống") == "dựa trên cơ sở FL truyền thống"
    assert sua_dinh_chu("khác từAdaBoost gốc") == "khác từ AdaBoost gốc"


def test_von_tu_lay_tu_ca_tai_lieu_chu_khong_rieng_doan_dang_sua():
    tai_lieu = "Máy thu tín hiệu.\n\nBộ giải mã tínhiệu đầu vào."
    assert "mã tín hiệu đầu vào" in sua_dinh_chu(tai_lieu, von_tu_cua(tai_lieu))

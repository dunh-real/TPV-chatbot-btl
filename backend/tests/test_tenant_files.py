"""File đầu ra của thuê bao này không được lọt sang thuê bao khác.

Thư mục `output` là kho dùng chung và tên file đoán được ("SLIDE_2026-08__t64.pptx"),
nên nếu cửa tải về chỉ kiểm "có nằm trong thư mục output không" thì bất kỳ ai gọi
được API cũng lấy được báo cáo của đơn vị khác. Trước đây tên file lại còn không
mang tenant, nên hai đơn vị xin báo cáo cùng một kỳ còn ghi đè lên nhau.
"""

from __future__ import annotations

import pytest

from app.core.context import Principal, use_principal
from app.services import storage


@pytest.fixture
def output_dir(tmp_path, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "output_dir", str(tmp_path))
    return tmp_path


def test_ten_file_mang_tenant(output_dir):
    with use_principal(Principal(tenant_id=64)):
        assert storage.tenant_stem("SLIDE_2026-08") == "SLIDE_2026-08__t64"
    with use_principal(Principal(tenant_id=63)):
        assert storage.tenant_stem("SLIDE_2026-08") == "SLIDE_2026-08__t63"


def test_hai_tenant_khong_con_ghi_de_nhau(output_dir):
    """Cùng kỳ, cùng loại báo cáo - phải ra hai file khác nhau."""
    ten = []
    for tenant in (63, 64):
        with use_principal(Principal(tenant_id=tenant)):
            ten.append(storage.tenant_stem("BC_TONGHOP_2026-08"))
    assert ten[0] != ten[1]


def test_khong_tai_duoc_file_cua_tenant_khac(output_dir):
    (output_dir / "SLIDE_2026-08__t63.pptx").write_bytes(b"so lieu cua tenant 63")

    with use_principal(Principal(tenant_id=63)):
        assert storage.resolve_output("SLIDE_2026-08__t63.pptx").path.is_file()

    with use_principal(Principal(tenant_id=64)):
        with pytest.raises(storage.StorageError):
            storage.resolve_output("SLIDE_2026-08__t63.pptx")


def test_file_khong_mang_dau_tenant_bi_khoa(output_dir):
    """File sinh ra trước khi có cơ chế này chứa số liệu của thuê bao không rõ."""
    (output_dir / "SLIDE_2026-08.pptx").write_bytes(b"cu")

    with use_principal(Principal(tenant_id=64)):
        with pytest.raises(storage.StorageError):
            storage.resolve_output("SLIDE_2026-08.pptx")

    # Không đặt tenant (script chạy tay) thì vẫn đọc được file không dấu.
    with use_principal(Principal(tenant_id=None)):
        assert storage.resolve_output("SLIDE_2026-08.pptx").path.is_file()


def test_bao_loi_khong_lo_ra_file_co_that(output_dir):
    """Nói "file của thuê bao khác" là đã xác nhận nó tồn tại."""
    (output_dir / "BC_TONGHOP_2026-08__t63.docx").write_bytes(b"x")

    with use_principal(Principal(tenant_id=64)):
        with pytest.raises(storage.StorageError) as co_that:
            storage.resolve_output("BC_TONGHOP_2026-08__t63.docx")
        with pytest.raises(storage.StorageError) as khong_co:
            storage.resolve_output("BC_TONGHOP_2026-08__t64.docx")
    assert "Không tìm thấy file" in str(co_that.value)
    assert "Không tìm thấy file" in str(khong_co.value)


# --------------------------------------------------------------------------- #
# Mỗi lần tạo là một file mới
# --------------------------------------------------------------------------- #
def test_tao_lai_ra_ten_file_khac(output_dir):
    """Tên cố định thì tạo lần hai ghi đè lần một, và trình duyệt lưu bản mới
    thành "... (1).pptx" - người dùng mở lại bản đầu rồi kết luận chưa sửa gì."""
    import time

    with use_principal(Principal(tenant_id=64)):
        mot = storage.versioned_stem("SLIDE_2026-08")
        time.sleep(1.05)
        hai = storage.versioned_stem("SLIDE_2026-08")

    assert mot != hai
    assert mot.startswith("SLIDE_2026-08__t64__")


def test_ten_co_moc_thoi_gian_van_doc_duoc_tenant(output_dir):
    """Dấu tenant không còn đứng cuối tên, phép kiểm chéo thuê bao phải vẫn đúng."""
    with use_principal(Principal(tenant_id=64)):
        ten = storage.versioned_stem("BC_TONGHOP_2026-08") + ".docx"
    (output_dir / ten).write_bytes(b"x")

    assert storage.tenant_of(ten) == 64
    with use_principal(Principal(tenant_id=64)):
        assert storage.resolve_output(ten).path.is_file()
    with use_principal(Principal(tenant_id=63)):
        with pytest.raises(storage.StorageError):
            storage.resolve_output(ten)

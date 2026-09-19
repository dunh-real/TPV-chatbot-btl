"""Lớp dữ liệu: đọc ERP theo kỳ, và phần dữ liệu app tự ghi.

Trọng tâm là phép "as-of": ERP chỉ giữ hiện trạng, nên số liệu của một kỳ được
dựng lại từ mốc tạo/xoá bản ghi. Sai ở đây là sai toàn bộ báo cáo, nên các mốc
thời gian trong dữ liệu mẫu (`tests/conftest.py`) được chọn để bắt đúng hai ca
khó: trang bị mua sau kỳ và trang bị đã thanh lý.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.erp_repository import (
    ErpDonViRepository,
    ErpNhanSuRepository,
    ErpTaiNguyenRepository,
    ErpTrangBiRepository,
    period_end,
    period_start,
)
from app.db.models import Base, TemplateBaoCao, VanBan
from app.db.repository import TemplateRepository, VanBanRepository


@pytest.fixture
async def session():
    """CSDL app - chỉ còn mẫu báo cáo và sổ văn bản."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([
            TemplateBaoCao(
                ma_template="BC_THANG", ten_bao_cao="Báo cáo tháng",
                loai_bao_cao="dinh_ky", mo_ta="Báo cáo định kỳ hằng tháng",
                truong_du_lieu=json.dumps({"muc": ["quan_so", "trang_bi"]}),
            ),
            VanBan(ma_van_ban="10/BC-DV02", ten_van_ban="Báo cáo Đơn vị 2",
                   loai_van_ban="bao_cao_di", noi_gui="Đơn vị 2",
                   noi_nhan="Ban Giám đốc", ngay_van_ban=date(2026, 8, 20),
                   file_path="", dang_file="docx"),
        ])
        await session.commit()
        yield session
    await engine.dispose()


# ----------------------------------------------------- mốc chốt kỳ ------- #
def test_moc_chot_ky_la_dau_thang_ke_tiep():
    assert period_start("2026-08") == datetime(2026, 8, 1)
    assert period_end("2026-08") == datetime(2026, 9, 1)


def test_moc_chot_ky_vat_qua_nam():
    assert period_end("2026-12") == datetime(2027, 1, 1)


# ------------------------------------------------------- phòng ban ------- #
async def test_danh_muc_phong_ban(erp_session):
    catalog = await ErpDonViRepository(erp_session).catalog()
    assert [c["ma_phong_ban"] for c in catalog] == ["00001", "00002"]
    assert catalog[0]["ten_phong_ban"] == "Đơn vị 1"


async def test_ma_don_vi_noi_duoc_sang_khoa_chinh(erp_session):
    assert await ErpDonViRepository(erp_session).id_map() == {"00001": 1, "00002": 2}


# --------------------------------------------- trang bị theo "as-of" ----- #
async def test_trang_bi_mua_sau_ky_khong_duoc_tinh(erp_session):
    """Xe công vụ mua 12/8 - kỳ tháng 7 chốt ngày 1/8 nên chưa được tính."""
    rows = await ErpTrangBiRepository(erp_session).list_as_of(period_end("2026-07"))
    assert "Xe công vụ" not in {asset.name for _, asset, _ in rows}


async def test_trang_bi_da_thanh_ly_bien_khoi_ky_sau(erp_session):
    """Máy chiếu thanh lý 3/8: còn ở kỳ tháng 7, mất ở kỳ tháng 8."""
    repo = ErpTrangBiRepository(erp_session)
    thang_7 = {asset.name for _, asset, _ in await repo.list_as_of(period_end("2026-07"))}
    thang_8 = {asset.name for _, asset, _ in await repo.list_as_of(period_end("2026-08"))}
    assert "Máy chiếu" in thang_7
    assert "Máy chiếu" not in thang_8


async def test_tong_trang_bi_doi_theo_ky(erp_session):
    repo = ErpTrangBiRepository(erp_session)
    tong = lambda rows: sum(a.so_luong for _, a, _ in rows)  # noqa: E731
    # Tháng 7: máy in 8 + máy chủ 6 + máy chiếu 4 = 18
    assert tong(await repo.list_as_of(period_end("2026-07"))) == 18
    # Tháng 8: máy in 8 + máy chủ 6 + xe công vụ 2 = 16 (máy chiếu đã thanh lý)
    assert tong(await repo.list_as_of(period_end("2026-08"))) == 16


async def test_loc_theo_phong_ban(erp_session):
    rows = await ErpTrangBiRepository(erp_session).list_as_of(period_end("2026-08"), [1])
    assert {asset.name for _, asset, _ in rows} == {"Máy in"}


async def test_lay_kem_ten_chung_loai(erp_session):
    rows = await ErpTrangBiRepository(erp_session).list_as_of(period_end("2026-08"), [1])
    assert rows[0][2] == "Thiết bị"


# ------------------------------------------- quân số theo "as-of" -------- #
async def test_quan_so_tru_nguoi_da_nghi_viec(erp_session):
    """Lê Văn C nghỉ 15/8: còn tính ở kỳ tháng 7, hết tính ở kỳ tháng 8."""
    repo = ErpNhanSuRepository(erp_session)
    assert (await repo.headcount_by_dept(period_end("2026-07")))[1] == 3
    assert (await repo.headcount_by_dept(period_end("2026-08")))[1] == 2


async def test_quan_so_cong_nguoi_moi_vao(erp_session):
    repo = ErpNhanSuRepository(erp_session)
    assert (await repo.headcount_by_dept(period_end("2026-07")))[2] == 2
    assert (await repo.headcount_by_dept(period_end("2026-08")))[2] == 3


async def test_bien_dong_nhan_su_trong_ky(erp_session):
    bien_dong = await ErpNhanSuRepository(erp_session).movement(
        period_start("2026-08"), period_end("2026-08")
    )
    assert bien_dong["tuyen_moi"] == {2: 1}
    assert bien_dong["nghi_viec"] == {1: 1}


# ---------------------------------------------- gộp thành tài nguyên ----- #
async def test_tai_nguyen_don_vi_theo_ky(erp_session):
    tai_nguyen = await ErpTaiNguyenRepository(erp_session).get_tai_nguyen("00002", "2026-08")
    assert tai_nguyen.ten_don_vi == "Đơn vị 2"
    assert tai_nguyen.quan_so == 3
    assert tai_nguyen.tong_trang_bi == 8      # máy chủ 6 + xe công vụ 2
    assert tai_nguyen.ky == "2026-08"


async def test_tai_nguyen_kem_canh_bao_so_lieu_suy_nguoc(erp_session):
    """Người ký phải biết số của kỳ cũ là hiện trạng suy ngược, không phải số đã chốt."""
    tai_nguyen = await ErpTaiNguyenRepository(erp_session).get_tai_nguyen("00001", "2026-07")
    assert "suy ngược" in tai_nguyen.as_dict()["ghi_chu"] or tai_nguyen.ghi_chu


async def test_don_vi_khong_ton_tai(erp_session):
    assert await ErpTaiNguyenRepository(erp_session).get_tai_nguyen("KHONG_CO") is None


async def test_chua_khai_bao_ma_trang_thai_thi_khong_ket_luan_bao_duong(erp_session):
    """Không biết mã trạng thái nào là "tốt" thì phải im lặng, không đoán."""
    tai_nguyen = await ErpTaiNguyenRepository(erp_session).get_tai_nguyen("00002", "2026-08")
    assert all(tb["tinh_trang_tot"] is None for tb in tai_nguyen.trang_bi)
    assert "so_loai_can_bao_duong" not in tai_nguyen.as_dict()


async def test_khai_bao_ma_trang_thai_thi_ket_luan_duoc(erp_session):
    from app.core.config import get_settings

    settings = get_settings()
    truoc = settings.erp_asset_status_good
    settings.erp_asset_status_good = "0"
    try:
        tai_nguyen = await ErpTaiNguyenRepository(erp_session).get_tai_nguyen("00002", "2026-08")
        # Máy chủ mã 0 (tốt), xe công vụ mã 1 (không tốt).
        assert tai_nguyen.as_dict()["so_loai_can_bao_duong"] == 1
    finally:
        settings.erp_asset_status_good = truoc


# ------------------------------------------------------ chốt chặn ghi ---- #
async def test_engine_erp_chan_moi_cau_lenh_ghi():
    """Chốt này phải chặn được cả lệnh ghi nguỵ trang sau chú thích SQL."""
    from app.db.erp_session import ReadOnlyViolation, _reject_writes

    for stmt in ["UPDATE Asm_Assets SET Quantity = 1",
                 "INSERT INTO Asm_Assets (Code) VALUES ('x')",
                 "DELETE FROM Dms_WorkDepartment",
                 "DROP TABLE Asm_Assets",
                 "  /* vô hại */ UPDATE Asm_Assets SET Quantity = 1",
                 "-- vô hại\nDELETE FROM Asm_Assets",
                 "EXEC sp_executesql N'DELETE FROM Asm_Assets'"]:
        with pytest.raises(ReadOnlyViolation):
            _reject_writes(None, None, stmt, None, None, False)


def test_chot_chan_khong_can_tro_cau_doc():
    """Tên cột chứa chữ "update" không được làm câu SELECT bị chặn oan."""
    from app.db.erp_session import _reject_writes

    for stmt in ["SELECT LastModificationTime FROM Asm_Assets",
                 "SELECT Name FROM Asm_Assets WHERE Description LIKE '%delete%'",
                 "WITH x AS (SELECT 1 AS a) SELECT a FROM x"]:
        _reject_writes(None, None, stmt, None, None, False)


async def test_danh_sach_bang_duoc_phep_khop_pham_vi_duoc_cap():
    from app.db.erp_session import ALLOWED_TABLES
    from app.db.erp_models import Asset, AssetCategory, EmployeeProfile, WorkDepartment

    for model in (Asset, AssetCategory, EmployeeProfile, WorkDepartment):
        assert model.__tablename__ in ALLOWED_TABLES


# --------------------------------------------- dữ liệu app tự quản lý ---- #
async def test_danh_muc_mau_bao_cao(session):
    catalog = await TemplateRepository(session).catalog()
    assert catalog[0]["ma_template"] == "BC_THANG"


async def test_truong_du_lieu_doc_ra_json(session):
    template = await TemplateRepository(session).get("BC_THANG")
    assert template.fields["muc"] == ["quan_so", "trang_bi"]


async def test_so_van_ban_ghi_va_doc_lai(session):
    repo = VanBanRepository(session)
    await repo.link_doc_id("10/BC-DV02", "doc-abc")
    assert (await repo.get_by_doc_id("doc-abc")).ma_van_ban == "10/BC-DV02"


async def test_erp_va_app_la_hai_co_so_du_lieu_tach_roi(erp_session, session):
    """Bảng app không được rơi vào ERP: `create_all` của app phải mù với bảng ERP."""
    with pytest.raises(Exception):
        await erp_session.execute(text("SELECT 1 FROM van_ban"))


# --------------------------------------------------------------------------- #
# Danh mục phòng ban: hỏi nhiều lần trong một phiên, quét bảng đúng một lần
# --------------------------------------------------------------------------- #
def _sync_engine(session):
    """Engine đồng bộ nằm dưới phiên async - chỗ gắn được sự kiện đếm truy vấn."""
    bind = session.get_bind()
    return getattr(bind, "sync_engine", bind)

async def test_danh_muc_phong_ban_chi_quet_bang_mot_lan(erp_session):
    """Một báo cáo tổng hợp từng quét bảng phòng ban 9 lần cho cùng một danh mục.

    Mỗi tool số liệu gọi `_resolve_units`, `name_map` và `id_map`; cả ba nạp lại
    đúng một danh sách không đổi, và một báo cáo gọi ba tool.
    """
    from sqlalchemy import event

    from app.db.erp_repository import ErpDonViRepository

    dem = 0

    def ghi_nhan(conn, cursor, statement, params, context, executemany):
        nonlocal dem
        if "Dms_WorkDepartment" in statement:
            dem += 1

    engine = _sync_engine(erp_session)
    event.listen(engine, "before_cursor_execute", ghi_nhan)
    try:
        repo = ErpDonViRepository(erp_session)
        await repo.name_map()
        await repo.id_map()
        await ErpDonViRepository(erp_session).list_all()     # repo khác, cùng phiên
    finally:
        event.remove(engine, "before_cursor_execute", ghi_nhan)

    assert dem == 1


async def test_hoi_theo_moc_qua_khu_khong_dung_cache(erp_session):
    """Danh mục "hiện tại" cache được; danh mục tại một mốc cũ thì không."""
    from datetime import datetime

    from sqlalchemy import event

    from app.db.erp_repository import ErpDonViRepository

    repo = ErpDonViRepository(erp_session)
    await repo.list_all()

    dem = 0

    def ghi_nhan(conn, cursor, statement, params, context, executemany):
        nonlocal dem
        if "Dms_WorkDepartment" in statement:
            dem += 1

    engine = _sync_engine(erp_session)
    event.listen(engine, "before_cursor_execute", ghi_nhan)
    try:
        await repo.list_all(datetime(2026, 3, 1))
    finally:
        event.remove(engine, "before_cursor_execute", ghi_nhan)

    assert dem == 1

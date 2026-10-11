"""Kiểm tra sổ văn bản, danh mục theo tenant và chốt chặn ghi ERP."""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.erp_repository import ErpDonViRepository
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
                truong_du_lieu=json.dumps({"muc": ["nhan_su", "thiet_bi"]}),
            ),
            VanBan(ma_van_ban="10/BC-DV02", ten_van_ban="Báo cáo Đơn vị 2",
                   loai_van_ban="bao_cao_di", noi_gui="Đơn vị 2",
                   noi_nhan="Ban Giám đốc", ngay_van_ban=date(2026, 8, 20),
                   file_path="", dang_file="docx"),
        ])
        await session.commit()
        yield session
    await engine.dispose()


# ------------------------------------------------------- phòng ban ------- #
async def test_danh_muc_phong_ban(erp_session):
    catalog = await ErpDonViRepository(erp_session).catalog()
    assert [c["ma_phong_ban"] for c in catalog] == ["00001", "00002"]
    assert catalog[0]["ten_phong_ban"] == "Đơn vị 1"


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


# --------------------------------------------- dữ liệu app tự quản lý ---- #
async def test_danh_muc_mau_bao_cao(session):
    catalog = await TemplateRepository(session).catalog()
    assert catalog[0]["ma_template"] == "BC_THANG"


async def test_truong_du_lieu_doc_ra_json(session):
    template = await TemplateRepository(session).get("BC_THANG")
    assert template.fields["muc"] == ["nhan_su", "thiet_bi"]


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
        await repo.catalog()
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

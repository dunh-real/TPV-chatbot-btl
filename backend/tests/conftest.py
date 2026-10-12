from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="session")
def settings():
    from app.core.config import Settings

    return Settings()


# Tenant dùng cho mọi dữ liệu mẫu. Trùng với tenant đang chạy thử trên ERP thật
# để truy vấn trong test đi qua đúng nhánh lọc tenant mà chạy thật sẽ đi.
ERP_TENANT = 64

# (id, mã, tên) - "đơn vị" trong báo cáo chính là phòng ban bên ERP.
ERP_DEPARTMENTS = [(1, "00001", "Đơn vị 1"), (2, "00002", "Đơn vị 2")]

# (id, mã, tên) - chiều gộp thứ hai của chỉ tiêu nhân sự.
ERP_POSITIONS = [(1, "TRPH", "Trưởng phòng"), (2, "NHV", "Nhân viên")]

# (id, tên, số lượng, phòng ban, mã trạng thái, ngày tạo, ngày xoá)
# Mốc thời gian là thứ quyết định số liệu của kỳ: ERP không chốt số theo kỳ nên
# mọi con số đều suy ra từ "bản ghi này đã tồn tại/còn sống tại thời điểm nào".
ERP_ASSETS = [
    (1, "Máy in", 8, 1, 0, datetime(2026, 7, 5), None),
    (2, "Máy chủ", 6, 2, 0, datetime(2026, 6, 10), None),
    # Mua giữa tháng 8 -> không được tính vào kỳ tháng 7.
    (3, "Xe công vụ", 2, 2, 1, datetime(2026, 8, 12), None),
    # Thanh lý đầu tháng 8 -> còn trong kỳ tháng 7, mất ở kỳ tháng 8.
    (4, "Máy chiếu", 4, 1, 1, datetime(2026, 5, 1), datetime(2026, 8, 3)),
]

# (id, mã, tên, phòng ban, ngày vào, ngày nghỉ, chức vụ)
ERP_EMPLOYEES = [
    (1, "NV001", "Nguyễn Văn A", 1, datetime(2026, 1, 5), None, 1),
    (2, "NV002", "Trần Thị B", 1, datetime(2026, 1, 5), None, 2),
    (3, "NV003", "Lê Văn C", 1, datetime(2026, 1, 5), datetime(2026, 8, 15), 2),
    (4, "NV004", "Phạm Thị D", 2, datetime(2026, 1, 5), None, 1),
    (5, "NV005", "Hoàng Văn E", 2, datetime(2026, 1, 5), None, 2),
    # Hồ sơ chưa gán chức vụ: bảng gộp theo chức vụ vẫn phải cộng đủ người này.
    (6, "NV006", "Vũ Thị F", 2, datetime(2026, 8, 10), None, None),
]


@pytest.fixture
async def erp_session():
    """Bảng ERP dựng trên SQLite in-memory, có sẵn dữ liệu rải theo thời gian.

    Dùng chính `ErpBase` của mã chạy thật nên tên bảng, tên cột và các phép lọc
    "as-of" được kiểm đúng như khi chạy trên SQL Server.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings
    from app.db.erp_models import (
        Asset,
        AssetCategory,
        EmployeeProfile,
        ErpBase,
        WorkDepartment,
        WorkPosition,
    )

    settings = get_settings()
    truoc = settings.erp_tenant_id
    settings.erp_tenant_id = ERP_TENANT

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(ErpBase.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        dau_ky = datetime(2026, 1, 1)
        for cat_id, ma, ten in ((1, "TB", "Thiết bị"), (2, "PT", "Phương tiện")):
            session.add(AssetCategory(id=cat_id, tenant_id=ERP_TENANT, code=ma, name=ten,
                                      is_active=True, creation_time=dau_ky,
                                      is_deleted=False))
        for dept_id, code, name in ERP_DEPARTMENTS:
            session.add(WorkDepartment(
                id=dept_id, tenant_id=ERP_TENANT, code=code, department_code=f"D{dept_id}",
                display_name=name, describe="", creation_time=dau_ky, is_deleted=False))
        for asset_id, name, qty, dept_id, status, tao, xoa in ERP_ASSETS:
            session.add(Asset(
                id=asset_id, tenant_id=ERP_TENANT, code=f"TS{asset_id:03d}", name=name,
                # Xe công vụ thuộc chủng loại khác - để bảng gộp theo chủng loại
                # có nhiều hơn một dòng.
                asset_category_id=2 if name == "Xe công vụ" else 1, status=status, quantity=qty, work_department_id=dept_id,
                is_active=True, creation_time=tao, last_modification_time=tao,
                is_deleted=xoa is not None, deletion_time=xoa))
        for pos_id, ma, ten in ERP_POSITIONS:
            session.add(WorkPosition(
                id=pos_id, tenant_id=ERP_TENANT, code=ma, name=ten,
                is_active=True, creation_time=dau_ky, is_deleted=False))
        for emp_id, ma, ten, dept_id, vao, nghi, pos_id in ERP_EMPLOYEES:
            session.add(EmployeeProfile(
                id=emp_id, tenant_id=ERP_TENANT, employee_code=ma, full_name=ten,
                hire_date=vao, resignation_date=nghi, work_department_id=dept_id,
                work_position_id=pos_id, creation_time=vao, is_deleted=False))
        await session.commit()
        yield session

    await engine.dispose()
    settings.erp_tenant_id = truoc


@pytest.fixture
def erp_query(tmp_path, monkeypatch):
    """Chạy SQL nghiệp vụ toàn tenant bằng Admin giả; quyền nhóm có bộ test riêng."""
    from types import SimpleNamespace as NS
    from app.core.config import get_settings
    from app.core.context import Principal
    from app.db.erp_auth import AccountAccess
    from app.services.erp_discovery import run_discovery
    from app.services.erp_sql_catalog import SchemaStore

    monkeypatch.setattr(get_settings(), "erp_sql_policy_file", str(Path(__file__).parents[1] / "config/erp_sql_policy.json"))
    principal = Principal(tenant_id=ERP_TENANT, user_id="1", access=AccountAccess(
        1, ERP_TENANT, "test", "Test", "", is_static_admin=True,
        permissions=frozenset({"Hrm.EmployeeProfile.View", "Asm.Asset.View"})))
    async def query(session, sql):
        message = NS(content=sql, tool_calls=[])
        client = NS(chat=NS(completions=NS(create=lambda **_: NS(choices=[NS(message=message, finish_reason="stop")]))))
        return await session.run_sync(lambda sync: run_discovery("Kiểm tra nghiệp vụ", principal=principal,
            client=client, connection=sync.connection(), store=SchemaStore(tmp_path / "schema.json", 300)))
    return query


@pytest.fixture(autouse=True)
def _qwen_dat_nhan_khong_goi_mang(request, monkeypatch):
    """Bước Qwen đặt nhãn/vai trò cột chạy ở mọi node dựng tài liệu. Test không có vLLM: để nó báo lỗi
    ngay (như khi Qwen tắt) thay vì chờ kết nối tới timeout. test_nhan_cot tự giả lập LLM; test live dùng thật."""
    if request.module.__name__.endswith("test_nhan_cot") or request.node.get_closest_marker("live"):
        return

    async def _khong_co_qwen(*args, **kwargs):
        raise RuntimeError("Qwen không chạy trong test")

    import app.documents.nhan_cot as nhan_cot
    monkeypatch.setattr(nhan_cot, "_hoi_qwen", _khong_co_qwen)

"""Kiểm tra SQLite chỉ chứa pháp lý và nghiệp vụ bắt buộc đi qua MSSQL."""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db import ai_session
from app.db.models import Base
from app.db.repository import TemplateRepository, DocumentRepository


def test_khong_dung_sqlite_lam_ket_noi_nghiep_vu(monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_database_url", "sqlite+aiosqlite:///:memory:")
    with pytest.raises(ai_session.AINotConfigured):
        ai_session.get_engine()


async def test_sqlite_chi_tao_tam_bang_phap_ly(tmp_path):
    expected = {"legal_document", "legal_component", "legal_version", "legal_action", "legal_citation",
                "answer_log", "answer_evidence", "legal_change_event"}
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legal.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            assert set(await conn.run_sync(lambda c: inspect(c).get_table_names())) == expected
    finally:
        await engine.dispose()


async def test_thieu_mssql_khong_fallback_sqlite(monkeypatch):
    from app.db import session
    monkeypatch.setattr(get_settings(), "ai_database_url", "")
    monkeypatch.setattr(session, "get_engine", lambda *a, **k: pytest.fail("Đã mở SQLite nghiệp vụ"))
    for call in (TemplateRepository().list_all(), DocumentRepository().get("123/BC")):
        with pytest.raises(ai_session.AINotConfigured):
            await call


async def test_workflow_thieu_mssql_khong_chay_tiep(monkeypatch):
    from app.services.ai_outputs import tracked_workflow
    monkeypatch.setattr(get_settings(), "ai_database_url", "")
    work = AsyncMock()
    wrapped = tracked_workflow("Draft")(work)
    with pytest.raises(ai_session.AINotConfigured):
        await wrapped(request="Thử")
    work.assert_not_awaited()


async def test_giu_so_truoc_khi_chay_workflow(monkeypatch):
    from app.services import ai_outputs
    repo = AsyncMock()
    repo.user_id = 1
    repo.number_exists.return_value = True
    @asynccontextmanager
    async def scope():
        yield repo
    monkeypatch.setattr(ai_outputs, "repository", scope)
    work = AsyncMock()
    async def perform(request, inputs):
        return await work()
    wrapped = ai_outputs.tracked_workflow("Draft")(perform)
    with pytest.raises(ai_session.DocumentNumberConflict):
        await wrapped(request="Thử", inputs={"so_ky_hieu": " 15/BC "})
    repo.lock.assert_awaited_once_with("number:15/BC", shared=True)
    repo.number_exists.assert_awaited_once_with("15/BC", exclude_task=None)
    repo.add.assert_not_awaited()
    work.assert_not_awaited()


async def test_kiem_so_bo_qua_task_dang_xuat(monkeypatch):
    from app.documents.the_thuc import kiem_so_van_ban
    repo = AsyncMock()
    repo.number_exists.return_value = False
    @asynccontextmanager
    async def scope():
        yield repo
    monkeypatch.setattr(ai_session, "repository", scope)
    token = ai_session.current_task_id.set(123)
    try:
        assert await kiem_so_van_ban("15/BC") == ""
        repo.number_exists.assert_awaited_once_with("15/BC", exclude_task=123)
    finally:
        ai_session.current_task_id.reset(token)


async def test_dang_ky_file_giu_task_context_va_luu_phien_ban(business_store, monkeypatch, tmp_path):
    from app.services import ai_outputs
    from app.core.context import current_tenant_id
    cfg = get_settings()
    monkeypatch.setattr(cfg, "output_dir", str(tmp_path))
    tenant = current_tenant_id()
    name = f"test__t{tenant}__out.docx" if tenant is not None else "test.docx"
    path = tmp_path / name
    path.write_bytes(b"fixture")
    result = await ai_outputs.register_file(path)
    assert result["ai_task_id"] == 1
    versions = [c for c in business_store.add.call_args_list if c.args[0] == "OutputVersions"]
    assert versions[0].kwargs["OwnerType"] == "Task"
    assert versions[0].kwargs["OwnerId"] == 1
    assert ai_session.current_task_id.get() is None

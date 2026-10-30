"""Kiểm tra ranh giới dữ liệu và tương thích khi bật ánh xạ SQL AI."""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.core.config import get_settings
from app.core.context import Principal, use_principal
from app.db.ai_repository import AIRepository
from app.db.erp_auth import AccountAccess
from app.services.object_storage import parse_reference


@pytest.fixture
def ai_mode(monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_database_url", "sqlite+aiosqlite://")


@pytest.mark.parametrize("value", ["minio://bucket/a/b.pdf", "minio:bucket/a/b.pdf", "s3://bucket/a/b.pdf"])
def test_minio_references(value):
    assert parse_reference(value) == ("bucket", "a/b.pdf")


@pytest.mark.parametrize("value", ["https://example.com/a", "minio://bucket", "minio://bucket/a?token=x"])
def test_reject_untrusted_references(value):
    with pytest.raises(ValueError):
        parse_reference(value)


def test_requires_verified_tenant():
    with use_principal(Principal(94, "407")):
        with pytest.raises(PermissionError):
            AIRepository(None, {})
    access = AccountAccess(407, 94, "test", "test", "")
    with use_principal(Principal(95, "407", access=access)):
        with pytest.raises(PermissionError):
            AIRepository(None, {})


def test_document_ids_separate_users_and_tenants(ai_mode):
    from app.services.ai_documents import scoped_id
    ids = []
    for tenant, user in [(94, "1"), (94, "2"), (95, "1")]:
        with use_principal(Principal(tenant, user)):
            ids.append(scoped_id("same-bytes"))
    assert len(set(ids)) == 3


def test_template_json_formats():
    from app.services.ai_templates import template_fields
    fields = template_fields('[{"Name":"nguoi_ky","Sample":"A"}]', '["Tình hình","Kiến nghị"]')
    assert fields["meta"]["nguoi_ky"]["source"] == "input"
    assert len(fields["sections"]) == 2
    assert template_fields('{"sections":[]}', '{}') == {"sections": []}


async def test_empty_scope_keeps_filter(ai_mode, monkeypatch):
    from app.services import ai_documents
    repo = AsyncMock()
    repo.document_ids.return_value = []
    @asynccontextmanager
    async def scope():
        yield repo
    monkeypatch.setattr(ai_documents, "repository", scope)
    query = await ai_documents.restrict()
    assert query.must[0].match.any == []


async def test_sql_history_does_not_use_old_cache(ai_mode, monkeypatch):
    from app.db import ai_session
    from app.services.conversation import ConversationMemory
    repo, cache, store = AsyncMock(), AsyncMock(), AsyncMock()
    repo.history.return_value = []
    @asynccontextmanager
    async def scope():
        yield repo
    monkeypatch.setattr(ai_session, "repository", scope)
    memory = ConversationMemory(cache, store)
    assert await memory.get_history("old-id") == []
    await memory.append_turns("old-id", [("user", "test")])
    repo.append_turns.assert_awaited_once()
    cache.get_history.assert_not_awaited()
    store.append_turns.assert_not_awaited()


async def test_draft_from_document_uses_published_sql_template(ai_mode, monkeypatch):
    from app.agents.nodes.drafting_doc import outline_node
    from app.tools import templates
    template = {"found": True, "template_id": 12, "template_version": 3,
                "fields": {"sections": [{"id": "1", "title": "Nội dung"}]}}
    get = AsyncMock(return_value=template)
    monkeypatch.setattr(templates, "get_template", get)
    result = await outline_node({"request": "Soạn báo cáo", "inputs": {"ma_template": "BC_SQL"}})
    assert result["template"]["template_id"] == 12
    get.assert_awaited_once_with("BC_SQL")


async def test_ingestion_failure_records_failed_state(ai_mode, monkeypatch):
    from app.services import ai_documents
    repo = AsyncMock()
    repo.document.return_value = {"Id": 1}
    repo.begin_document.return_value = 1
    repo.conn.execute.return_value.first.return_value = None
    @asynccontextmanager
    async def scope():
        yield repo
    monkeypatch.setattr(ai_documents, "repository", scope)
    # Thay truy vấn chống trùng bằng câu lệnh SQL thật trên bảng tối thiểu.
    from sqlalchemy import Table, Column, Integer, String, Boolean, MetaData
    repo.table = lambda _: Table("docs", MetaData(), Column("Id", Integer),
        Column("AiServerDocId", String), Column("IsDeleted", Boolean))
    from unittest.mock import MagicMock
    response = MagicMock()
    response.first.return_value = None
    repo.conn.execute.return_value = response
    @ai_documents.tracked_ingestion
    async def ingest(self, *args, **kwargs):
        raise RuntimeError("Qdrant down")
    with pytest.raises(RuntimeError, match="Qdrant down"):
        await ingest(None, "text", "title", "id")
    assert repo.patch.call_args.kwargs["IndexStatus"] == "Failed"

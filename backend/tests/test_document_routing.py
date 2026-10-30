"""Kiểm tra UC33: quyền, nguồn, bằng chứng và phân chia ghi ERP."""
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from app.api import document_routings as api
from app.core.context import Principal, use_principal
from app.db.erp_auth import AccountAccess
from app.schemas.routing import RoutingRequest
from app.services import document_routing as service

DEPTS = [dict(id=1, code="HC", display_name="Hành chính"), dict(id=2, code="KT", display_name="Kỹ thuật")]
CATS = [dict(field_code="NS", field_name="Nhân sự")]
TEXT = "Đề nghị Phòng Hành chính tổng hợp báo cáo nhân sự tháng này."
RAW = dict(document_type_code="CONG_VAN", field_code="NS", urgency_level="Thuong",
    primary_unit_code="HC", coordinating_unit_codes=["HC", "KT", "KT"],
    reason="Nội dung thuộc nhân sự", evidence=[dict(quote=TEXT)], confidence=.9)


def principal(permissions=("Ai.Routing",)):
    return Principal(tenant_id=94, user_id="407", access=AccountAccess(user_id=407, tenant_id=94,
        user_name="test", display_name="test", email="", permissions=frozenset(permissions)))


def fake_repository(monkeypatch, repo):
    @asynccontextmanager
    async def context(): yield repo
    monkeypatch.setattr(api, "repository", context)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,valid", [({}, True), ({"primary_unit_code":None}, False),
    ({"primary_unit_code":"FAKE"}, False), ({"field_code":None}, False),
    ({"urgency_level":None}, False), ({"evidence":[{"quote":"Bằng chứng bịa"}]}, False)])
async def test_verified_mapping(monkeypatch, changes, valid):
    monkeypatch.setattr(service, "get_llm", lambda: SimpleNamespace(chat_json=AsyncMock(return_value={**RAW, **changes})))
    result = await service.suggest(TEXT, DEPTS, CATS)
    assert (result["status"] == "AI_SUGGESTED") == valid
    assert json.loads(result["ai_coordinating_units_json"]) == ([1, 2] if "primary_unit_code" in changes else [2])
    if "evidence" in changes: assert json.loads(result["ai_evidence_json"]) == []


def test_duplicate_code_not_mapped():
    result = service._map_result(RAW, DEPTS + [{**DEPTS[0], "id":99}], CATS, ["Thuong"], RAW["evidence"], [])
    assert result["ai_primary_unit_id"] is None
    assert result["status"] == "NEEDS_REVIEW"


@pytest.mark.asyncio
async def test_long_text_not_truncated(monkeypatch):
    chat = AsyncMock(return_value=RAW)
    monkeypatch.setattr(service, "get_llm", lambda: SimpleNamespace(chat_json=chat))
    text = "x" * 13000 + TEXT
    await service.suggest(text, DEPTS, CATS)
    assert json.loads(chat.call_args.args[0][1]["content"])["document"] == text
    with pytest.raises(ValueError): await service.suggest("x" * 60001, DEPTS, CATS)


@pytest.mark.asyncio
async def test_endpoints_deny_without_permission():
    from app.agents.quyen import ThieuQuyen
    from fastapi.responses import JSONResponse
    app = FastAPI(); app.include_router(api.router)
    app.add_exception_handler(ThieuQuyen, lambda r, e: JSONResponse(status_code=403, content={"detail":str(e)}))
    with use_principal(principal(permissions=())):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for path in ("/suggest", "/1/rerun"):
                assert (await client.post("/api/document-routings" + path, json={"ExtractedText":TEXT})).status_code == 403
            assert (await client.get("/api/document-routings/1")).status_code == 403


@pytest.mark.asyncio
async def test_private_document_denied(monkeypatch):
    fake_repository(monkeypatch, SimpleNamespace(document=AsyncMock(return_value=None)))
    with pytest.raises(HTTPException) as error: await api.source(RoutingRequest(document_id="sql:999"), None)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_arbitrary_minio_denied(monkeypatch):
    fake_repository(monkeypatch, SimpleNamespace())
    with pytest.raises(HTTPException) as error:
        await api.source(RoutingRequest(file_url="minio://bucket/private.docx"), None)
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_authorized_minio(monkeypatch):
    fake_repository(monkeypatch, SimpleNamespace(principal=principal(("Ai.AiChatbot",)),
        document=AsyncMock(return_value={"FileUrl":"minio://bucket/public.txt"})))
    class Store:
        client = SimpleNamespace(stat_object=lambda *a: SimpleNamespace(size=50))
        def source_client(self, bucket): return self.client
        def validate_bucket(self, bucket): assert bucket == "bucket"
        def open(self, bucket, key):
            return SimpleNamespace(stream=lambda n:iter([TEXT.encode()]),close=lambda:None,release_conn=lambda:None)
    monkeypatch.setattr(api, "ObjectStorage", Store)
    monkeypatch.setattr(api, "read_file", AsyncMock(return_value=TEXT))
    assert await api.source(RoutingRequest(document_id="sql:1"), None) == (TEXT, "minio://bucket/public.txt")


@pytest.mark.asyncio
async def test_erp_no_duplicate_product_or_task(monkeypatch):
    from sqlalchemy import Table, Column, MetaData, Integer, String
    versions = Table("v", MetaData(), Column("VersionNo", Integer), Column("OwnerType", String), Column("OwnerId", Integer))
    repo = SimpleNamespace(add=AsyncMock(), lock=AsyncMock(), table=lambda name:versions, scope=lambda t:True,
        conn=SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar=lambda:0))))
    fake_repository(monkeypatch, repo)
    monkeypatch.setattr(api.db, "catalogs", AsyncMock(return_value=(DEPTS, CATS)))
    monkeypatch.setattr(api.db, "erp_task", AsyncMock())
    monkeypatch.setattr(api.db, "save", AsyncMock())
    monkeypatch.setattr(service, "suggest", AsyncMock(return_value=service._map_result(RAW, DEPTS, CATS, ["Thuong"], RAW["evidence"], [])))
    result = await api.run(RoutingRequest(), TEXT, "", 123)
    assert result["ai_task_id"] == 123
    api.db.save.assert_not_awaited()
    assert [call.args[0] for call in repo.add.call_args_list] == ["OutputVersions"]


@pytest.mark.asyncio
async def test_text_only_rerun_keeps_source(monkeypatch):
    fake_repository(monkeypatch, SimpleNamespace())
    monkeypatch.setattr(api.db, "load", AsyncMock(return_value={"ai_task_id":123}))
    monkeypatch.setattr(api.db, "erp_task", AsyncMock(return_value={"InputJson":json.dumps({"extracted_text":TEXT})}))
    run = AsyncMock(return_value={})
    monkeypatch.setattr(api, "run", run)
    await api.rerun(1)
    assert run.call_args.args[1] == TEXT
    assert run.call_args.args[3] is None

@pytest.mark.asyncio
@pytest.mark.parametrize("tenant,user,allowed", [(94,407,True), (95,407,False), (94,408,False)])
async def test_repository_tenant_and_owner(monkeypatch, tenant, user, allowed):
    from sqlalchemy import create_engine, Table, Column, MetaData, Integer, String, Boolean
    from app.db.ai_repository import AIRepository
    engine = create_engine("sqlite://")
    table = Table("AI_DocumentRoutings", MetaData(), Column("Id", Integer, primary_key=True),
        Column("TenantId", Integer), Column("UserId", Integer), Column("IsDeleted", Boolean),
        Column("SuggestionJson", String))
    table.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(table.insert().values(Id=1,TenantId=tenant,UserId=user,IsDeleted=False,SuggestionJson='{}'))
        async def execute(query): return conn.execute(query)
        monkeypatch.setattr(api.db, "reflected", AsyncMock(return_value=table))
        with use_principal(principal()):
            repo = AIRepository(SimpleNamespace(execute=execute), {})
            if allowed: assert await api.db.load(repo,1) == {}
            else:
                with pytest.raises(HTTPException) as error: await api.db.load(repo,1)
                assert error.value.status_code == 404

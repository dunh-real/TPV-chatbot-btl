"""DB role/permission grants, tenant isolation and fail-closed API dependencies."""
from dataclasses import replace

import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.agents import quyen
from app.core.context import Principal, use_principal
from app.db.erp_auth import AccountAccess, ErpAuthRepository


@pytest.fixture
async def auth_db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        for sql in (
            "CREATE TABLE AbpUsers (Id INT, TenantId INT, UserName TEXT, Name TEXT, Surname TEXT, EmailAddress TEXT, NormalizedUserName TEXT, NormalizedEmailAddress TEXT, IsDeleted INT, IsActive INT)",
            "CREATE TABLE AbpRoles (Id INT, TenantId INT, Name TEXT, DisplayName TEXT, IsStatic INT, IsDeleted INT)",
            "CREATE TABLE AbpUserRoles (UserId INT, RoleId INT, TenantId INT)",
            "CREATE TABLE AbpPermissions (RoleId INT, UserId INT, TenantId INT, Name TEXT, IsGranted INT)",
            "INSERT INTO AbpUsers VALUES (421,94,'demo','Demo','','demo@test','DEMO','DEMO@TEST',0,1),(153,64,'lh','LH','','lh@test','LH','LH@TEST',0,1),(2,NULL,'host','Host','','host@test','HOST','HOST@TEST',0,1)",
            "INSERT INTO AbpRoles VALUES (251,94,'director','Director',0,0),(154,64,'Admin','Admin',1,0),(999,94,'deleted','Deleted',0,1),(252,94,'second','Second',0,0)",
            "INSERT INTO AbpUserRoles VALUES (421,251,94),(153,154,64),(421,154,94),(421,999,94),(2,154,NULL)",
            "INSERT INTO AbpPermissions VALUES (251,NULL,94,'Ai.Slide.Create',1),(251,NULL,94,'Hrm.EmployeeProfile.View',1),(251,NULL,94,'Asm.Asset.View',0),(251,NULL,64,'WrongTenant',1),(999,NULL,94,'DeletedRole',1)",
        ):
            await conn.execute(text(sql))
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


async def test_db_grants_ignore_denied_deleted_and_cross_tenant_roles(auth_db):
    access = await ErpAuthRepository(auth_db).find_user(user_id=421)
    assert access.role_ids == (251,)
    assert access.permissions == frozenset({"Ai.Slide.Create", "Hrm.EmployeeProfile.View"})
    assert not access.is_static_admin
    assert not access.has("Asm.Asset.View")
    assert not access.has("WrongTenant")


async def test_static_admin_is_preserved_only_with_matching_tenant(auth_db):
    repo = ErpAuthRepository(auth_db)
    admin = await repo.find_user(user_id=153)
    assert admin.is_static_admin and admin.has("Ai.Slide.Create")
    host = await repo.find_user(user_id=2)
    assert not host.is_static_admin and host.role_ids == ()


async def test_role_grants_are_union_and_revocation_takes_effect(auth_db):
    await auth_db.execute(text("INSERT INTO AbpUserRoles VALUES (421,252,94)"))
    await auth_db.execute(text("INSERT INTO AbpPermissions VALUES (252,NULL,94,'Asm.Asset.View',1)"))
    repo = ErpAuthRepository(auth_db)
    assert (await repo.find_user(user_id=421)).has("Asm.Asset.View")
    await auth_db.execute(text("DELETE FROM AbpUserRoles WHERE UserId=421 AND RoleId=252"))
    assert not (await repo.find_user(user_id=421)).has("Asm.Asset.View")


async def test_direct_deny_overrides_role_and_admin(auth_db):
    await auth_db.execute(text("INSERT INTO AbpPermissions VALUES (NULL,421,94,'Hrm.EmployeeProfile.View',0)"))
    await auth_db.execute(text("INSERT INTO AbpPermissions VALUES (NULL,153,64,'Ai.Slide.Create',0)"))
    repo = ErpAuthRepository(auth_db)
    assert not (await repo.find_user(user_id=421)).has("Hrm.EmployeeProfile.View")
    admin = await repo.find_user(user_id=153)
    assert admin.is_static_admin and not admin.has("Ai.Slide.Create")
    assert admin.has("Ai.AiChatbot")


async def test_direct_grant_works_without_role_and_is_tenant_scoped(auth_db):
    await auth_db.execute(text("DELETE FROM AbpUserRoles WHERE UserId=421"))
    await auth_db.execute(text("INSERT INTO AbpPermissions VALUES (NULL,421,94,'Ai.Summarize',1),(NULL,421,64,'WrongTenant',1)"))
    user = await ErpAuthRepository(auth_db).find_user(user_id=421)
    assert user.role_ids == () and user.has("Ai.Summarize")
    assert not user.has("WrongTenant")


async def test_role_deny_does_not_cancel_another_role_grant(auth_db):
    await auth_db.execute(text("INSERT INTO AbpUserRoles VALUES (421,252,94)"))
    await auth_db.execute(text("INSERT INTO AbpPermissions VALUES (252,NULL,94,'Hrm.EmployeeProfile.View',0)"))
    assert (await ErpAuthRepository(auth_db).find_user(user_id=421)).has("Hrm.EmployeeProfile.View")


async def test_static_admin_shortcut_can_be_disabled(auth_db, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "erp_static_admin_full_access", False)
    admin = await ErpAuthRepository(auth_db).find_user(user_id=153)
    assert not admin.is_static_admin and not admin.has("Ai.Summarize")


async def test_conflicting_role_rows_do_not_expand_permission(auth_db):
    await auth_db.execute(text("INSERT INTO AbpPermissions VALUES (251,NULL,94,'Ai.Slide.Create',0)"))
    assert not (await ErpAuthRepository(auth_db).find_user(user_id=421)).has("Ai.Slide.Create")


async def test_ambiguous_name_and_disabled_user_do_not_resolve(auth_db):
    await auth_db.execute(text("INSERT INTO AbpUsers VALUES (422,64,'demo','Other','','other@test','DEMO','OTHER@TEST',0,1)"))
    repo = ErpAuthRepository(auth_db)
    assert await repo.find_user(identifier="demo") is None
    await auth_db.execute(text("UPDATE AbpUsers SET IsActive=0 WHERE Id=421"))
    assert await repo.find_user(user_id=421) is None


def identity(*permissions, admin=False):
    return Principal(tenant_id=94, user_id="421", access=AccountAccess(
        421, 94, "demo", "Demo", "", permissions=frozenset(permissions), is_static_admin=admin))


def test_missing_account_does_not_open_protected_features():
    for who in (Principal(), Principal(user_id="unknown"), replace(identity(), user_id=None)):
        assert not who.can("Ai.Slide.Create")
        with use_principal(who), pytest.raises(quyen.ThieuQuyen):
            quyen.can_admin()()


@pytest.mark.parametrize("intent,permission", [
    ("qa", "Ai.AiChatbot"), ("document", "Ai.DocumentReview"),
    ("draft", "Ai.ReportDrafting"), ("report", "Ai.ReportSummary.Create"),
    ("presentation", "Ai.Slide.Create"),
])
def test_agent_checks_exact_db_permission(intent, permission):
    assert quyen.duoc_chay(intent, identity(permission))
    assert not quyen.duoc_chay(intent, identity())
    assert quyen.duoc_chay(intent, identity(admin=True))


async def test_permission_dependency_returns_403_without_db_access():
    app = FastAPI()

    @app.exception_handler(quyen.ThieuQuyen)
    async def denied(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403, content={"detail": exc.message})

    @app.get("/protected", dependencies=[Depends(quyen.can_quyen("Ai.Slide.Create"))])
    async def protected():
        return {"ok": True}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for who, status in ((Principal(), 403), (identity(), 403), (identity("Ai.Slide.Create"), 200)):
            with use_principal(who):
                assert (await client.get("/protected")).status_code == status


@pytest.mark.parametrize("module,path,permission", [
    ("documents", "/api/documents/review", "Ai.DocumentReview"),
    ("reports", "/api/reports/draft", "Ai.ReportDrafting"),
    ("reports", "/api/reports/aggregate", "Ai.ReportSummary.Create"),
    ("presentations", "/api/presentations/create", "Ai.Slide.Create"),
    ("ocr", "/api/ocr/extract", "Ai.Ocr"),
    ("mindmap", "/api/mindmap/generate", "Ai.MindMap"),
])
async def test_real_endpoints_require_exact_feature_permission(module, path, permission):
    import importlib
    from fastapi.responses import JSONResponse

    app = FastAPI()
    app.include_router(importlib.import_module("app.api." + module).router)

    @app.exception_handler(quyen.ThieuQuyen)
    async def denied(request, exc):
        return JSONResponse(status_code=403, content={"detail": exc.message})

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for who in (Principal(), identity(), identity("Ai.ModuleAi")):
            with use_principal(who):
                assert (await client.post(path, json={})).status_code == 403
        # A valid grant passes auth, then fails missing required input; no LLM or writes.
        with use_principal(identity(permission)):
            assert (await client.post(path, json={})).status_code == 422

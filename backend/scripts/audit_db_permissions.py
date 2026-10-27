"""Read-only audit of every active ERP role/account; no LLM calls or DB writes.

Run from backend: .venv/bin/python scripts/audit_db_permissions.py
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.core.config import get_settings
from app.core.context import Principal
from app.db.erp_auth import AccountAccess, ErpAuthRepository
from app.db.erp_session import dispose_erp_engine, erp_session_scope
from app.services.erp_sql_catalog import reflect_schema
from app.services.erp_sql_policy import SQLPolicy
from app.tools.base import ToolError


async def audit():
    counts = {"roles": 0, "accounts": 0, "sql_allowed": 0, "sql_denied": 0}
    async with erp_session_scope() as session:
        repo = ErpAuthRepository(session)
        snapshot = await session.run_sync(lambda s: reflect_schema(s.connection()))

        async def inspect(access):
            who = Principal(tenant_id=access.tenant_id, user_id=str(access.user_id), access=access)
            result = {"tenant": access.tenant_id, "roles": list(access.role_ids),
                "admin": access.is_static_admin, "grants": len(access.permissions),
                "chat": who.can("Ai.AiChatbot"), "slide": who.can("Ai.Slide.Create"),
                "draft": who.can("Ai.ReportDrafting"), "report": who.can("Ai.ReportSummary.Create")}
            try:
                policy = await session.run_sync(lambda s: SQLPolicy(
                    snapshot, get_settings().erp_sql_policy_file, who, connection=s.connection()))
                result.update(sql="allowed", tables=len(policy.tables), source=policy.group)
                # Validate tenant bindings for every table, without executing business SQL.
                for name, table in policy.tables.items():
                    column = next(iter(table["columns"].values()))["name"]
                    _, _, params, _ = policy.secure(f"SELECT [{column}] FROM {name}", 1)
                    assert params["__erp_tenant"] == access.tenant_id
                if access.tenant_id == 64 and access.user_id == 153:
                    assert policy.group == "account_read_grant"
                if access.tenant_id == 94 and access.user_id == 421:
                    assert policy.group == "db_permissions"
                    assert "dbo.hrm_employeeprofile" in policy.tables
                    assert policy.rules["dbo.hrm_employeeprofile"]["scope"] == "tenant"
                    assert "identitynumber" not in policy.tables["dbo.hrm_employeeprofile"]["columns"]
            except ToolError as exc:
                if (access.tenant_id, access.user_id) in {(64, 153), (94, 421)}:
                    raise
                result.update(sql="denied", reason=str(exc))
            return result

        roles = (await session.execute(text(
            "SELECT Id,TenantId,Name,DisplayName,IsStatic FROM AbpRoles WHERE IsDeleted=0 ORDER BY TenantId,Id"))).all()
        for rid, tid, name, label, static in roles:
            # A synthetic account measures role grants, not membership. Self scopes
            # without a real employee link intentionally fail closed.
            access = AccountAccess(-1, tid, "role-audit", "", "", role_ids=(rid,),
                permissions=await repo._permissions_of((rid,), tid),
                is_static_admin=bool(static) and name.lower() == "admin")
            print(json.dumps({"kind": "role", "role_id": rid, "label": label.strip(),
                              **await inspect(access)}, ensure_ascii=False))
            counts["roles"] += 1

        users = (await session.execute(text(
            "SELECT Id FROM AbpUsers WHERE IsActive=1 AND IsDeleted=0 ORDER BY TenantId,Id"))).scalars().all()
        for uid in users:
            access = await repo.find_user(user_id=uid)
            assert access is not None
            result = await inspect(access)
            print(json.dumps({"kind": "account", "user_id": uid, **result}, ensure_ascii=False))
            counts["accounts"] += 1
            counts["sql_" + result["sql"]] += 1
    print(json.dumps({"kind": "summary", **counts}))


async def main():
    try:
        await audit()
    finally:
        await dispose_erp_engine()


if __name__ == "__main__":
    asyncio.run(main())

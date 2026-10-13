"""Kiểm tra nhóm tài khoản, giới hạn tenant/cột/dòng và liên kết nhân viên mơ hồ."""
import json
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.core.context import Principal
from app.db.erp_auth import AccountAccess
from app.services.erp_discovery import GuardedExecutor
from app.services.erp_sql_catalog import SchemaStore
from app.services.erp_sql_policy import SQLPolicy
from app.tools.base import ToolError

POLICY = Path(__file__).parents[1] / "config/erp_sql_policy.json"


@pytest.fixture
def db(tmp_path):
    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE Dms_Employee (Id INTEGER, TenantId INTEGER, UserId INTEGER, WorkDepartmentId INTEGER, IsDeleted INTEGER)"))
        conn.execute(text("INSERT INTO Dms_Employee VALUES (10,64,1,100,0),(20,64,2,200,0),(30,94,1,100,0)"))
        conn.execute(text("CREATE TABLE Hrm_EmployeeProfile (Id INTEGER, TenantId INTEGER, EmployeeId INTEGER, FullName TEXT, WorkDepartmentId INTEGER, IsDeleted INTEGER, IdentityNumber TEXT)"))
        conn.execute(text("INSERT INTO Hrm_EmployeeProfile VALUES (1,64,10,'A',100,0,'secret'),(2,64,11,'B',100,0,'secret'),(3,64,20,'C',200,0,'secret'),(4,94,30,'D',100,0,'secret')"))
        yield conn, SchemaStore(tmp_path / "schema.json", 300).refresh(conn)
    engine.dispose()


def principal(roles=(), admin=False, grants=("Hrm.EmployeeProfile.View",), tenant=64):
    return Principal(tenant_id=tenant, user_id="1", access=AccountAccess(
        1, tenant, "test", "Test", "", role_ids=roles,
        permissions=frozenset(grants), is_static_admin=admin))


def build(db, who, path=POLICY):
    conn, snapshot = db
    policy = SQLPolicy(snapshot, path, who, connection=conn)
    return policy, GuardedExecutor(conn, policy, 100)


@pytest.mark.parametrize("who,group,ids", [
    (principal(), "employee", [1]),
    (principal(roles=(161,)), "department_manager", [1, 2]),
    (principal(roles=(159,)), "director", [1, 2, 3]),
    (principal(admin=True), "admin", [1, 2, 3]),
    (principal(roles=(159, 161)), "director", [1, 2, 3]),
    (principal(roles=(999,)), "employee", [1]),
])
def test_group_scope_survives_cte_union_and_or(db, who, group, ids):
    policy, executor = build(db, who)
    assert policy.group == group
    result = executor.execute("WITH p AS (SELECT * FROM dbo.Hrm_EmployeeProfile WHERE 1=1 OR 1=1) SELECT Id FROM p UNION ALL SELECT Id FROM dbo.Hrm_EmployeeProfile")
    assert result["ok"], result
    assert sorted(row[0] for row in result["rows"]) == sorted(ids * 2)
    assert executor.validate("SELECT FullName FROM dbo.Hrm_EmployeeProfile")["ok"]
    assert not executor.validate("SELECT IdentityNumber FROM dbo.Hrm_EmployeeProfile")["ok"]
    assert not executor.validate("SELECT * FROM dbo.Dms_Employee")["ok"]


def test_group_cannot_replace_module_permission(db):
    with pytest.raises(ToolError):
        build(db, principal(roles=(159,), grants=()))
    _, executor = build(db, principal(grants=("Hrm.EmployeeProfile.ViewMine",)))
    assert executor.execute("SELECT Id FROM dbo.Hrm_EmployeeProfile")["rows"] == [[1]]
    with pytest.raises(ToolError):
        build(db, principal(roles=(161,), grants=("Hrm.EmployeeProfile.ViewMine",)))


@pytest.mark.parametrize("change", ["DELETE FROM Dms_Employee WHERE TenantId=64 AND UserId=1", "INSERT INTO Dms_Employee VALUES (40,64,1,200,0)"])
def test_missing_or_ambiguous_employee_is_denied(db, change):
    db[0].execute(text(change))
    with pytest.raises(ToolError, match="đúng một liên kết"):
        build(db, principal(roles=(161,)))


def test_role_ids_cannot_grant_another_tenant(db):
    policy, executor = build(db, principal(roles=(159,), tenant=94))
    assert policy.group == "employee"
    assert executor.execute("SELECT Id FROM dbo.Hrm_EmployeeProfile")["rows"] == [[4]]


def test_user_restrictions_intersect_with_group(db, tmp_path):
    source = json.loads(POLICY.read_text())
    source["users"] = {"64:1": {"dbo.Hrm_EmployeeProfile": {
        "columns": ["Id"], "row_filters": {"WorkDepartmentId": [200]}}}}
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(source))
    _, executor = build(db, principal(roles=(161,)), path)
    assert executor.execute("SELECT Id FROM dbo.Hrm_EmployeeProfile")["rows"] == []
    assert not executor.validate("SELECT FullName FROM dbo.Hrm_EmployeeProfile")["ok"]


def test_null_department_has_no_rows(db):
    db[0].execute(text("UPDATE Dms_Employee SET WorkDepartmentId=NULL WHERE TenantId=64 AND UserId=1"))
    _, executor = build(db, principal(roles=(161,)))
    assert executor.execute("SELECT Id FROM dbo.Hrm_EmployeeProfile")["rows"] == []


def expanded_account(admin=True, tenant=64):
    who = principal(admin=admin, tenant=tenant)
    return replace(who, user_id="153", access=replace(who.access, user_id=153))


def test_expanded_grant_only_for_named_admin_and_tenant(db):
    policy, executor = build(db, expanded_account())
    assert policy.group == "account_read_grant"
    result = executor.execute("SELECT Id,IdentityNumber FROM dbo.Hrm_EmployeeProfile WHERE 1=1 OR 1=1")
    assert result["ok"], result
    assert sorted(row[0] for row in result["rows"]) == [1, 2, 3]
    assert not executor.validate("SELECT * FROM dbo.AbpUsers")["ok"]
    assert not executor.validate("DELETE FROM dbo.Hrm_EmployeeProfile")["ok"]
    _, other = build(db, principal(admin=True))
    assert not other.validate("SELECT IdentityNumber FROM dbo.Hrm_EmployeeProfile")["ok"]
    other_policy, other_tenant = build(db, expanded_account(tenant=94))
    assert other_policy.group == "admin"
    assert not other_tenant.validate("SELECT IdentityNumber FROM dbo.Hrm_EmployeeProfile")["ok"]
    assert other_tenant.execute("SELECT Id FROM dbo.Hrm_EmployeeProfile")["rows"] == [[4]]


def test_expanded_account_losing_admin_is_denied(db):
    with pytest.raises(ToolError, match="Admin ERP"):
        build(db, expanded_account(admin=False))


def test_expanded_grant_does_not_auto_open_new_columns(db, tmp_path):
    conn, _ = db
    conn.execute(text("ALTER TABLE Hrm_EmployeeProfile ADD NewlyAddedField TEXT"))
    snapshot = SchemaStore(tmp_path / "new-schema.json", 300).refresh(conn)
    policy = SQLPolicy(snapshot, POLICY, expanded_account(), connection=conn)
    executor = GuardedExecutor(conn, policy, 100)
    assert not executor.validate("SELECT NewlyAddedField FROM dbo.Hrm_EmployeeProfile")["ok"]


def test_expanded_grant_still_obeys_user_restrictions(db, tmp_path):
    source = json.loads(POLICY.read_text())
    source["users"] = {"64:153": {"dbo.Hrm_EmployeeProfile": {
        "columns": ["Id"], "row_filters": {"WorkDepartmentId": [200]}}}}
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(source))
    _, executor = build(db, expanded_account(), path)
    assert executor.execute("SELECT Id FROM dbo.Hrm_EmployeeProfile")["rows"] == [[3]]
    assert not executor.validate("SELECT IdentityNumber FROM dbo.Hrm_EmployeeProfile")["ok"]

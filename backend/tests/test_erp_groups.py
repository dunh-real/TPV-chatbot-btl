"""Quyền DB quyết định phạm vi SQL, không phụ thuộc RoleId hoặc nhóm JSON."""
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
        conn.execute(text("CREATE TABLE Hrm_EmployeeProfile (Id INTEGER, TenantId INTEGER, EmployeeId INTEGER, FullName TEXT, WorkDepartmentId INTEGER, IsDeleted INTEGER, IdentityNumber TEXT, Gender INTEGER DEFAULT 0, DateOfBirth TEXT DEFAULT NULL, EducationLevel INTEGER DEFAULT 0)"))
        conn.execute(text("INSERT INTO Hrm_EmployeeProfile (Id,TenantId,EmployeeId,FullName,WorkDepartmentId,IsDeleted,IdentityNumber) VALUES (1,64,10,'A',100,0,'secret'),(2,64,11,'B',100,0,'secret'),(3,64,20,'C',200,0,'secret'),(4,94,30,'D',100,0,'secret')"))
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
    (principal(), "db_permissions", [1, 2, 3]),
    (principal(roles=(161,)), "db_permissions", [1, 2, 3]),
    (principal(roles=(159,)), "db_permissions", [1, 2, 3]),
    (principal(admin=True), "db_permissions", [1, 2, 3]),
    (principal(roles=(159, 161)), "db_permissions", [1, 2, 3]),
    (principal(roles=(251,), grants=("Hrm.EmployeeProfile.ViewMine",)), "db_permissions", [1]),
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
    _, executor = build(db, principal(roles=(161,), grants=("Hrm.EmployeeProfile.ViewMine",)))
    assert executor.execute("SELECT Id FROM dbo.Hrm_EmployeeProfile")["rows"] == [[1]]


@pytest.mark.parametrize("change", ["DELETE FROM Dms_Employee WHERE TenantId=64 AND UserId=1", "INSERT INTO Dms_Employee VALUES (40,64,1,200,0)"])
def test_missing_or_ambiguous_employee_is_denied(db, change):
    db[0].execute(text(change))
    with pytest.raises(ToolError, match="đúng một liên kết"):
        build(db, principal(grants=("Hrm.EmployeeProfile.ViewMine",)))


def test_role_ids_cannot_grant_another_tenant(db):
    policy, executor = build(db, principal(roles=(159,), tenant=94))
    assert policy.group == "db_permissions"
    assert executor.execute("SELECT Id FROM dbo.Hrm_EmployeeProfile")["rows"] == [[4]]


def test_user_restrictions_intersect_with_group(db, tmp_path):
    source = json.loads(POLICY.read_text())
    source["users"] = {"64:1": {"dbo.Hrm_EmployeeProfile": {
        "columns": ["Id"], "row_filters": {"WorkDepartmentId": [200]}}}}
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(source))
    _, executor = build(db, principal(roles=(161,)), path)
    assert executor.execute("SELECT Id FROM dbo.Hrm_EmployeeProfile")["rows"] == [[3]]
    assert not executor.validate("SELECT FullName FROM dbo.Hrm_EmployeeProfile")["ok"]


def test_null_department_does_not_remove_own_profile(db):
    db[0].execute(text("UPDATE Dms_Employee SET WorkDepartmentId=NULL WHERE TenantId=64 AND UserId=1"))
    _, executor = build(db, principal(grants=("Hrm.EmployeeProfile.ViewMine",)))
    assert executor.execute("SELECT Id FROM dbo.Hrm_EmployeeProfile")["rows"] == [[1]]


@pytest.mark.parametrize("tenant,user", [(64,153),(94,407),(94,1234)])
def test_admin_business_columns_follow_role_not_account(db, tenant, user):
    who=principal(admin=True,tenant=tenant)
    who=replace(who,user_id=str(user),access=replace(who.access,user_id=user))
    _,executor=build(db,who)
    assert executor.validate("SELECT Gender,DateOfBirth,EducationLevel FROM dbo.Hrm_EmployeeProfile")["ok"]
    assert not executor.validate("SELECT IdentityNumber FROM dbo.Hrm_EmployeeProfile")["ok"]
    assert not executor.validate("DELETE FROM dbo.Hrm_EmployeeProfile")["ok"]
    rows=executor.execute("SELECT Id FROM dbo.Hrm_EmployeeProfile WHERE 1=1 OR 1=1")["rows"]
    assert sorted(r[0] for r in rows)==([4] if tenant==94 else [1,2,3])


def test_non_admin_does_not_receive_admin_columns(db):
    _,executor=build(db,principal())
    assert not executor.validate("SELECT DateOfBirth FROM dbo.Hrm_EmployeeProfile")["ok"]


def test_admin_does_not_auto_open_new_columns(db,tmp_path):
    conn,_=db
    conn.execute(text("ALTER TABLE Hrm_EmployeeProfile ADD NewlyAddedField TEXT"))
    snapshot=SchemaStore(tmp_path/"schema.json",300).refresh(conn)
    policy=SQLPolicy(snapshot,POLICY,principal(admin=True),connection=conn)
    assert not GuardedExecutor(conn,policy,100).validate("SELECT NewlyAddedField FROM dbo.Hrm_EmployeeProfile")["ok"]


def test_role_name_or_id_never_grants_sql_access(db):
    with pytest.raises(ToolError):
        build(db, principal(roles=(159, 168, 251), grants=()))


def test_full_view_does_not_require_employee_link(db):
    db[0].execute(text("DELETE FROM Dms_Employee"))
    _, executor = build(db, principal(roles=(999,)))
    assert executor.execute("SELECT Id FROM dbo.Hrm_EmployeeProfile")["rows"] == [[1], [2], [3]]


def test_view_mine_cannot_be_widened_by_role_ids(db):
    _, executor = build(db, principal(roles=(159, 168), grants=("Hrm.EmployeeProfile.ViewMine",)))
    assert executor.execute("SELECT Id FROM dbo.Hrm_EmployeeProfile WHERE 1=1 OR 1=1")["rows"] == [[1]]

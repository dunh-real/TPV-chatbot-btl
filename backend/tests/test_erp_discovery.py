"""Kiểm tra discovery độc lập, phân quyền SQL và cập nhật schema bằng DB tạm."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, text

from app.core.config import get_settings
from app.core.context import Principal
from app.db.erp_auth import AccountAccess
from app.documents.query_result import query_sections, query_slides
from app.schemas.erp import QueryResult
from app.services.erp_discovery import GuardedExecutor, run_discovery
from app.services.erp_sql_catalog import SchemaStore
from app.services.erp_sql_policy import SQLPolicy
from app.tools.base import ToolError


def identity(tenant=64, permissions=("Hrm.EmployeeProfile.View", "Asm.Asset.View")):
    return Principal(tenant_id=tenant, user_id="1", access=AccountAccess(
        1, tenant, "tester", "Tester", "", permissions=frozenset(permissions)))


@pytest.fixture
def database(tmp_path, monkeypatch):
    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        conn.execute(text("""CREATE TABLE Hrm_EmployeeProfile (
            Id INTEGER PRIMARY KEY, TenantId INTEGER, WorkDepartmentId INTEGER,
            WorkPositionId INTEGER, HireDate TEXT, ResignationDate TEXT,
            CreationTime TEXT, IsDeleted INTEGER, DeletionTime TEXT, FullName TEXT)"""))
        conn.execute(text("""INSERT INTO Hrm_EmployeeProfile VALUES
            (1,64,1,1,'2026-01-01',NULL,'2026-01-01',0,NULL,'secret one'),
            (2,64,2,1,'2026-02-01',NULL,'2026-02-01',0,NULL,'secret two'),
            (3,94,1,1,'2026-01-01',NULL,'2026-01-01',0,NULL,'other tenant')"""))
        conn.commit()
        # Giữ các kiểm thử engine độc lập với mapping nhóm của DB triển khai.
        source = json.loads((Path(__file__).parents[1] / "config/erp_sql_policy.json").read_text())
        source.pop("groups", None); source.pop("default_group", None)
        rule = source["tables"]["dbo.Hrm_EmployeeProfile"]
        for item in source["tables"].values():
            item["permissions_any"] = [p for p in item["permissions_any"] if p != "Hrm.EmployeeProfile.ViewMine"]
        rule["columns"] = [c for c in rule["columns"] if c not in ("EmployeeId", "EmployeeCode", "FullName")]
        path = tmp_path / "engine_policy.json"
        path.write_text(json.dumps(source))
        monkeypatch.setattr(get_settings(), "erp_sql_policy_file", str(path))
        monkeypatch.setattr(get_settings(), "erp_semantic_file", "")
        yield conn, SchemaStore(tmp_path / "schema.json", 300)
    engine.dispose()


def policy(database, principal=None):
    conn, store = database
    return SQLPolicy(store.refresh(conn), get_settings().erp_sql_policy_file, principal or identity())


def test_tsql_age_expression_accepts_implicit_date_casts(database):
    sql = """SELECT DATEDIFF(YEAR, HireDate, '2026-10-30') - CASE
        WHEN DATEADD(YEAR, DATEDIFF(YEAR, HireDate, '2026-10-30'), HireDate)
             > '2026-10-30' THEN 1 ELSE 0 END AS Age
        FROM dbo.Hrm_EmployeeProfile"""
    secured, _, params, sources = policy(database).secure(sql, 100)
    assert "DATEADD" in secured and "DATEDIFF" in secured
    assert params == {"__erp_tenant": 64} and sources == ["dbo.Hrm_EmployeeProfile"]
    with pytest.raises(ToolError, match="Hàm SQL chưa được cấp phép"):
        policy(database).secure("SELECT SUSER_SNAME() FROM dbo.Hrm_EmployeeProfile", 100)


class Dump(NS):
    def model_dump(self):
        def plain(value):
            if isinstance(value, NS): return {k: plain(v) for k, v in vars(value).items()}
            if isinstance(value, list): return [plain(v) for v in value]
            return value
        return plain(self)


def response(content="", calls=(), finish="stop"):
    return NS(choices=[NS(message=NS(content=content, tool_calls=list(calls)), finish_reason=finish)])


def tool(name, args):
    return Dump(id="call-1", type="function", function=Dump(name=name, arguments=json.dumps(args)))


class Model:
    def __init__(self, *answers):
        self.answers, self.requests = iter(answers), []
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        answer = next(self.answers)
        if isinstance(answer, Exception): raise answer
        return answer


@pytest.mark.parametrize("sql", [
    "SELECT FullName FROM dbo.Hrm_EmployeeProfile", "SELECT * FROM dbo.AbpUsers",
    "SELECT * FROM otherdb.dbo.Hrm_EmployeeProfile", "SELECT * INTO dbo.copy FROM dbo.Hrm_EmployeeProfile",
    "SELECT * FROM dbo.Hrm_EmployeeProfile; DELETE FROM dbo.Hrm_EmployeeProfile",
    "WITH p AS (SELECT Id FROM dbo.Hrm_EmployeeProfile) DELETE FROM p",
    "UPDATE dbo.Hrm_EmployeeProfile SET IsDeleted=1", "SELECT SUSER_SNAME() FROM dbo.Hrm_EmployeeProfile",
    "SELECT dbo.SUM(Id) FROM dbo.Hrm_EmployeeProfile", "SELECT @tenant FROM dbo.Hrm_EmployeeProfile",
    "SELECT TOP (50) PERCENT Id FROM dbo.Hrm_EmployeeProfile", "SELECT 99 AS count",
    "SELECT * FROM OPENROWSET('provider','connection','query')",
])
def test_reject_unauthorized_sql(database, sql):
    with pytest.raises(ToolError): policy(database).secure(sql, 100)


@pytest.mark.parametrize("principal", [Principal(tenant_id=64), identity(None),
    Principal(tenant_id=94, user_id="1", access=identity().access)])
def test_identity_fail_closed_before_io(principal, monkeypatch):
    monkeypatch.setattr("app.services.erp_discovery.get_sql_engine", lambda: pytest.fail("DB reached"))
    with pytest.raises(ToolError): run_discovery("Số nhân sự?", principal=principal)


@pytest.mark.parametrize("grants", [(), ("Hrm.EmployeeProfile.ViewMine",)])
def test_no_module_grant_means_no_catalog(database, grants):
    with pytest.raises(ToolError): policy(database, identity(permissions=grants))


def test_tenant_applied_to_every_join(database):
    conn, _ = database
    result = GuardedExecutor(conn, policy(database), 100).execute(
        "SELECT p.Id,q.Id AS second FROM dbo.Hrm_EmployeeProfile p JOIN dbo.Hrm_EmployeeProfile q ON p.Id=q.Id WHERE 1=1 OR 1=1")
    assert result["ok"], result
    assert result["rows"] == [[1, 1], [2, 2]]


def test_cte_union_and_star_cannot_escape_policy(database):
    conn, _ = database
    executor = GuardedExecutor(conn, policy(database), 100)
    result = executor.execute("WITH p AS (SELECT * FROM dbo.Hrm_EmployeeProfile) SELECT Id FROM p UNION ALL SELECT Id FROM dbo.Hrm_EmployeeProfile")
    assert result["ok"], result
    assert sorted(result["rows"]) == [[1], [1], [2], [2]]
    assert "FullName" not in executor.execute("SELECT * FROM dbo.Hrm_EmployeeProfile")["columns"]


def test_user_scope_restricts_columns_and_rows(database, tmp_path, monkeypatch):
    source = json.loads(Path(get_settings().erp_sql_policy_file).read_text())
    source["users"] = {"64:1": {"dbo.Hrm_EmployeeProfile": {
        "columns": ["Id", "WorkDepartmentId"], "row_filters": {"WorkDepartmentId": [1]}}}}
    path = tmp_path / "policy.json"; path.write_text(json.dumps(source))
    monkeypatch.setattr(get_settings(), "erp_sql_policy_file", str(path))
    conn, _ = database; limited = policy(database)
    assert set(limited.tables["dbo.hrm_employeeprofile"]["columns"]) == {"id", "workdepartmentid"}
    executor = GuardedExecutor(conn, limited, 100)
    assert executor.execute("SELECT Id FROM dbo.Hrm_EmployeeProfile")["rows"] == [[1]]
    assert not executor.validate("SELECT HireDate FROM dbo.Hrm_EmployeeProfile")["ok"]


def test_schema_file_updates_without_granting_new_fields(database):
    conn, store = database; first = store.refresh(conn)
    conn.execute(text("ALTER TABLE Hrm_EmployeeProfile ADD Password TEXT"))
    second = store.refresh(conn, force=True)
    assert first["schema_version"] != second["schema_version"]
    assert "password" in json.loads(store.path.read_text())["tables"]["dbo.hrm_employeeprofile"]["columns"]
    assert "password" not in policy(database).tables["dbo.hrm_employeeprofile"]["columns"]


def test_discovery_tools_repair_and_output(database):
    conn, store = database
    model = Model(response(calls=[tool("describe_tables", {"tables": ["dbo.Hrm_EmployeeProfile"]})]),
                  response("SELECT Missing FROM dbo.Hrm_EmployeeProfile"),
                  response("SELECT COUNT(*) AS total FROM dbo.Hrm_EmployeeProfile"))
    result = run_discovery("Có bao nhiêu hồ sơ nhân sự?", principal=identity(), client=model, connection=conn, store=store)
    assert result.rows == [[2]] and result.source_tables == ["dbo.Hrm_EmployeeProfile"]
    assert result.schema_version and result.policy_version and result.query_id
    assert "secret one" not in json.dumps(model.requests)
    assert "FullName" not in model.requests[0]["messages"][0]["content"]
    assert model.requests[0]["tool_choice"] == "auto"
    assert model.requests[0]["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    assert model.requests[1]["messages"][-1]["role"] == "tool"
    assert "Missing" in model.requests[2]["messages"][-1]["content"]
    # Slide dùng nhãn hiển thị, không phụ thuộc alias SQL sau bước đặt nhãn cột.
    result.columns[0].label = "Tổng hồ sơ"
    assert query_sections([result.as_dict()])[0]["table"]["rows"] == [["2"]]
    assert "Tổng hồ sơ" in "\n".join(query_slides([result.as_dict()]))


def test_multiple_sql_blocks_are_repaired_before_execution(database, monkeypatch):
    conn, store = database
    multiple = "```sql\nSELECT Missing FROM dbo.Hrm_EmployeeProfile\n```\n```sql\nSELECT 2\n```"
    model = Model(response(multiple), response("SELECT COUNT(*) AS total FROM dbo.Hrm_EmployeeProfile"))
    executed = []
    execute = GuardedExecutor.execute
    monkeypatch.setattr(GuardedExecutor, "execute",
                        lambda self, sql: (executed.append(sql), execute(self, sql))[1])
    result = run_discovery("Đếm nhân sự", principal=identity(), client=model, connection=conn, store=store)
    assert result.rows == [[2]] and len(executed) == 1
    assert len(model.requests) == 2
    assert multiple == model.requests[1]["messages"][-2]["content"]
    assert "ONE sql code block" in model.requests[1]["messages"][-1]["content"]


def test_multiple_sql_blocks_stop_at_repair_limit(database, monkeypatch):
    conn, store = database
    monkeypatch.setattr(get_settings(), "erp_text2sql_max_repairs", 1)
    monkeypatch.setattr(GuardedExecutor, "execute", lambda *args: pytest.fail("Ambiguous SQL executed"))
    multiple = response("```sql\nSELECT 1\n```\n```sql\nSELECT 2\n```")
    model = Model(multiple, multiple)
    with pytest.raises(ToolError):
        run_discovery("Đếm nhân sự", principal=identity(), client=model, connection=conn, store=store)
    assert len(model.requests) == 2


def test_truncated_results_are_preview_only(database, monkeypatch):
    conn, store = database; monkeypatch.setattr(get_settings(), "erp_text2sql_max_rows", 1)
    result = run_discovery("Danh sách Id", principal=identity(), client=Model(response("SELECT Id FROM dbo.Hrm_EmployeeProfile ORDER BY Id")), connection=conn, store=store)
    assert result.rows == [[1]] and result.truncated
    with pytest.raises(ToolError): query_sections([result.as_dict()])


def test_truncated_model_output_is_never_executed(database):
    conn, store = database
    with pytest.raises(ToolError):
        run_discovery("Đếm nhân sự", principal=identity(), client=Model(response("SELECT COUNT(*) FROM dbo.Hrm_EmployeeProfile", finish="length")), connection=conn, store=store)


def test_precision_survives_json_contract():
    result = QueryResult(query_id="q", question="Số tiền", columns=[],
        rows=[[Decimal("12345678901234567890.123400"), 9007199254740993]], row_count=1,
        truncated=False, schema_version="s", policy_version="p", source_tables=[])
    assert result.as_dict()["rows"] == [["12345678901234567890.123400", "9007199254740993"]]


def test_catalog_lookup_obeys_tenant_permissions_and_literal_search(database):
    from app.services.erp_sql_catalog import AuthorizedCatalog
    conn, store = database
    conn.execute(text("""CREATE TABLE Dms_WorkDepartment (Id INTEGER, TenantId INTEGER,
        Code TEXT, DepartmentCode TEXT, DisplayName TEXT, IsDeleted INTEGER,
        CreationTime TEXT, DeletionTime TEXT, Describe TEXT)"""))
    conn.execute(text("""INSERT INTO Dms_WorkDepartment VALUES
        (1,64,'A','A','Phòng 100%_Việt',0,NULL,NULL,NULL),
        (2,94,'B','B','Phòng 100%_Việt tenant khác',0,NULL,NULL,NULL),
        (3,64,'C','C','Phòng 100xxViệt',0,NULL,NULL,NULL)"""))
    snapshot = store.refresh(conn, force=True)
    access = SQLPolicy(snapshot, get_settings().erp_sql_policy_file, identity())
    catalog = AuthorizedCatalog(snapshot, access)
    catalog.executor = GuardedExecutor(conn, access, 100)
    result = catalog.lookup_values("Dms_WorkDepartment", "DisplayName", "%_Việt")
    assert result.get("values") == ["Phòng 100%_Việt"], result
    with pytest.raises(ValueError): catalog.lookup_values("Hrm_EmployeeProfile", "Id")
    with pytest.raises(ValueError): catalog.describe_tables(["AbpUsers"])


def test_tool_call_budget_cannot_be_exceeded(database, monkeypatch):
    conn, store = database
    monkeypatch.setattr(get_settings(), "erp_text2sql_max_tool_calls", 1)
    calls = [tool("describe_tables", {"tables": ["Hrm_EmployeeProfile"]})] * 2
    model = Model(response(calls=calls))
    with pytest.raises(ToolError):
        run_discovery("Nhân sự", principal=identity(), client=model, connection=conn, store=store)
    assert len(model.requests) == 1

"""Phân quyền nhóm ERP, lọc bảng/cột và ép phạm vi tenant/cá nhân/phòng ban trên SQL."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Literal

import sqlglot
from sqlalchemy import text
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

from app.core.context import current_principal
from app.services.erp_sql_catalog import fingerprint
from app.tools.base import ToolError

SAFE_FUNCTIONS = frozenset("AND OR CASE COUNT COUNT_BIG SUM AVG MIN MAX COALESCE NULLIF IF IIF CAST TRY_CAST CONVERT TRY_CONVERT ABS ROUND CEIL CEILING FLOOR ROW_NUMBER RANK DENSE_RANK LAG LEAD FIRST_VALUE LAST_VALUE LENGTH CHAR_LENGTH LEN TRIM LTRIM RTRIM LOWER UPPER SUBSTRING REPLACE CONCAT CONCAT_WS DATE_ADD DATEADD DATE_DIFF DATEDIFF DATEDIFF_BIG DATE_FROM_PARTS DATEFROMPARTS EXTRACT YEAR MONTH DAY ISNULL CURRENT_DATE CURRENT_TIMESTAMP".split())


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    permissions_any: list[str] = Field(min_length=1)
    columns: list[str] = Field(min_length=1)
    lookup_columns: list[str] = []
    column_permissions: dict[str, list[str]] = {}
    tenant_column: str = "TenantId"
    meaning: str = ""


class UserScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    columns: list[str] | None = None
    row_filters: dict[str, list[int | str]] = {}


class GroupTable(UserScope):
    permissions_any: list[str] | None = None
    scope: Literal["tenant", "self", "department"] = "tenant"
    scope_column: str | None = None


class AccountGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    priority: int = 0
    static_admin: bool = False
    tenant_roles: dict[str, list[int]] = {}
    tables: dict[str, GroupTable]


class PolicyDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    tables: dict[str, Rule]
    groups: dict[str, AccountGroup] = {}
    default_group: str | None = None
    # Nếu khai tài khoản thì chỉ được đọc các bảng/cột/dòng trong mục của tài khoản đó.
    users: dict[str, dict[str, UserScope]] = {}


class SQLPolicy:
    def __init__(self, snapshot, path: str | Path, principal=None, connection=None):
        principal = principal or current_principal()
        access = principal.access
        if access is None or not principal.user_id:
            raise ToolError("Cần xác định tài khoản và quyền ERP trước khi truy vấn dữ liệu")
        if type(principal.tenant_id) is not int or access.tenant_id != principal.tenant_id:
            raise ToolError("Tenant của tài khoản ERP không hợp lệ hoặc không khớp request")
        document = PolicyDocument.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
        self.version = document.version + ":" + fingerprint(document.model_dump())[:16]
        self.tenant_id, self.tables, self.rules = principal.tenant_id, {}, {}
        self.group = None
        group = None
        if document.groups:
            matches = [(name, item) for name, item in document.groups.items()
                       if (item.static_admin and access.is_static_admin)
                       or set(access.role_ids).intersection(item.tenant_roles.get(str(self.tenant_id), []))]
            if matches:
                matches.sort(key=lambda pair: pair[1].priority, reverse=True)
                if len(matches) > 1 and matches[0][1].priority == matches[1][1].priority:
                    raise ToolError("Tài khoản khớp nhiều nhóm cùng mức ưu tiên; cần kiểm tra policy")
                self.group, group = matches[0]
            elif document.default_group in document.groups:
                self.group = document.default_group
                group = document.groups[self.group]
            else:
                raise ToolError("Tài khoản chưa được ánh xạ vào nhóm truy vấn ERP")
        # Chỉ đọc liên kết định danh khi nhóm cần phạm vi cá nhân/phòng ban.
        employee = None
        if group and any(t.scope != "tenant" for t in group.tables.values()):
            if connection is None:
                raise ToolError("Cần kết nối ERP để xác định phạm vi nhân viên của tài khoản")
            schema = "dbo." if connection.dialect.name == "mssql" else ""
            rows = connection.execute(text(
                f"SELECT Id, WorkDepartmentId FROM {schema}Dms_Employee "
                "WHERE UserId=:uid AND TenantId=:tenant AND IsDeleted=0"),
                {"uid": access.user_id, "tenant": self.tenant_id}).fetchmany(2)
            if len(rows) != 1:
                raise ToolError("Tài khoản cần đúng một liên kết nhân viên ERP đang hoạt động trong tenant")
            employee = rows[0]
        user_scope = document.users.get(f"{self.tenant_id}:{access.user_id}")
        for name, rule in document.tables.items():
            key = name.lower()
            grouped = group.tables.get(name) if group else None
            if group and grouped is None:
                continue
            scoped = user_scope.get(name) if user_scope is not None else None
            if user_scope is not None and scoped is None:
                continue
            if not any(access.has(grant) for grant in rule.permissions_any):
                continue
            if grouped and grouped.permissions_any is not None and not any(
                access.has(grant) for grant in grouped.permissions_any
            ):
                continue
            if key not in snapshot["tables"]:
                continue
            table = deepcopy(snapshot["tables"][key])
            allowed = {c.lower() for c in rule.columns}
            if grouped and grouped.columns is not None:
                allowed &= {c.lower() for c in grouped.columns}
            if scoped and scoped.columns is not None:
                allowed &= {c.lower() for c in scoped.columns}
            for column, grants in rule.column_permissions.items():
                if not grants or not any(access.has(g) for g in grants):
                    allowed.discard(column.lower())
            table["columns"] = {c: info for c, info in table["columns"].items() if c in allowed}
            tenant = rule.tenant_column.lower()
            if tenant not in snapshot["tables"][key]["columns"] or not table["columns"]:
                raise ToolError("Schema thay đổi làm mất cột bắt buộc của chính sách ERP; cần quản trị kiểm tra")
            filters = list((scoped.row_filters if scoped else {}).items())
            if grouped:
                filters.extend(grouped.row_filters.items())
                if grouped.scope != "tenant":
                    if not grouped.scope_column:
                        raise ToolError("Nhóm thiếu cột giới hạn phạm vi")
                    value = employee[0 if grouped.scope == "self" else 1]
                    filters.append((grouped.scope_column, [] if value is None else [value]))
            if any(c.lower() not in snapshot["tables"][key]["columns"] for c, _ in filters):
                raise ToolError("Chính sách giới hạn dòng dùng cột không tồn tại")
            self.rules[key] = {"tenant_column": rule.tenant_column, "row_filters": filters,
                "scope": grouped.scope if grouped else "tenant",
                "lookup_columns": [c.lower() for c in rule.lookup_columns if c.lower() in table["columns"]]}
            table["meaning"] = rule.meaning
            if grouped:
                table["meaning"] += " Phạm vi quyền: " + {
                    "tenant": "trong tenant tài khoản",
                    "self": "chỉ hồ sơ của bản thân tài khoản, không phải toàn công ty",
                    "department": "chỉ phòng ban hiện tại của tài khoản, không phải toàn công ty",
                }[grouped.scope] + "; bộ lọc được engine bắt buộc áp dụng."
            self.tables[key] = table
        if not self.tables:
            raise ToolError("Tài khoản chưa được cấp bảng/cột ERP phù hợp trong chính sách truy vấn")
        for info in self.tables.values():
            info["relations"] = [r for r in info["relations"] if r["target"].lower() in self.tables
                and all(a.lower() in info["columns"] and b.lower() in self.tables[r["target"].lower()]["columns"] for a, b in r["columns"])]
            info["primary_key"] = [c for c in info["primary_key"] if c.lower() in info["columns"]]
        self.schema = {}
        for info in self.tables.values():
            self.schema.setdefault(info["schema"].lower(), {})[info["table"].lower()] = {
                c: value["type"] for c, value in info["columns"].items()}

    def predicate(self, key):
        rule = self.rules[key]
        clauses = [exp.column(rule["tenant_column"]).eq(exp.Placeholder(this="__erp_tenant"))]
        for column, values in rule["row_filters"]:
            clauses.append(exp.column(column).isin(*values) if values else exp.false())
        return exp.and_(*clauses)

    def secure(self, sql: str, max_rows: int):
        """Trả SQL đã giới hạn quyền, bản dùng binding, tham số và bảng nguồn."""
        try:
            if not isinstance(sql, str) or not sql.strip() or len(sql) > 64000:
                raise ValueError("SQL rỗng hoặc quá dài")
            trees = [t for t in sqlglot.parse(sql, read="tsql") if t is not None]
            if len(trees) != 1 or not isinstance(trees[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)):
                raise ValueError("Chỉ chấp nhận một SELECT hoặc WITH ... SELECT")
            tree = trees[0]
            for name in ("Insert", "Update", "Delete", "Merge", "Into", "Create", "Drop", "Alter", "Command", "Execute", "Lock", "Parameter", "Placeholder", "Hint", "WithTableHint", "Collate"):
                cls = getattr(exp, name, None)
                if cls and tree.find(cls):
                    raise ValueError(f"Cấu trúc SQL bị cấm: {name}")
            for fn in tree.find_all(exp.Func):
                name = fn.name.upper() if isinstance(fn, exp.Anonymous) else fn.sql_name().upper()
                if isinstance(fn.parent, exp.Dot) or name not in SAFE_FUNCTIONS:
                    raise ValueError("Hàm SQL chưa được cấp phép")
            for table in tree.find_all(exp.Table):
                if not isinstance(table.this, exp.Identifier) or table.catalog:
                    raise ValueError("Không cho phép nguồn ngoài DB hoặc table function")
            def physical_tables(query):
                return [source for scope in traverse_scope(query) for _, source in scope.selected_sources.values() if isinstance(source, exp.Table)]
            for table in physical_tables(tree):
                key = f"{table.db or 'dbo'}.{table.name}".lower()
                if key not in self.tables:
                    raise ValueError("Bảng nằm ngoài quyền truy cập")
                table.set("db", exp.to_identifier(self.tables[key]["schema"]))
            tree = qualify(tree, dialect="tsql", schema=self.schema, validate_qualify_columns=True)
            sources = set()
            for table in physical_tables(tree):
                key = f"{table.db}.{table.name}".lower()
                info = self.tables[key]
                sources.add(info["name"])
                projection = exp.select(*[exp.column(c["name"], quoted=True) for c in info["columns"].values()])
                projection = projection.from_(exp.Table(this=exp.to_identifier(info["table"], quoted=True), db=exp.to_identifier(info["schema"], quoted=True)))
                table.replace(projection.where(self.predicate(key)).subquery(table.alias_or_name))
            if not sources:
                raise ValueError("Truy vấn ERP phải sử dụng ít nhất một bảng được cấp quyền")
            limit = tree.args.get("limit")
            if limit:
                count = limit.expression
                if not isinstance(count, exp.Literal) or not count.is_int or limit.args.get("limit_options") or int(count.this) < 1:
                    raise ValueError("TOP phải là số nguyên dương; không dùng PERCENT/WITH TIES")
                tree.limit(min(int(count.this), max_rows + 1), copy=False)
            else:
                tree.limit(max_rows + 1, copy=False)
            binding = tree.transform(lambda n: exp.Parameter(this=exp.Var(this="__erp_tenant")) if isinstance(n, exp.Placeholder) else n)
            return tree.sql(dialect="tsql"), binding.sql(dialect="tsql"), {"__erp_tenant": self.tenant_id}, sorted(sources)
        except Exception as exc:
            raise ToolError(f"SQL không qua kiểm tra quyền/cấu trúc: {str(exc)[:240]}") from exc

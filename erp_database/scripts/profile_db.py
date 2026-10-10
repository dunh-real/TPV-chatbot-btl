"""Profile toàn bộ database bqp-private-ai-assistant (SQL Server local, Windows Authentication).

Chỉ lấy số liệu tổng hợp và giá trị của cột phân loại ít giá trị (trạng thái, loại); không đọc
tên người, email, số điện thoại, hash mật khẩu.

    uv run --no-project --with pyodbc python scripts/profile_db.py
Ra profile.json cạnh thư mục scripts/.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pyodbc

DB = "bqp-private-ai-assistant"
OUT = Path(__file__).resolve().parents[1] / "profile.json"
CONN = (f"Driver={{ODBC Driver 18 for SQL Server}};Server=localhost;Database={DB};"
        "Trusted_Connection=yes;Encrypt=yes;TrustServerCertificate=yes")

# Cột có thể chứa thông tin cá nhân hoặc bí mật: không lấy giá trị, chỉ đếm.
PII = re.compile(r"(name|email|phone|mobile|address|password|hash|token|secret|key|stamp|identity|cccd|cmnd|"
                 r"passport|bank|account|tax|salary|ip|browser|avatar|photo|image|content|body|message|note|"
                 r"description|comment|reason|title|text|url|path|file|json|data|value|parameter|exception|"
                 r"code|number|sdt|ten|ho)", re.I)
CATEGORY = re.compile(r"(status|state|type|kind|level|category|severity|mode|priority|source|gender|is[A-Z]|"
                      r"loai|trangthai)", re.I)
ABP_COLS = ["TenantId", "IsDeleted", "CreationTime", "CreatorUserId", "LastModificationTime",
            "LastModifierUserId", "DeleterUserId", "DeletionTime"]


def q(cur, sql, *args):
    cur.execute(sql, *args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def jsonable(v):
    if isinstance(v, (bytes, bytearray)):
        return f"<{len(v)} bytes>"
    if hasattr(v, "isoformat"):
        return v.isoformat(sep=" ")[:19]
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    return str(v)


def main() -> None:
    conn = pyodbc.connect(CONN, autocommit=True)
    cur = conn.cursor()
    out: dict = {"database": DB}
    out["server"] = q(cur, "SELECT @@SERVERNAME AS server, CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(30)) AS version, "
                           "CAST(SERVERPROPERTY('Edition') AS nvarchar(60)) AS edition, "
                           "DATABASEPROPERTYEX(DB_NAME(), 'Collation') AS collation")[0]
    tables = q(cur, """
        SELECT t.object_id, s.name AS sch, t.name AS tbl, t.create_date, t.modify_date,
               (SELECT SUM(p.rows) FROM sys.partitions p WHERE p.object_id = t.object_id AND p.index_id IN (0,1)) AS rows,
               (SELECT SUM(a.total_pages) * 8 FROM sys.partitions p JOIN sys.allocation_units a ON a.container_id = p.partition_id
                 WHERE p.object_id = t.object_id) AS size_kb
        FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id WHERE t.is_ms_shipped = 0 ORDER BY s.name, t.name""")
    cols = q(cur, """
        SELECT c.object_id, c.column_id, c.name, ty.name AS type, c.max_length, c.precision, c.scale, c.is_nullable,
               c.is_identity, c.is_computed, dc.definition AS default_def
        FROM sys.columns c JOIN sys.types ty ON ty.user_type_id = c.user_type_id
        LEFT JOIN sys.default_constraints dc ON dc.object_id = c.default_object_id
        JOIN sys.tables t ON t.object_id = c.object_id WHERE t.is_ms_shipped = 0 ORDER BY c.object_id, c.column_id""")
    pks = q(cur, """
        SELECT k.parent_object_id AS object_id, k.name, STRING_AGG(c.name, ',') WITHIN GROUP (ORDER BY ic.key_ordinal) AS cols
        FROM sys.key_constraints k JOIN sys.index_columns ic ON ic.object_id = k.parent_object_id AND ic.index_id = k.unique_index_id
        JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id WHERE k.type = 'PK'
        GROUP BY k.parent_object_id, k.name""")
    fks = q(cur, """
        SELECT fk.name, fk.parent_object_id AS from_id, OBJECT_SCHEMA_NAME(fk.parent_object_id) + '.' + OBJECT_NAME(fk.parent_object_id) AS from_table,
               cp.name AS from_col, OBJECT_SCHEMA_NAME(fk.referenced_object_id) + '.' + OBJECT_NAME(fk.referenced_object_id) AS to_table,
               cr.name AS to_col, fk.delete_referential_action_desc AS on_delete, fk.is_disabled, fk.is_not_trusted
        FROM sys.foreign_keys fk JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
        JOIN sys.columns cp ON cp.object_id = fkc.parent_object_id AND cp.column_id = fkc.parent_column_id
        JOIN sys.columns cr ON cr.object_id = fkc.referenced_object_id AND cr.column_id = fkc.referenced_column_id""")
    idx = q(cur, """
        SELECT i.object_id, i.name, i.type_desc, i.is_unique, i.has_filter, i.filter_definition,
               STRING_AGG(c.name, ',') WITHIN GROUP (ORDER BY ic.key_ordinal) AS cols
        FROM sys.indexes i JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.is_included_column = 0
        JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
        JOIN sys.tables t ON t.object_id = i.object_id WHERE t.is_ms_shipped = 0 AND i.is_primary_key = 0
        GROUP BY i.object_id, i.name, i.type_desc, i.is_unique, i.has_filter, i.filter_definition""")

    by_id = {t["object_id"]: t for t in tables}
    for t in tables:
        t["columns"] = [c for c in cols if c["object_id"] == t["object_id"]]
        t["pk"] = next((p["cols"] for p in pks if p["object_id"] == t["object_id"]), None)
        t["indexes"] = [{k: i[k] for k in ("name", "type_desc", "is_unique", "filter_definition", "cols")}
                        for i in idx if i["object_id"] == t["object_id"]]
        t["fk_out"] = [{k: f[k] for k in ("name", "from_col", "to_table", "to_col", "on_delete")}
                       for f in fks if f["from_id"] == t["object_id"]]
        full = f"{t['sch']}.{t['tbl']}"
        t["fk_in"] = [{"from_table": f["from_table"], "from_col": f["from_col"], "to_col": f["to_col"]}
                      for f in fks if f["to_table"] == full]
        names = {c["name"] for c in t["columns"]}
        t["abp"] = [c for c in ABP_COLS if c in names]
        ref = f"[{t['sch']}].[{t['tbl']}]"
        prof: dict = {}
        if t["rows"]:
            if "TenantId" in names:
                prof["by_tenant"] = q(cur, f"SELECT TenantId, COUNT_BIG(*) AS n FROM {ref} GROUP BY TenantId ORDER BY n DESC")
            if "IsDeleted" in names:
                prof["soft_deleted"] = q(cur, f"SELECT SUM(CASE WHEN IsDeleted = 1 THEN 1 ELSE 0 END) AS deleted, COUNT_BIG(*) AS total FROM {ref}")[0]
            for dc in ("CreationTime", "ExecutionTime", "CreatedAt", "StartTime", "Date", "Time"):
                if dc in names:
                    prof["time_range"] = {"column": dc, **q(cur, f"SELECT MIN([{dc}]) AS min, MAX([{dc}]) AS max FROM {ref}")[0]}
                    break
            nulls, cats = {}, {}
            for c in t["columns"]:
                n = c["name"]
                if c["type"] in ("image", "text", "ntext", "xml", "geography", "geometry", "hierarchyid", "sql_variant"):
                    continue
                if c["is_nullable"]:
                    nulls[n] = q(cur, f"SELECT SUM(CASE WHEN [{n}] IS NULL THEN 1 ELSE 0 END) AS n FROM {ref}")[0]["n"]
                safe_type = c["type"] in ("int", "bigint", "smallint", "tinyint", "bit") or (
                    c["type"] in ("nvarchar", "varchar") and 0 < c["max_length"] <= 200)
                if safe_type and n not in ABP_COLS and n != "Id" and not n.endswith("Id") and CATEGORY.search(n) \
                        and not (c["type"] in ("nvarchar", "varchar") and PII.search(n)):
                    d = q(cur, f"SELECT COUNT(DISTINCT [{n}]) AS d FROM {ref}")[0]["d"]
                    if d <= 25:
                        cats[n] = q(cur, f"SELECT [{n}] AS v, COUNT_BIG(*) AS n FROM {ref} GROUP BY [{n}] ORDER BY n DESC")
            prof["nulls"] = {k: v for k, v in nulls.items() if v}
            prof["categories"] = cats
        t["profile"] = prof

    out["tables"] = tables
    out["procedures"] = [r["name"] for r in q(cur, "SELECT name FROM sys.procedures WHERE is_ms_shipped = 0 ORDER BY name")]
    out["principals"] = q(cur, "SELECT name, type_desc, authentication_type_desc FROM sys.database_principals "
                               "WHERE type IN ('S','U','G') AND name NOT IN ('guest','INFORMATION_SCHEMA','sys')")
    out["tenants"] = q(cur, "SELECT Id, TenancyName, Name, IsActive, IsDeleted, EditionId, CreationTime FROM dbo.AbpTenants ORDER BY Id")
    out["editions"] = q(cur, "SELECT Id, Name, DisplayName, IsDeleted FROM dbo.AbpEditions ORDER BY Id")
    out["migrations"] = q(cur, "SELECT MigrationId, ProductVersion FROM dbo.__EFMigrationsHistory ORDER BY MigrationId")
    out["audit_top_services"] = q(cur, "SELECT TOP 25 ServiceName, COUNT_BIG(*) AS n, MIN(ExecutionTime) AS first, MAX(ExecutionTime) AS last, "
                                       "AVG(CAST(ExecutionDuration AS bigint)) AS avg_ms, SUM(CASE WHEN Exception IS NOT NULL THEN 1 ELSE 0 END) AS errors "
                                       "FROM dbo.AbpAuditLogs GROUP BY ServiceName ORDER BY n DESC")
    out["audit_by_month"] = q(cur, "SELECT FORMAT(ExecutionTime, 'yyyy-MM') AS month, COUNT_BIG(*) AS n FROM dbo.AbpAuditLogs GROUP BY FORMAT(ExecutionTime, 'yyyy-MM') ORDER BY month")
    out["hangfire_jobs"] = q(cur, "SELECT StateName, COUNT(*) AS n FROM HangFire.Job GROUP BY StateName")
    out["hangfire_failed_reasons"] = q(cur, """
        SELECT TOP 10 LEFT(s.Reason, 120) AS reason, COUNT(*) AS n FROM HangFire.State s
        WHERE s.Name = 'Failed' GROUP BY LEFT(s.Reason, 120) ORDER BY n DESC""")
    out["roles"] = q(cur, "SELECT TenantId, COUNT(*) AS roles FROM dbo.AbpRoles GROUP BY TenantId ORDER BY TenantId")
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=jsonable), encoding="utf-8")
    print(f"{len(tables)} bảng -> {OUT}")


if __name__ == "__main__":
    main()

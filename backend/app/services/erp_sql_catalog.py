"""Lưu toàn bộ schema ERP thành JSON; chỉ đưa phần được cấp quyền vào discovery."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect, text


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def sqlserver_tables(connection):
    columns = connection.execute(text("""
        SELECT s.name AS schema_name,t.name AS table_name,c.name AS column_name,
               ty.name AS type_name,c.max_length,c.precision,c.scale,c.is_nullable
        FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id
        JOIN sys.columns c ON c.object_id=t.object_id
        JOIN sys.types ty ON ty.user_type_id=c.user_type_id
        WHERE t.is_ms_shipped=0 ORDER BY s.name,t.name,c.column_id
    """)).mappings()
    tables = {}
    for c in columns:
        full = f"{c['schema_name']}.{c['table_name']}"
        info = tables.setdefault(full.lower(), {"name": full, "schema": c["schema_name"],
            "table": c["table_name"], "columns": {}, "primary_key": [], "relations": []})
        kind = c["type_name"]
        if kind in {"varchar", "nvarchar", "char", "nchar", "varbinary", "binary"}:
            size = c["max_length"]
            if kind in {"nvarchar", "nchar"} and size > 0:
                size //= 2
            kind += f"({size if size >= 0 else 'MAX'})"
        elif kind in {"decimal", "numeric"}:
            kind += f"({c['precision']},{c['scale']})"
        info["columns"][c["column_name"].lower()] = {"name": c["column_name"], "type": kind,
                                                    "nullable": bool(c["is_nullable"])}
    for row in connection.execute(text("""
        SELECT s.name,t.name,c.name FROM sys.indexes i
        JOIN sys.tables t ON t.object_id=i.object_id
        JOIN sys.schemas s ON s.schema_id=t.schema_id
        JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id
        JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id
        WHERE i.is_primary_key=1 AND t.is_ms_shipped=0 ORDER BY s.name,t.name,ic.key_ordinal
    """)):
        tables[f"{row[0]}.{row[1]}".lower()]["primary_key"].append(row[2])
    relations = {}
    for row in connection.execute(text("""
        SELECT fk.object_id,ss.name,st.name,sc.name,ts.name,tt.name,tc.name
        FROM sys.foreign_keys fk JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id=fk.object_id
        JOIN sys.tables st ON st.object_id=fk.parent_object_id JOIN sys.schemas ss ON ss.schema_id=st.schema_id
        JOIN sys.columns sc ON sc.object_id=st.object_id AND sc.column_id=fkc.parent_column_id
        JOIN sys.tables tt ON tt.object_id=fk.referenced_object_id JOIN sys.schemas ts ON ts.schema_id=tt.schema_id
        JOIN sys.columns tc ON tc.object_id=tt.object_id AND tc.column_id=fkc.referenced_column_id
        ORDER BY fk.object_id,fkc.constraint_column_id
    """)):
        source, target = f"{row[1]}.{row[2]}".lower(), f"{row[4]}.{row[5]}"
        if source not in tables or target.lower() not in tables:
            continue
        relation = relations.setdefault((source, row[0]), {"target": target, "columns": []})
        relation["columns"].append([row[3], row[6]])
    for (source, _), relation in sorted(relations.items()):
        tables[source]["relations"].append(relation)
    return tables


def reflect_schema(connection) -> dict:
    if connection.dialect.name == "mssql":
        tables = sqlserver_tables(connection)
    else:
        inspector = inspect(connection)
        schemas = inspector.get_schema_names() if connection.dialect.name == "mssql" else [None]
        tables = {}
        for schema in sorted(schemas, key=str):
            if schema in {"sys", "INFORMATION_SCHEMA", "guest"} or str(schema).startswith("db_"):
                continue
            for name in sorted(inspector.get_table_names(schema=schema)):
                full = f"{schema or 'dbo'}.{name}"
                columns = {c["name"].lower(): {"name": c["name"], "type": str(c["type"]),
                           "nullable": bool(c.get("nullable"))}
                           for c in inspector.get_columns(name, schema=schema)}
                relations = [{"target": f"{fk.get('referred_schema') or schema or 'dbo'}.{fk['referred_table']}",
                              "columns": list(zip(fk["constrained_columns"], fk["referred_columns"]))}
                             for fk in inspector.get_foreign_keys(name, schema=schema)]
                tables[full.lower()] = {"name": full, "schema": schema or "dbo", "table": name,
                    "columns": columns, "primary_key": inspector.get_pk_constraint(name, schema=schema).get("constrained_columns", []),
                    "relations": sorted(relations, key=lambda r: json.dumps(r, sort_keys=True))}
    database = {"dialect": connection.dialect.name, "database": connection.engine.url.database,
                "host": connection.engine.url.host, "port": connection.engine.url.port}
    content = {"database": database, "tables": tables}
    return {**content, "schema_version": fingerprint(content),
            "captured_at": datetime.now(timezone.utc).isoformat()}


class SchemaStore:
    def __init__(self, path: str | Path, interval: float):
        self.path, self.interval = Path(path), interval
        self._lock, self._checked, self._snapshot = threading.Lock(), 0.0, None

    def refresh(self, connection, *, force=False):
        with self._lock:
            if not force and self._snapshot and time.monotonic() - self._checked < self.interval:
                return self._snapshot
            # Luôn kiểm tra DB thật khi hết TTL; không dùng file cũ khi kết nối lỗi.
            snapshot = reflect_schema(connection)
            if not self._snapshot or snapshot["schema_version"] != self._snapshot["schema_version"]:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".schema-", suffix=".json")
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                        json.dump(snapshot, output, ensure_ascii=False, indent=2)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, self.path)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
                self._snapshot = snapshot
            self._checked = time.monotonic()
            return self._snapshot


class AuthorizedCatalog:
    """Giao diện metadata của pipeline discovery; không giữ kết nối DB chưa giới hạn quyền."""
    def __init__(self, snapshot, policy, semantic_path=""):
        from app.services.erp_semantic import enrich_semantics
        self.tables, self.policy = deepcopy(policy.tables), policy
        for name, table in self.tables.items():
            table["access_scope"] = policy.rules[name]["scope"]
        self.semantic_version = enrich_semantics(snapshot, self.tables, semantic_path)
        self.schema_version = snapshot["schema_version"]
        self.lookup_columns = {f"{name}.{col}" for name, rule in policy.rules.items()
                               for col in rule["lookup_columns"]}
        self.executor = None

    def table(self, name):
        key = str(name).replace("[", "").replace("]", "").lower()
        key = key if "." in key else "dbo." + key
        if key not in self.tables:
            raise ValueError("Bảng nằm ngoài quyền truy cập")
        return self.tables[key]

    def describe_tables(self, tables):
        if not isinstance(tables, list) or not 1 <= len(tables) <= 8:
            raise ValueError("Cần từ 1 đến 8 bảng")
        return [self.table(name) for name in tables]

    def get_relations(self, tables):
        selected = {t["name"].lower() for t in self.describe_tables(tables)}
        return [{"table": name, **relation} for name, info in self.tables.items()
                for relation in info["relations"]
                if name in selected or relation["target"].lower() in selected]

    def lookup_values(self, table, column, search="", limit=10):
        info = self.table(table)
        key = str(column).lower()
        if f"{info['name'].lower()}.{key}" not in self.lookup_columns:
            raise ValueError("Cột không được phép khám phá giá trị")
        if not isinstance(search, str) or len(search) > 100 or type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("Tham số khám phá danh mục không hợp lệ")
        # SQLGlot trích dẫn identifier/literal; executor vẫn áp dụng quyền và tenant.
        from sqlglot import exp
        field = exp.column(info["columns"][key]["name"], quoted=True)
        query = exp.select(field.copy().as_("value")).distinct().from_(info["name"])
        query = query.where(exp.Not(this=field.copy().is_(exp.Null())))
        if search:
            escaped = search.replace("~", "~~").replace("%", "~%").replace("_", "~_").replace("[", "~[")
            query = query.where(exp.Escape(this=exp.Like(this=field.copy(), expression=exp.National(this="%" + escaped + "%")), expression=exp.Literal.string("~")))
        if "isdeleted" in info["columns"]:
            query = query.where(exp.column("IsDeleted").eq(0))
        query = query.order_by(field.copy()).limit(limit + 1)
        result = self.executor.execute(query.sql(dialect="tsql"))
        if not result["ok"]:
            return result
        return {"values": [row[0] for row in result["rows"][:limit]],
                "truncated": len(result["rows"]) > limit or result["truncated"]}

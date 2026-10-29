"""Nạp semantic YAML, kiểm tra tham chiếu DB và chỉ ghép mô tả nằm trong quyền đọc."""
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from app.services.erp_sql_catalog import fingerprint
from app.tools.base import ToolError


class Note(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    requires: list[str] = []


class Meaning(BaseModel):
    model_config = ConfigDict(extra="forbid")
    meaning: str
    columns: dict[str, str] = {}
    caveats: list[Note] = []
    references: list[str] = []


class Semantic(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    tables: dict[str, Meaning]


def enrich_semantics(snapshot, tables, path):
    """`tables` là bản sao schema đã lọc quyền; không sửa snapshot hoặc cấp quyền mới."""
    if not path:
        return ""
    try:
        document = Semantic.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))

        def exists(ref, catalog):
            ref = ref.lower()
            if ref in catalog:
                return True
            table, _, column = ref.rpartition(".")
            return table in catalog and column in catalog[table]["columns"]

        for name, entry in document.tables.items():
            key = name.lower()
            if key not in tables:
                continue
            refs = [f"{name}.{c}" for c in entry.columns] + entry.references
            refs += [r for note in entry.caveats for r in note.requires]
            missing = [ref for ref in refs if not exists(ref, snapshot["tables"])]
            if missing:
                raise ValueError(f"{name}: tham chiếu không tồn tại {', '.join(missing)}")
            info = tables[key]
            info["meaning"] = entry.meaning
            for column, meaning in entry.columns.items():
                if column.lower() in info["columns"]:
                    info["columns"][column.lower()]["meaning"] = meaning
            info["caveats"] = [note.text for note in entry.caveats
                               if all(exists(ref, tables) for ref in note.requires)]
            info["references"] = [ref for ref in entry.references if ref.lower() in tables]
        return document.version + ":" + fingerprint(document.model_dump())[:16]
    except Exception as exc:
        raise ToolError(f"Semantic catalog không hợp lệ; cần cập nhật cùng DB: {exc}") from exc

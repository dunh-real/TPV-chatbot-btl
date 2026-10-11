"""Hợp đồng kết quả ERP dùng chung cho chat, báo cáo Word và PowerPoint."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_serializer


class QueryColumn(BaseModel):
    name: str
    data_type: str
    label: str | None = None
    unit: str | None = None


class QueryResult(BaseModel):
    query_id: str
    question: str
    columns: list[QueryColumn]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    schema_version: str
    policy_version: str
    source_tables: list[str]
    warnings: list[str] = Field(default_factory=list)

    @field_serializer("rows", when_used="json")
    def serialize_rows(self, rows):
        # Decimal/BigInt ra chuỗi để JSON/JavaScript không làm tròn tiền hoặc Id.
        def value(item):
            if isinstance(item, Decimal) or (type(item) is int and abs(item) > 2**53 - 1):
                return str(item)
            if isinstance(item, bytes):
                return item.hex()
            return item
        return [[value(item) for item in row] for row in rows]

    def as_dict(self):
        return self.model_dump(mode="json")

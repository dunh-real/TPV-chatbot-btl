#!/usr/bin/env python
"""Sinh DDL SQL Server từ chính model SQLAlchemy - schema luôn khớp với code.

    uv run python scripts/gen_schema.py            # in ra màn hình
    uv run python scripts/gen_schema.py -o scripts/schema_sqlserver.sql
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.dialects import mssql  # noqa: E402
from sqlalchemy.schema import CreateIndex, CreateTable  # noqa: E402

from app.db.models import Base  # noqa: E402

HEADER = """-- Schema CSDL nghiệp vụ TPV cho SQL Server 2016+.
-- KHÔNG sửa tay: sinh từ app/db/models.py bằng
--     uv run python scripts/gen_schema.py -o scripts/schema_sqlserver.sql
--
-- Bảng tài nguyên được tách làm hai (don_vi / trang_bi) thay vì gộp một bảng:
-- gộp lại thì quân số bị lặp theo từng dòng trang bị, sửa sót một dòng là báo
-- cáo ra số sai.
--
-- Cột truong_du_lieu của template_bao_cao là JSON lưu dạng NVARCHAR(MAX)
-- (SQL Server không có kiểu JSON riêng).
"""


def generate() -> str:
    dialect = mssql.dialect(deprecate_large_types=True)   # NVARCHAR(MAX) thay vì NTEXT
    dialect.server_version_info = (15, 0, 0)              # để cột ngày ra DATE, không DATETIME

    parts = [HEADER]
    for table in Base.metadata.sorted_tables:
        parts.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")
        for index in table.indexes:
            parts.append(str(CreateIndex(index).compile(dialect=dialect)).strip() + ";")
    return "\n\n".join(parts) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sinh DDL SQL Server từ model")
    parser.add_argument("-o", "--output", help="Ghi ra file thay vì in màn hình")
    ns = parser.parse_args()

    ddl = generate()
    if ns.output:
        Path(ns.output).write_text(ddl, encoding="utf-8")
        print(f"Đã ghi {ns.output}")
    else:
        print(ddl)

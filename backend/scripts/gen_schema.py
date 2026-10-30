#!/usr/bin/env python
"""Sinh DDL SQLite của tám bảng pháp lý; không sinh schema nghiệp vụ MSSQL."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.dialects import sqlite  # noqa: E402
from sqlalchemy.schema import CreateIndex, CreateTable  # noqa: E402

from app.db.models import Base  # noqa: E402

HEADER = "-- Schema SQLite pháp lý; nghiệp vụ MSSQL dùng bảng có sẵn.\n"


def generate() -> str:
    dialect = sqlite.dialect()

    parts = [HEADER]
    for table in Base.metadata.sorted_tables:
        parts.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")
        for index in table.indexes:
            parts.append(str(CreateIndex(index).compile(dialect=dialect)).strip() + ";")
    return "\n\n".join(parts) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sinh DDL SQLite pháp lý từ model")
    parser.add_argument("-o", "--output", help="Ghi ra file thay vì in màn hình")
    ns = parser.parse_args()

    ddl = generate()
    if ns.output:
        Path(ns.output).write_text(ddl, encoding="utf-8")
        print(f"Đã ghi {ns.output}")
    else:
        print(ddl)

#!/usr/bin/env python
"""CLI nạp tài liệu vào Qdrant.

    uv run python scripts/ingest.py data/corpus              # cả thư mục
    uv run python scripts/ingest.py a.pdf b.md --type luat   # từng file
    uv run python scripts/ingest.py --recreate data/corpus   # tạo lại collection
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.ingestion import get_ingestion_pipeline  # noqa: E402
from app.rag.vectorstore import get_vector_store  # noqa: E402


async def main(args: argparse.Namespace) -> int:
    store = get_vector_store()
    await store.ensure_collection(recreate=args.recreate)

    pipeline = get_ingestion_pipeline()
    results = []
    for target in args.paths:
        path = Path(target)
        if path.is_dir():
            results.extend(await pipeline.ingest_directory(path))
        elif path.is_file():
            results.append(await pipeline.ingest_file(path, doc_type=args.type))
        else:
            print(f"Bỏ qua (không tồn tại): {path}", file=sys.stderr)

    total_chunks = sum(r.chunk_count for r in results)
    for r in results:
        print(f"  {r.doc_title:<40} {r.chunk_count:>4} chunk  {r.elapsed_ms:>7.0f}ms  [{r.doc_id}]")
    print(f"\nĐã nạp {len(results)} tài liệu / {total_chunks} chunk. "
          f"Tổng số point trong collection: {await store.count()}")
    await store.close()
    return 0 if results else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nạp tài liệu vào kho tri thức")
    parser.add_argument("paths", nargs="+", help="File hoặc thư mục cần nạp")
    parser.add_argument("--type", default="", help="Nhãn doc_type để lọc khi truy vấn")
    parser.add_argument("--recreate", action="store_true", help="Xoá và tạo lại collection")
    parser.add_argument("-v", "--verbose", action="store_true")
    ns = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if ns.verbose else logging.INFO,
        format="%(levelname)-8s %(name)s: %(message)s",
    )
    raise SystemExit(asyncio.run(main(ns)))

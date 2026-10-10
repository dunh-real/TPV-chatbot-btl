"""Nạp lại kho tri thức vào một collection MỚI bằng pipeline hiện tại (chunker VBQPPL + phiên bản).

Collection cũ chỉ được ĐỌC để biết đã có những tài liệu nào; không ghi, không xoá - giữ nguyên
để quay lui. File gốc tìm trong thư mục upload (cửa /api/agent/upload thêm hậu tố GUID vào tên
nên so theo tên thật).

    python scripts/reindex_legal.py --from-collection tpv_documents --to-collection tpv_documents_v2 \
        [--extra file_hoac_thu_muc ...]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.rag.ingestion import SUPPORTED_SUFFIXES, IngestionPipeline  # noqa: E402
from app.rag.vectorstore import QdrantVectorStore  # noqa: E402

log = logging.getLogger("reindex_legal")


async def sources_in(collection: str) -> list[str]:
    settings = get_settings().model_copy(update={"qdrant_collection": collection})
    store = QdrantVectorStore(settings)
    seen: dict[str, str] = {}
    offset = None
    try:
        while True:
            points, offset = await store.client.scroll(collection_name=collection, limit=512, offset=offset,
                                                       with_payload=["doc_id", "source"], with_vectors=False)
            for p in points:
                payload = p.payload or {}
                if payload.get("doc_id"):
                    seen.setdefault(str(payload["doc_id"]), str(payload.get("source") or ""))
            if offset is None:
                break
    finally:
        await store.close()
    return sorted(set(seen.values()) - {""})


def find_upload(source: str, upload_dir: Path) -> Path | None:
    from app.services.storage import safe_name

    want = {source, safe_name(source)}
    for path in upload_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.name in want:
            return path
        try:
            from app.api.agent import ten_that

            if ten_that(path.name) in want:
                return path
        except Exception:  # noqa: BLE001
            pass
    return None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-collection", default="tpv_documents")
    ap.add_argument("--to-collection", required=True)
    ap.add_argument("--extra", nargs="*", default=[], help="file/thư mục nạp thêm")
    ap.add_argument("--report", type=Path, default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    settings = get_settings()
    if args.to_collection == args.from_collection:
        raise SystemExit("Collection đích phải khác collection nguồn (không ghi đè dữ liệu đang chạy).")
    if settings.legal_versioning_enabled:
        from app.db.session import create_all

        await create_all()
    target = settings.model_copy(update={"qdrant_collection": args.to_collection})
    pipe = IngestionPipeline(target, store=QdrantVectorStore(target))
    await pipe.store.ensure_collection()

    report: dict[str, list] = {"ok": [], "missing_file": [], "error": []}
    jobs: list[tuple[Path, str]] = []
    for source in (await sources_in(args.from_collection) if args.from_collection else []):
        path = find_upload(source, Path(settings.upload_dir))
        if path is None:
            report["missing_file"].append(source)
        else:
            jobs.append((path, source))
    for item in args.extra:
        p = Path(item)
        files = [f for f in p.rglob("*") if f.is_file()] if p.is_dir() else [p]
        jobs += [(f, f.name) for f in sorted(files) if f.suffix.lower() in SUPPORTED_SUFFIXES]

    for path, source in jobs:
        try:
            res = await pipe.ingest_file(path, source=source)
            report["ok"].append({"source": source, "doc_id": res.doc_id, "title": res.doc_title,
                                 "chunks": res.chunk_count})
            log.info("OK %s -> %d chunk", source, res.chunk_count)
        except Exception as exc:  # noqa: BLE001 - một file lỗi không dừng cả lô
            report["error"].append({"source": source, "error": f"{type(exc).__name__}: {exc}"[:300]})
            log.exception("Lỗi %s", source)
    await pipe.store.close()
    out = json.dumps(report, ensure_ascii=False, indent=1)
    if args.report:
        args.report.write_text(out, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    asyncio.run(main())

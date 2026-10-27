"""Kiểm tra chỉ đọc SQL/ERP, MinIO, Qdrant, số file local; ghi JSON, không sửa DB."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from minio import Minio
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine
from urllib3 import PoolManager, Timeout

from app.core.config import Settings
from app.db.erp_session import _reject_writes


def error_result(exc):
    # Exception text can include credentials, URLs and SQL parameters.
    result = {"status": "failed", "error": type(exc).__name__}
    code = getattr(exc, "code", None)
    if code in {"SignatureDoesNotMatch", "InvalidAccessKeyId", "AccessDenied", "NoSuchBucket"}:
        result["code"] = code
    return result


def local_inventory(root: Path):
    if not root.is_dir():
        return {"status": "missing", "root": str(root)}
    count = size = 0
    def onerror(exc):
        raise exc
    for folder, _, names in os.walk(root, onerror=onerror):
        for name in names:
            path = Path(folder) / name
            if not path.is_symlink() and path.is_file():
                size += path.stat().st_size
                count += 1
    return {"status": "ok", "root": str(root), "files": count, "bytes": size}


def minio_inventory(cfg, buckets):
    url = urlsplit(cfg.minio_endpoint if "://" in cfg.minio_endpoint else
                   ("https://" if cfg.minio_secure else "http://") + cfg.minio_endpoint)
    if url.scheme not in {"http", "https"} or url.username or url.password or url.path not in {"", "/"}:
        raise ValueError("InvalidMinioEndpoint")
    client = Minio(url.netloc, access_key=cfg.minio_access_key, secret_key=cfg.minio_secret_key,
                   secure=url.scheme == "https", http_client=PoolManager(
                       timeout=Timeout(connect=5, read=30), retries=False))
    names = list(dict.fromkeys(buckets)) if buckets else [b.name for b in client.list_buckets()]
    results = {}
    for name in names:
        try:
            count = size = 0
            for obj in client.list_objects(name, recursive=True):
                if not obj.is_dir:
                    count += 1
                    size += obj.size
            results[name] = {"status": "ok", "objects": count, "bytes": size}
        except Exception as exc:
            results[name] = error_result(exc)
    return {"status": "ok" if all(b["status"] == "ok" for b in results.values()) else "failed",
            "configured_bucket": cfg.minio_bucket, "buckets": results}


async def probe_database(url):
    if not url:
        return {"status": "not_configured"}
    from sqlalchemy.engine import make_url
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite" and parsed.database != ":memory:":
        if not parsed.database or not Path(parsed.database).is_file():
            return {"status": "missing"}  # Không để SQLite tự tạo DB mới.
    engine = create_async_engine(url, echo=False)
    event.listen(engine.sync_engine, "before_cursor_execute", _reject_writes)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            return {"status": "ok", "read_only_probe": True}
    finally:
        await engine.dispose()


async def qdrant_inventory(cfg):
    headers = {"api-key": cfg.qdrant_api_key} if cfg.qdrant_api_key else {}
    async with httpx.AsyncClient(base_url=cfg.qdrant_url.rstrip("/") + "/",
                                 headers=headers, timeout=30) as client:
        from urllib.parse import quote
        collection = quote(cfg.qdrant_collection, safe="")
        response = await client.get("collections/" + collection)
        response.raise_for_status()
        info = response.json()["result"]
        return {"status": "ok", "collection": cfg.qdrant_collection,
                "points_count": info.get("points_count")}


async def audit(args):
    cfg = Settings(_env_file=args.env_file)
    # Optional ERP-only configuration from an existing tunnel test environment.
    # Never copies it to .env or to reports.
    if args.erp_env_file:
        from dotenv import dotenv_values
        cfg.erp_database_url = dotenv_values(args.erp_env_file).get("ERP_DATABASE_URL") or ""
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "read_only": True}

    async def collect(name, action):
        try:
            report[name] = await asyncio.wait_for(action(), timeout=args.timeout)
        except Exception as exc:
            report[name] = error_result(exc)

    await asyncio.gather(
        collect("app_database", lambda: probe_database(cfg.database_url)),
        collect("erp_database", lambda: probe_database(cfg.erp_database_url)),
        collect("qdrant", lambda: qdrant_inventory(cfg)),
    )
    # Blocking SDK/filesystem calls run only in this CLI, never in API workers.
    for name, action in [
        ("uploads", lambda: local_inventory(Path(args.uploads or cfg.upload_dir))),
        ("outputs", lambda: local_inventory(Path(cfg.output_dir))),
        ("minio", lambda: minio_inventory(cfg, args.bucket)),
    ]:
        try:
            report[name] = action()
        except Exception as exc:
            report[name] = error_result(exc)
    report["status"] = "ok" if all(report[n].get("status") == "ok" for n in
        ["app_database", "erp_database", "qdrant", "uploads", "outputs", "minio"]) else "attention"
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--env-file", default=".env")
    p.add_argument("--erp-env-file", help="Chỉ đọc ERP_DATABASE_URL từ cấu hình tunnel có sẵn")
    p.add_argument("--bucket", action="append", help="Lặp lại để chọn bucket; mặc định liệt kê tất cả")
    p.add_argument("--uploads", help="Đối soát một thư mục khác, không sửa UPLOAD_DIR")
    p.add_argument("--timeout", type=float, default=120)
    p.add_argument("--report", required=True)
    args = p.parse_args()
    try:
        report = asyncio.run(audit(args))
        output = Path(args.report)
        output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive create avoids clobbering other files / following symlinks.
        with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(json.dumps({"status": report["status"], "report": str(output),
                          "checks": {k: report[k].get("status") for k in
                                     ["app_database", "erp_database", "qdrant", "uploads", "outputs", "minio"]}}))
        return 0 if report["status"] == "ok" else 1
    except Exception as exc:
        print(json.dumps(error_result(exc)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

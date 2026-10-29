#!/usr/bin/env python
"""Đọc MinIO, gửi từng file qua API upload; không gọi pipeline ingest trực tiếp."""
from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import shutil
import sys
import tempfile
import time
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import httpx
from minio import Minio

logger = logging.getLogger(__name__)
DOCUMENT_SUFFIXES = {".pdf", ".doc", ".docx"}


def ingest_bucket(minio, api, *, bucket, prefix="", upload_url, doc_type="", force=False):
    started = time.perf_counter()
    totals = {"ingested": 0, "already_exists": 0, "errors": 0, "skipped": 0}
    index = 0
    logger.info("Bắt đầu liệt kê bucket=%s, prefix=%s", bucket, prefix or "(toàn bộ)")
    for obj in minio.list_objects(bucket, prefix=prefix, recursive=True):
        name = obj.object_name
        if obj.is_dir or name.endswith("/"):
            totals["skipped"] += 1
            continue
        if PurePosixPath(name).suffix.lower() not in DOCUMENT_SUFFIXES:
            totals["skipped"] += 1
            logger.info("Bỏ qua file không thuộc PDF/DOC/DOCX: %s", name)
            continue
        index += 1
        file_started = time.perf_counter()
        logger.info("[%d] File %s | dung lượng: %s bytes", index, name, getattr(obj, "size", "không rõ"))
        response = None
        try:
            logger.info("[%d] Đang đọc file từ MinIO", index)
            response = minio.get_object(bucket, name)
            # HTTP multipart cần file seek được. Giữ tối đa 8 MiB trong RAM,
            # file lớn dùng file tạm và tự xóa sau mỗi request.
            with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as content:
                shutil.copyfileobj(response, content)
                logger.info("[%d] Đã đọc %d bytes trong %.1fs", index, content.tell(),
                            time.perf_counter() - file_started)
                content.seek(0)
                filename = PurePosixPath(name).name
                logger.info("[%d] Đang gửi file và chờ API ingest trả kết quả", index)
                api_started = time.perf_counter()
                result = api.post(
                    upload_url,
                    files={"file": (filename, content,
                                    mimetypes.guess_type(filename)[0] or "application/octet-stream")},
                    data={"doc_type": doc_type, "force_reingest": str(force).lower()},
                )
                result.raise_for_status()
                body = result.json()
                if not isinstance(body, dict) or not isinstance(body.get("already_exists"), bool) or not body.get("doc_id"):
                    raise ValueError("Response upload không đúng hợp đồng")
                key = "already_exists" if body["already_exists"] else "ingested"
                totals[key] += 1
                logger.info("[%d] %s | doc_id=%s | chunk=%s | API %.1fs | tổng %.1fs",
                            index, "Đã tồn tại, bỏ qua ingest" if body["already_exists"] else "Đã nạp",
                            body["doc_id"], body.get("chunk_count"),
                            time.perf_counter() - api_started, time.perf_counter() - file_started)
                print(json.dumps({"object": name, "status": key, "doc_id": body["doc_id"],
                                  "chunk_count": body.get("chunk_count")}, ensure_ascii=False), flush=True)
        except Exception as exc:
            totals["errors"] += 1
            # Không in headers, URL có thông tin xác thực hoặc secret.
            error = type(exc).__name__
            if isinstance(exc, httpx.HTTPStatusError):
                error += f" HTTP {exc.response.status_code}"
            logger.error("[%d] Lỗi %s sau %.1fs; tiếp tục file kế tiếp", index, error,
                         time.perf_counter() - file_started)
            print(json.dumps({"object": name, "status": "error", "error": error},
                             ensure_ascii=False), file=sys.stderr, flush=True)
        finally:
            if response is not None:
                response.close()
                response.release_conn()
    logger.info("Hoàn tất sau %.1fs | đã nạp=%d | đã tồn tại=%d | lỗi=%d | bỏ qua=%d",
                time.perf_counter() - started, totals["ingested"], totals["already_exists"],
                totals["errors"], totals["skipped"])
    return totals


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.getenv("MINIO_ENDPOINT", "http://localhost:9000"))
    parser.add_argument("--bucket", default="vanbantrunguong")
    parser.add_argument("--prefix", default="")
    parser.add_argument("--upload-url", default=os.getenv("INGEST_UPLOAD_URL", "http://localhost:8080/api/documents/upload"))
    parser.add_argument("--type", default="", dest="doc_type")
    parser.add_argument("--force-reingest", action="store_true")
    parser.add_argument("--timeout", type=float, default=1800, help="Timeout API mỗi thao tác, giây")
    args = parser.parse_args(argv)
    access = os.getenv("MINIO_ACCESS_KEY")
    secret = os.getenv("MINIO_SECRET_KEY")
    if not access or not secret:
        parser.error("Cần MINIO_ACCESS_KEY và MINIO_SECRET_KEY trong biến môi trường")
    endpoint = urlsplit(args.endpoint if "://" in args.endpoint else "http://" + args.endpoint)
    if endpoint.scheme not in {"http", "https"} or not endpoint.netloc or endpoint.path not in {"", "/"}:
        parser.error("Endpoint phải là địa chỉ S3 API, ví dụ http://localhost:9000")
    if args.timeout <= 0:
        parser.error("--timeout phải lớn hơn 0")
    try:
        headers = json.loads(os.getenv("INGEST_API_HEADERS", "{}"))
        if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
            raise ValueError
    except (ValueError, TypeError):
        parser.error("INGEST_API_HEADERS phải là JSON object gồm các header chuỗi")
    client = Minio(endpoint.netloc, access_key=access, secret_key=secret, secure=endpoint.scheme == "https")
    upload_endpoint = urlsplit(args.upload_url)
    logger.info("MinIO=%s://%s:%s | API=%s://%s:%s%s | force_reingest=%s",
                endpoint.scheme, endpoint.hostname, endpoint.port or (443 if endpoint.scheme == "https" else 80),
                upload_endpoint.scheme, upload_endpoint.hostname,
                upload_endpoint.port or (443 if upload_endpoint.scheme == "https" else 80),
                upload_endpoint.path, args.force_reingest)
    try:
        with httpx.Client(headers=headers, timeout=args.timeout) as api:
            totals = ingest_bucket(client, api, bucket=args.bucket, prefix=args.prefix,
                                   upload_url=args.upload_url, doc_type=args.doc_type,
                                   force=args.force_reingest)
    except Exception as exc:
        print(f"Không hoàn tất việc liệt kê bucket: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(json.dumps({"summary": totals}, ensure_ascii=False), flush=True)
    return 1 if totals["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

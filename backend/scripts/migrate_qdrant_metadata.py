"""Backfill metadata theo mapping BE: mặc định dry-run, không đổi vector hoặc tự gán phân vùng."""
import argparse
import asyncio
import json
import os
from pathlib import Path

from qdrant_client import AsyncQdrantClient, models
from app.core.config import get_settings
from app.services.vector_metadata import FIELDS


def read_mapping(path):
    rows = json.loads(Path(path).read_text())
    result, documents = {}, set()
    for row in rows:
        key = row["doc_id"]
        if not isinstance(key, str) or not key or key in result:
            raise ValueError("doc_id thiếu hoặc trùng trong mapping")
        for field in FIELDS[:3]:
            if type(row.get(field)) is not int or row[field] < (0 if field == "tenant_id" else 1):
                raise ValueError(f"{field} phải là ID nguyên hợp lệ")
        for field in FIELDS[3:]:
            if row.get(field) is not None and (type(row[field]) is not int or row[field] <= 0):
                raise ValueError(f"{field} sai kiểu ID")
        identity = (row["tenant_id"], row["document_id"])
        if identity in documents:
            raise ValueError("Một tài liệu ERP có nhiều doc_id trong mapping; cần đối soát")
        documents.add(identity)
        result[key] = {field: row.get(field) for field in FIELDS}
    return result


async def run(args):
    mapping, cfg = read_mapping(args.mapping), get_settings()
    if args.apply and not args.backup:
        raise ValueError("--apply cần --backup để lưu metadata gốc trước khi sửa")
    client = AsyncQdrantClient(url=cfg.qdrant_url, api_key=cfg.qdrant_api_key, timeout=60)
    points, unknown, offset = {}, 0, None
    try:
        while True:
            page, offset = await client.scroll(args.collection, limit=512, offset=offset,
                with_vectors=False, with_payload=["doc_id", *FIELDS])
            for point in page:
                old = point.payload or {}
                key = old.get("doc_id")
                if key not in mapping:
                    unknown += 1
                    continue
                new = mapping[key]
                if old.get("tenant_id") is not None and old["tenant_id"] != new["tenant_id"]:
                    raise ValueError("Mapping đổi tenant của point có sẵn; dừng để đối soát")
                points.setdefault(key, []).append((point.id, old))
            if offset is None:
                break
        print(json.dumps({"dry_run": not args.apply, "matched_points": sum(map(len, points.values())),
                          "unmapped_points": unknown, "mapping_without_points": sorted(set(mapping)-points.keys())}))
        if not args.apply:
            return
        if unknown:
            raise ValueError("Còn point không có mapping; chưa được bật bộ lọc metadata bắt buộc")
        fd = os.open(args.backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as out:
            json.dump([{"id": str(pid), "payload": old} for group in points.values() for pid, old in group], out)
        for key, group in points.items():
            for start in range(0, len(group), 256):
                await client.set_payload(args.collection, mapping[key], points=[pid for pid, _ in group[start:start+256]], wait=True)
        for field in FIELDS:
            await client.create_payload_index(args.collection, field, models.PayloadSchemaType.INTEGER, wait=True)
        print("Metadata đã cập nhật; vector và point ID được giữ nguyên")
    finally:
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup")
    asyncio.run(run(parser.parse_args()))

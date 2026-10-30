"""Đọc mẫu đã công bố từ SQL; tải file MinIO vào cache cục bộ có kiểm soát."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import anyio

from app.core.config import get_settings
from app.db.ai_session import repository
from app.db.business_types import TemplateInfo
from app.services.object_storage import ObjectStorage, parse_reference


def template_fields(fields_json, structure_json):
    fields, structure = json.loads(fields_json or "{}"), json.loads(structure_json or "{}")
    if isinstance(fields, list):
        fields = {"meta": {f["Name"]: {"source": "input", "example": f.get("Sample", "")}
                           for f in fields if isinstance(f, dict) and f.get("Name")}}
    if isinstance(structure, list):
        structure = {"sections": [{"id": str(i+1), "title": title, "type": "llm"}
                                   if isinstance(title, str) else title for i, title in enumerate(structure)]}
    if not isinstance(fields, dict) or not isinstance(structure, dict):
        raise ValueError("Cấu trúc JSON của mẫu không hợp lệ")
    return {**structure, **fields}


async def list_templates(kind=None):
    async with repository() as repo:
        rows = await repo.templates(kind)
        result = []
        for row in rows:
            version = await repo.template_version(row)
            if version is None:
                continue
            fields = template_fields(version["FieldsJson"], version["StructureJson"])
            template = TemplateInfo(ma_template=row["Code"], ten_bao_cao=row["Name"],
                loai_bao_cao=row["TemplateType"], mo_ta=row["Description"] or "",
                file_path=version["FileUrl"], fields=fields)
            template.ai_id, template.ai_version = row["Id"], version["VersionNo"]
            template.ai_hash = version["FileHashSha256"]
            result.append(template)
        return result


async def materialize(template):
    url = urlsplit(template.file_path or "")
    if url.scheme not in {"s3", "minio"}:
        # Không tự tải URL bên ngoài hoặc mở đường dẫn lấy từ DB.
        if template.file_path:
            raise ValueError("File mẫu cần tham chiếu s3://bucket/object trên MinIO")
        return template
    cfg = get_settings()
    bucket, key = parse_reference(template.file_path)
    cache = Path(cfg.upload_dir) / "ai_templates"
    cache.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(f"{bucket}/{key}:{template.ai_hash}".encode()).hexdigest() + Path(key).suffix
    target = cache / name
    def download():
        import os
        import tempfile
        with tempfile.TemporaryDirectory(dir=cache) as directory:
            path = Path(directory) / name
            ObjectStorage().download(bucket, key, path, name)
            if template.ai_hash and ObjectStorage.checksum(path) != template.ai_hash:
                raise ValueError("SHA-256 file mẫu không khớp phiên bản SQL")
            os.replace(path, target)
    if not target.exists():
        await anyio.to_thread.run_sync(download)
    template.file_path = str(target)
    return template

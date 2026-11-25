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
    cfg = get_settings()
    mapping_dir = Path(cfg.upload_dir) / "ai_templates"
    async with repository() as repo:
        rows = await repo.templates(kind)
        result = []
        for row in rows:
            version = await repo.template_version(row)
            if version is None:
                continue
            code = row.get("Code", "")
            mapping_file = mapping_dir / f"{code}_mapping.json"
            struct_json = version.get("StructureJson")
            if not struct_json and mapping_file.is_file():
                try:
                    struct_json = mapping_file.read_text(encoding="utf-8")
                except Exception:
                    pass
            fields = template_fields(version.get("FieldsJson"), struct_json)
            template = TemplateInfo(ma_template=row.get("Code", ""), ten_bao_cao=row.get("Name", ""),
                loai_bao_cao=row.get("TemplateType") or row.get("FileType") or "General", mo_ta=row.get("Description") or "",
                file_path=version.get("FileUrl") or version.get("FilePath") or "", fields=fields)
            template.ai_id, template.ai_version = row.get("Id"), version.get("VersionNo", 1)
            template.ai_hash = version.get("FileHashSha256", "")
            result.append(template)
        return result


async def materialize(template):
    if not template.file_path:
        return template
    # Nếu file đã tồn tại cục bộ trên đĩa thì dùng trực tiếp
    p = Path(template.file_path)
    if p.is_file():
        return template
    url = urlsplit(template.file_path)
    if url.scheme not in {"s3", "minio"}:
        cfg = get_settings()
        local_cand = Path(cfg.upload_dir) / "ai_templates" / Path(template.file_path).name
        if local_cand.is_file():
            template.file_path = str(local_cand)
            return template
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


async def get_version(template_id, version_no):
    """Chỉ lấy phiên bản đã công bố, trong cùng phạm vi quyền như danh sách mẫu."""
    from app.services.integration_errors import IntegrationError
    if template_id <= 0 or version_no <= 0:
        raise IntegrationError('INVALID_REQUEST', 'ID mẫu và phiên bản phải lớn hơn 0', 422)
    cfg = get_settings()
    mapping_dir = Path(cfg.upload_dir) / "ai_templates"
    async with repository() as repo:
        row = next((r for r in await repo.templates() if r["Id"] == template_id), None)
        version = await repo.template_version(row, version_no) if row else None
        if not version:
            raise IntegrationError("INVALID_REQUEST", "Không tìm thấy phiên bản mẫu đã công bố trong phạm vi quyền", 404)
        code = row.get("Code", "")
        mapping_file = mapping_dir / f"{code}_mapping.json"
        struct_json = version.get("StructureJson")
        if not struct_json and mapping_file.is_file():
            try:
                struct_json = mapping_file.read_text(encoding="utf-8")
            except Exception:
                pass
        template = TemplateInfo(ma_template=row["Code"], ten_bao_cao=row["Name"],
            file_path=version.get("FileUrl") or version.get("FilePath") or "",
            fields=template_fields(version.get("FieldsJson"), struct_json),
            ai_id=template_id, ai_version=version.get("VersionNo", 1), ai_hash=version.get("FileHashSha256", ""))
    binding(template)
    return await materialize(template)


def binding(template):
    """Hợp đồng mapping phải đúng file/phiên bản, không tự suy luận lại mẫu cũ."""
    from app.schemas.templates import TemplateMapping
    from app.services.integration_errors import IntegrationError
    try:
        mapping = TemplateMapping.model_validate(template.fields.get('binding'))
    except ValueError as exc:
        raise IntegrationError('CONFIG_INVALID', 'Phiên bản mẫu chưa có mapping hợp lệ; cần phân tích và duyệt một lần', 422) from exc
    if template.ai_hash and template.ai_hash != mapping.file_sha256:
        raise IntegrationError('CONFIG_INVALID', 'Hash mapping không khớp phiên bản mẫu đã lưu', 409)
    return mapping

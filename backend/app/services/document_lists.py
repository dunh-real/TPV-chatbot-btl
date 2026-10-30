"""Danh mục cá nhân trên đĩa và CSV; không thêm bảng hoặc ghi vào ERP."""
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime, timezone
from uuid import uuid4

from app.core.config import get_settings
from app.core.context import current_principal
from app.schemas.documents import SavedDocumentList


def _root():
    principal = current_principal()
    if not principal.user_id or principal.access is None:
        raise PermissionError("Cần tài khoản xác thực để lưu danh mục")
    user = hashlib.sha256(str(principal.user_id).encode()).hexdigest()
    tenant = "common" if principal.tenant_id is None else str(int(principal.tenant_id))
    return Path(get_settings().saved_documents_dir) / tenant / user


def _path(saved_id):
    if not re.fullmatch(r"[a-f0-9]{32}", saved_id):
        raise ValueError("saved_id không hợp lệ")
    path = _root() / (saved_id + ".json")
    if path.is_symlink():
        raise FileNotFoundError(saved_id)
    return path


def save(request):
    name = request.name.strip()
    if not name:
        raise ValueError("Tên danh mục không được để trống")
    record = SavedDocumentList(saved_id=uuid4().hex, name=name,
        doc_ids=list(dict.fromkeys(request.doc_ids)), created_at=datetime.now(timezone.utc))
    path = _path(record.saved_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".saving-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(record.model_dump_json())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return record


def load(saved_id):
    return SavedDocumentList.model_validate_json(_path(saved_id).read_text(encoding="utf-8"))


def list_saved():
    # Không mkdir khi đọc; danh mục rỗng không làm phát sinh thư mục.
    records = [load(path.stem) for path in _root().glob("*.json") if not path.is_symlink()]
    return sorted(records, key=lambda r: r.created_at, reverse=True)


def delete(saved_id):
    _path(saved_id).unlink()


def export_csv(doc_ids, details):
    def safe(value):
        value = str(value or "")
        if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
            return "'" + value
        return value
    output = io.StringIO(newline="")
    writer = csv.writer(output, quoting=csv.QUOTE_ALL)
    writer.writerow(["doc_id", "ten_tai_lieu", "so_hieu", "loai", "ngay_hieu_luc", "nguon", "ban_goc_api", "trang_thai"])
    for doc_id in dict.fromkeys(doc_ids):
        doc = details.get(doc_id)
        row = [doc_id, doc.title, doc.so_hieu, doc.loai, doc.ngay_hieu_luc, doc.source,
               doc.original_url, "available"] if doc else [doc_id, "", "", "", "", "", "", "missing"]
        writer.writerow([safe(cell) for cell in row])
    return output.getvalue().encode("utf-8-sig")

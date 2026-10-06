"""Lưu cây sơ đồ tư duy ra đĩa, tách theo thuê bao.

Cây không nằm trong Qdrant vì nó không phải nội dung đem đi truy hồi, mà là kết
quả đã chốt của một lần đọc cả tài liệu - dựng lại tốn 5-9 lượt LLM nên phải giữ
được qua các lần khởi động lại.

Bố cục trên đĩa:

    {mindmap_dir}/{tenant_id}/{doc_id}.json

Thuê bao nằm ở tầng THƯ MỤC chứ không phải ở đuôi tên file như bên `storage.py`:
ở đây tên file là doc_id, và doc_id sinh từ nội dung nên hai thuê bao nạp cùng
một văn bản sẽ có cùng id. Cùng một đường dẫn thì cây của người này ghi đè lên
cây của người kia.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.context import current_tenant_id

logger = logging.getLogger(__name__)

# doc_id là chuỗi hex do `make_doc_id` sinh, nhưng nó đi vào đây từ body của
# request nên không được tin - nó trở thành tên file.
_SAFE_RE = re.compile(r"^[A-Za-z0-9_-]{1,120}$")


class MindmapStoreError(ValueError):
    """Định danh tài liệu không dùng làm tên file được."""


def _safe_id(doc_id: str) -> str:
    """Chặn hẳn doc_id lạ, KHÔNG gọt cho sạch rồi dùng tiếp.

    Gọt thì "../../etc/passwd" thành "etcpasswd" - hết đường thoát thư mục thật,
    nhưng hai doc_id khác nhau lại có thể gọt ra cùng một tên file, và cây của
    tài liệu này ghi đè lên cây của tài liệu kia. doc_id thật luôn là hex nên
    không có gì hợp lệ bị chặn oan.
    """
    doc_id = (doc_id or "").strip()
    if not _SAFE_RE.match(doc_id):
        raise MindmapStoreError(
            f"doc_id không hợp lệ: {doc_id!r} - chỉ nhận chữ, số, '-' và '_'"
        )
    return doc_id


def _tenant_dir() -> Path:
    """Thư mục của thuê bao đang gọi, tạo sẵn nếu chưa có."""
    tenant = current_tenant_id()
    path = Path(get_settings().mindmap_dir) / (str(tenant) if tenant is not None else "chung")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path(doc_id: str) -> Path:
    return _tenant_dir() / f"{_safe_id(doc_id)}.json"


def save(doc_id: str, tree: dict[str, Any], meta: dict[str, Any] | None = None) -> Path:
    """Ghi đè cây của một tài liệu."""
    path = _path(doc_id)
    record = {
        "doc_id": doc_id,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        **(meta or {}),
        "tree": tree,
    }
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Đã lưu sơ đồ tư duy %s", path.name)
    return path


def load(doc_id: str) -> dict[str, Any] | None:
    """Bản ghi đã lưu, hoặc None nếu chưa có / file hỏng."""
    path = _path(doc_id)
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Không đọc được sơ đồ tư duy %s: %s", path.name, exc)
        return None
    return record if isinstance(record, dict) and isinstance(record.get("tree"), dict) else None


def delete(doc_id: str) -> bool:
    """Xoá cây. True nếu có file để xoá."""
    path = _path(doc_id)
    if not path.is_file():
        return False
    path.unlink()
    logger.info("Đã xoá sơ đồ tư duy %s", path.name)
    return True


def list_all() -> list[dict[str, Any]]:
    """Mọi sơ đồ của thuê bao đang gọi, mới nhất lên đầu."""
    from app.documents.mindmap import count_nodes

    items: list[dict[str, Any]] = []
    for path in _tenant_dir().glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            tree = record["tree"]
        except (json.JSONDecodeError, OSError, KeyError, TypeError):
            # File hỏng thì bỏ qua khi liệt kê, không để nó chặn cả danh sách.
            continue
        items.append({
            "doc_id": record.get("doc_id", path.stem),
            "doc_title": record.get("doc_title") or tree.get("title") or path.stem,
            "node_count": count_nodes(tree),
            "saved_at": record.get("saved_at"),
        })
    items.sort(key=lambda item: item.get("saved_at") or "", reverse=True)
    return items

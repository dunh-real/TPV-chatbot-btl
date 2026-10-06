"""Công cụ tra cứu kho tài liệu: tìm đoạn liên quan và đọc lại nguyên văn bản.

Đây là mặt tiền JSON của tầng truy hồi. Node `retrieve` của workflow 1 vẫn gọi
thẳng `HybridRetriever` vì nó cần đối tượng `RetrievalResult` (hạng từng nhánh,
thời gian từng chặng) để ghi trace; tool này trả dict thuần cho những nơi chỉ cần
kết quả - agent định tuyến, API tra cứu, và prompt của các workflow khác.

Hai việc khác nhau, đừng lẫn:
    search_documents - xếp hạng, chỉ giữ vài đoạn khớp nhất, CÓ THỂ bỏ sót
    get_document     - đọc trọn một tài liệu theo đúng thứ tự, KHÔNG bỏ sót
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import get_settings
from app.db.repository import VanBanRepository
from app.db.session import session_scope
from app.rag.retrieval import get_retriever
from app.rag.vectorstore import build_filter, get_vector_store
from app.tools.base import ToolError

logger = logging.getLogger(__name__)


def _snippet(text: str, limit: int = 300) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit].rsplit(" ", 1)[0] + "…"


async def search_documents(
    query: str,
    document_type: str | None = None,
    top_n: int | None = None,
    doc_ids: list[str] | str | None = None,
) -> dict[str, Any]:
    """Tìm các đoạn tài liệu liên quan nhất tới câu hỏi (hybrid 3 nhánh + rerank).

    `document_type` lọc theo trường `doc_type` đã gắn lúc nạp tài liệu (ví dụ
    "quy_dinh", "cong_van"); bỏ trống là tìm toàn kho.
    """
    query = (query or "").strip()
    if not query:
        raise ToolError("search_documents: thiếu câu truy vấn")

    cfg = get_settings()
    if isinstance(doc_ids, str):
        doc_ids = [doc_ids]

    result = await get_retriever().retrieve(
        query=query,
        query_filter=build_filter(
            doc_ids=doc_ids,
            doc_types=[document_type] if document_type else None,
        ),
        top_n=top_n or cfg.rerank_top_n,
    )

    return {
        "query": query,
        "document_type": document_type or "",
        "count": len(result.chunks),
        "hits": [
            {
                "doc_id": chunk.doc_id,
                "doc_title": chunk.doc_title,
                "section": chunk.section,
                "page": chunk.payload.get("page"),
                "source": str(chunk.payload.get("source", "")),
                "text": chunk.text,
                "score": round(chunk.rerank_score, 4),
                "citation": chunk.citation_label(),
            }
            for chunk in result.chunks
        ],
    }


async def get_document(document_id: str, max_chars: int | None = None) -> dict[str, Any]:
    """Đọc lại một tài liệu theo `doc_id` trong kho hoặc số ký hiệu trong sổ văn bản.

    Nhận cả hai loại định danh vì người dùng nhắc tới văn bản theo số ký hiệu
    ("88/BC-HCQT") còn hệ thống lưu theo doc_id băm từ nội dung.
    """
    document_id = (document_id or "").strip()
    if not document_id:
        raise ToolError("get_document: thiếu định danh tài liệu")

    cfg = get_settings()
    max_chars = max_chars or cfg.context_max_chars

    # Sổ văn bản biết số ký hiệu, ngày tháng, file gốc; kho vector biết nội dung.
    record = None
    try:
        async with session_scope() as session:
            repo = VanBanRepository(session)
            record = await repo.get(document_id) or await repo.get_by_doc_id(document_id)
    except Exception as exc:  # noqa: BLE001 - mất CSDL thì vẫn đọc được nội dung từ kho
        logger.warning("Không tra được sổ văn bản: %s", exc)

    doc_id = (record.doc_id if record and record.doc_id else document_id)

    chunks: list[dict[str, Any]] = []
    try:
        chunks = await get_vector_store().scroll_document(doc_id)
    except Exception as exc:  # noqa: BLE001 - Qdrant chết thì vẫn trả được phần siêu dữ liệu
        logger.warning("Không đọc được nội dung tài liệu %s: %s", doc_id, exc)

    text = "\n\n".join(str(chunk.get("text", "")) for chunk in chunks).strip()
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars].rsplit("\n", 1)[0]

    metadata: dict[str, Any] = {}
    if chunks:
        first = chunks[0]
        metadata = {
            "doc_title": first.get("doc_title", ""),
            "source": first.get("source", ""),
            "doc_type": first.get("doc_type", ""),
        }
    if record is not None:
        metadata |= {
            "ma_van_ban": record.ma_van_ban,
            "ten_van_ban": record.ten_van_ban,
            "loai_van_ban": record.loai_van_ban,
            "noi_gui": record.noi_gui,
            "noi_nhan": record.noi_nhan,
            "ngay_van_ban": record.ngay_van_ban.isoformat() if record.ngay_van_ban else None,
            "file_path": record.file_path,
        }

    return {
        "found": bool(chunks or record),
        "document_id": document_id,
        "doc_id": doc_id if chunks else "",
        "metadata": metadata,
        "chunk_count": len(chunks),
        "text": text,
        "truncated": truncated,
        "snippet": _snippet(text),
    }

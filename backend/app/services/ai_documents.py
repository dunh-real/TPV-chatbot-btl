"""Liên kết sổ tài liệu SQL với file MinIO và chỉ mục Qdrant."""
import hashlib
from datetime import timezone
from functools import wraps
from urllib.parse import urlsplit

from qdrant_client import models

from app.core.context import current_principal
from app.db.ai_session import enabled, repository
from app.db.ai_repository import now


def scoped_id(doc_id):
    if not enabled():
        return doc_id
    principal = current_principal()
    user = principal.access.user_id if principal.access else principal.user_id
    return hashlib.sha256(f"{principal.tenant_id}:{user}:{doc_id}".encode()).hexdigest()[:32]


def metadata(row):
    url = urlsplit(row["FileUrl"] or "")
    from app.services.object_storage import parse_reference
    bucket, key = parse_reference(row["FileUrl"]) if url.scheme in {"s3", "minio"} else (None, None)
    return dict(doc_title=row["Title"], source=row["FileName"], original_filename=row["FileName"],
        chunk_count=row["ChunkCount"], file_size_bytes=row["FileSizeBytes"],
        checksum_sha256=row["FileHashSha256"], ingested_at=row["CreationTime"].replace(tzinfo=timezone.utc).timestamp(),
        storage_bucket=bucket, storage_object_key=key,
        doc_type=row["FileName"].rsplit(".", 1)[-1].lower() if "." in row["FileName"] else "")


async def document_metadata(doc_ids):
    from sqlalchemy import and_, or_
    async with repository() as repo:
        result = {}
        table = repo.table("KnowledgeDocuments")
        ids = list(dict.fromkeys(doc_ids))
        for start in range(0, len(ids), 500):
            batch = ids[start:start+500]
            sql_ids = [int(key[4:]) for key in batch if key.startswith("sql:") and key[4:].isdigit()]
            rows = await repo.rows("KnowledgeDocuments", or_(table.c.Id.in_(sql_ids),
                and_(table.c.AiServerDocId.in_(batch), repo.unique_index())),
                                   table.c.IndexStatus == "Indexed", owned=True)
            for row in rows:
                key = f"sql:{row['Id']}" if row["Id"] in sql_ids else row["AiServerDocId"]
                result[key] = metadata(row)
        return result


async def restrict(query_filter=None):
    if not enabled():
        return query_filter
    async with repository() as repo:
        allowed = await repo.document_ids()
    # Danh sách rỗng phải khớp rỗng; không được biến thành bỏ lọc.
    return models.Filter(must=[*( [query_filter] if query_filter else []),
        models.FieldCondition(key="doc_id", match=models.MatchAny(any=allowed))])


def tracked_ingestion(function):
    @wraps(function)
    async def run(self, text, doc_title, doc_id=None, source="", doc_type="", metadata=None, **kwargs):
        if not enabled():
            return await function(self, text, doc_title, doc_id, source, doc_type, metadata, **kwargs)
        from app.rag.ingestion import make_doc_id
        doc_id = doc_id or make_doc_id(source or doc_title, text)
        async with repository() as repo:
            if doc_id.startswith("sql:"):
                raise ValueError("Tiền tố sql: dành cho định danh metadata, không dùng để nạp")
            row_id = await repo.begin_document(doc_id, doc_title, metadata or {})
        try:
            result = await function(self, text, doc_title, doc_id, source, doc_type, metadata, **kwargs)
            if not result.chunk_count:
                raise ValueError("Tài liệu không tạo được đoạn nội dung")
            async with repository() as repo:
                await repo.patch("KnowledgeDocuments", row_id, IndexStatus="Indexed",
                    ProcessingStatus="Extracted", ChunkCount=result.chunk_count, LastIndexedTime=now())
            return result
        except BaseException:
            import anyio
            with anyio.CancelScope(shield=True):
                async with repository() as repo:
                    await repo.patch("KnowledgeDocuments", row_id, IndexStatus="Failed", ProcessingStatus="Failed")
            raise
    return run

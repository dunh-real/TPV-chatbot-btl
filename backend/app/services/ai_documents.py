"""Liên kết sổ tài liệu SQL với file MinIO và chỉ mục Qdrant."""
import hashlib
from zoneinfo import ZoneInfo
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
        checksum_sha256=row["FileHashSha256"], ingested_at=row["CreationTime"].replace(tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")).timestamp(),
        storage_bucket=bucket, storage_object_key=key,
        doc_type=row["FileName"].rsplit(".", 1)[-1].lower() if "." in row["FileName"] else "")


async def document_metadata(doc_ids):
    async with repository() as repo:
        result = {}
        for key in dict.fromkeys(doc_ids):
            row = await repo.document(key)
            if row:
                result[key] = metadata(row)
        return result


async def resolve_ids(doc_ids):
    if not enabled():
        return list(doc_ids)
    async with repository() as repo:
        result = []
        for key in doc_ids:
            row = await repo.document(key)
            if row and row["AiServerDocId"]:
                result.append(row["AiServerDocId"])
        return list(dict.fromkeys(result))


async def public_ids(doc_ids):
    if not enabled():
        return {key:key for key in doc_ids}
    async with repository() as repo:
        result = {}
        for key in dict.fromkeys(doc_ids):
            row = await repo.document(key)
            if row:
                result[key] = f"sql:{row['Id']}"
        return result


async def restrict(query_filter=None):
    if not enabled():
        return query_filter
    async with repository() as repo:
        allowed = await repo.document_ids()
    async def normalized(value):
        if isinstance(value, models.Filter):
            value = value.model_copy(deep=True)
            for field in ("must", "should", "must_not"):
                conditions = getattr(value, field)
                if conditions:
                    setattr(value, field, [await normalized(c) for c in conditions])
            return value
        if isinstance(value, models.FieldCondition) and value.key == "doc_id":
            match = value.match
            values = match.any if isinstance(match, models.MatchAny) else [match.value] if isinstance(match, models.MatchValue) else []
            return models.FieldCondition(key="doc_id", match=models.MatchAny(any=await resolve_ids(values)))
        return value
    return models.Filter(must=[*([await normalized(query_filter)] if query_filter else []),
        models.FieldCondition(key="doc_id", match=models.MatchAny(any=allowed))])


def tracked_ingestion(function):
    @wraps(function)
    async def run(self, text, doc_title, doc_id=None, source="", doc_type="", metadata=None, **kwargs):
        if not enabled():
            return await function(self, text, doc_title, doc_id, source, doc_type, metadata, **kwargs)
        from app.db.ai_session import erp_document
        target = erp_document.get()
        if target:
            row_id, version = target
            async with repository() as repo:
                await repo.lock(f"erp-document:{row_id}", shared=True)
                row = await repo.erp_document(row_id, version)
                index_id = row["AiServerDocId"] or hashlib.sha256(
                    f"erp:{repo.principal.tenant_id}:{row_id}:{version}".encode()).hexdigest()[:32]
                await repo.patch("KnowledgeDocuments", row_id, AiServerDocId=index_id,
                                 IndexStatus="Pending", ProcessingStatus="Extracting")
                failure = None
                try:
                    result = await function(self, text, doc_title, index_id, source, doc_type, metadata, **kwargs)
                    if not result.chunk_count:
                        raise ValueError("Tài liệu không tạo được đoạn nội dung")
                except Exception as exc:
                    failure = exc
                    await repo.patch("KnowledgeDocuments", row_id, IndexStatus="Failed", ProcessingStatus="Failed")
                else:
                    await repo.patch("KnowledgeDocuments", row_id, IndexStatus="Indexed",
                        ProcessingStatus="Extracted", ChunkCount=result.chunk_count, LastIndexedTime=now())
            if failure:
                raise failure
            return result
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

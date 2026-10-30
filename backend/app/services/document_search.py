"""Gom kết quả truy hồi thành tài liệu, chỉ trả metadata công khai."""
from urllib.parse import quote
import logging
from qdrant_client import models

from app.rag.retrieval import get_retriever
from app.rag.vectorstore import build_filter, get_vector_store
from app.schemas.documents import (DocumentDetail, DocumentSearchItem, DocumentSearchResponse,
    DocumentSnippet, DocumentSearchRequest, RelatedDocuments, LegalRelatedItem)


def describe(doc_id, metadata, legal=None):
    linked = bool(metadata.get("storage_bucket") and metadata.get("storage_object_key"))
    url = f"/api/documents/{quote(doc_id, safe='')}"
    label = lambda value: str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    source = label(metadata.get("source"))
    return DocumentDetail(
        doc_id=doc_id, title=metadata.get("doc_title") or source or doc_id,
        source=source, doc_type=metadata.get("doc_type", ""),
        chunk_count=metadata.get("chunk_count", 0), ingested_at=metadata.get("ingested_at"),
        original_filename=label(metadata.get("original_filename")), file_size_bytes=metadata.get("file_size_bytes"),
        original_available=linked, detail_url=url+"/detail", original_url=url+"/original" if linked else None,
        **(legal or {}))


async def search_documents(request, exclude=()):
    query_filter = build_filter(**request.filters.model_dump()) or models.Filter()
    query_filter.must_not = [models.FieldCondition(key="latest", match=models.MatchValue(value=False))]
    if exclude:
        query_filter.must_not.append(models.FieldCondition(key="doc_id", match=models.MatchAny(any=list(exclude))))
    result = await get_retriever().retrieve(query=request.query,
        query_filter=query_filter,
        top_n=min(request.limit * 5, 250), rerank=request.use_rerank)
    groups = {}
    for chunk in result.chunks:
        if chunk.doc_id:
            groups.setdefault(chunk.doc_id, []).append(chunk)
    score = (lambda c: c.rerank_score) if request.use_rerank else (lambda c: c.rrf_score)
    ranked = sorted(groups, key=lambda key: max(map(score, groups[key])), reverse=True)[:request.limit]
    metadata = await get_vector_store().document_metadata(ranked)
    items = []
    for doc_id in ranked:
        if doc_id not in metadata:  # Tài liệu có thể vừa bị xoá sau truy hồi.
            continue
        chunks = sorted(groups[doc_id], key=score, reverse=True)
        items.append(DocumentSearchItem(**describe(doc_id, metadata[doc_id]).model_dump(),
            score=score(chunks[0]), snippets=[DocumentSnippet(text=c.text, section=c.section,
                page=c.payload.get("page")) for c in chunks[:3]]))
    return DocumentSearchResponse(items=items, returned_count=len(items),
        candidate_documents=len(groups), score_kind="rerank" if request.use_rerank else "rrf")


async def _legal_candidates(doc_id, limit):
    from app.core.config import get_settings
    if not get_settings().legal_versioning_enabled:
        return [], [], "disabled"
    from sqlalchemy import select, or_
    try:
        from app.db.models import LegalDocument, LegalAction
        from app.db.session import session_scope
        async with session_scope() as session:
            base = (await session.execute(select(LegalDocument).where(
                LegalDocument.doc_id == doc_id).order_by(LegalDocument.legal_id).limit(1))).scalar_one_or_none()
            if base is None:
                return [], [], "not_registered"
            numbers = (await session.execute(select(LegalDocument.doc_id).where(
                LegalDocument.so_hieu == base.so_hieu, LegalDocument.doc_id != doc_id)
                .order_by(LegalDocument.doc_id).limit(limit))).scalars().all() if base.so_hieu else []
            # Chỉ dùng quan hệ đã áp dụng, không suy luận thay thế từ độ tương đồng.
            actions = (await session.execute(select(LegalAction).where(
                LegalAction.status == "applied", or_(LegalAction.source_legal_id == base.legal_id,
                LegalAction.target_legal_id == base.legal_id)).order_by(LegalAction.action_id).limit(limit * 4))).scalars().all()
            links = {}
            for action in actions:
                outgoing = action.source_legal_id == base.legal_id
                other = action.target_legal_id if outgoing else action.source_legal_id
                if other and other != base.legal_id:
                    links[(other, action.op, "outgoing" if outgoing else "incoming")] = None
            ids = list(dict.fromkeys(key[0] for key in links))
            others = dict((await session.execute(select(LegalDocument.legal_id, LegalDocument.doc_id)
                .where(LegalDocument.legal_id.in_(ids)))).all()) if ids else {}
            relations = [(others[key], op, direction) for key, op, direction in links if key in others]
            return list(dict.fromkeys(numbers)), relations[:limit], "ok"
    except Exception:
        logging.getLogger(__name__).exception("Không đọc được quan hệ pháp luật")
        return [], [], "unavailable"


async def related_documents(doc_id, metadata, limit):
    store = get_vector_store()
    query = build_filter(doc_ids=[doc_id])
    query.must_not = [models.FieldCondition(key="latest", match=models.MatchValue(value=False))]
    chunks = await store.scroll_filtered(query, limit=1)
    text = str(chunks[0].get("text") or "")[:1500] if chunks else ""
    query_text = f"{metadata.get('doc_title') or metadata.get('source') or doc_id}\n{text}"[:4000]
    topics = await search_documents(DocumentSearchRequest(query=query_text, limit=limit), exclude=[doc_id])
    numbers, relations, legal_status = await _legal_candidates(doc_id, limit)
    candidates = await store.document_metadata([*numbers, *(item[0] for item in relations)])
    return RelatedDocuments(doc_id=doc_id, same_topic=topics.items,
        same_number=[describe(key, candidates[key]) for key in numbers if key in candidates],
        legal_relations=[LegalRelatedItem(**describe(key, candidates[key]).model_dump(), relation=op, direction=direction)
                         for key, op, direction in relations if key in candidates and key != doc_id],
        legal_status=legal_status)

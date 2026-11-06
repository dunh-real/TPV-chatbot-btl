"""Kho pháp luật chỉ đọc dùng chung; chỉ nhận tài liệu đã đánh dấu và xác minh."""
from qdrant_client import models


def scope():
    return models.Filter(must=[
        models.FieldCondition(key="knowledge_scope", match=models.MatchValue(value="shared_legal")),
        models.FieldCondition(key="legal_index_status", match=models.MatchValue(value="ready")),
        models.IsEmptyCondition(is_empty=models.PayloadField(key="document_id")),
    ])


async def metadata(doc_ids):
    from app.rag.vectorstore import get_vector_store
    keys = [key for key in dict.fromkeys(doc_ids) if not key.startswith("sql:")]
    if not keys:
        return {}
    return await get_vector_store().raw_document_metadata(keys, scope())


def object_info(client, bucket, key):
    """S3 proxy cho LIST/GET nhưng có thể chặn HEAD; lấy đúng object từ LIST."""
    for item in client.list_objects(bucket, prefix=key, recursive=True):
        if item.object_name == key:
            return item
    raise FileNotFoundError(key)

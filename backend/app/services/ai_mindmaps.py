"""Lưu/đọc sơ đồ tư duy trên SQL khi bật kết nối AI; giữ JSON cho chế độ cũ."""
import json

from app.db.ai_repository import dumps, now
from app.db.ai_session import enabled, repository
from app.documents.mindmap import count_nodes
from app.services import mindmap_store


def record(row):
    return dict(doc_id=row["DocId"], doc_title=row["Title"], tree=json.loads(row["TreeJson"]),
                saved_at=(row["LastModificationTime"] or row["CreationTime"]).isoformat())


async def load(doc_id):
    if not enabled():
        return mindmap_store.load(doc_id)
    async with repository() as repo:
        table = repo.table("MindMaps")
        rows = await repo.rows("MindMaps", table.c.DocId == doc_id, owned=True)
        return record(rows[0]) if rows else None


async def save(doc_id, tree, meta=None):
    if not enabled():
        return mindmap_store.save(doc_id, tree, meta)
    async with repository() as repo:
        if not await repo.document(doc_id):
            raise PermissionError("Không được đọc tài liệu nguồn của sơ đồ")
        table = repo.table("MindMaps")
        rows = await repo.rows("MindMaps", table.c.DocId == doc_id, owned=True)
        values = dict(Title=(meta or {}).get("doc_title") or tree.get("title") or doc_id,
                      TreeJson=dumps(tree), NodeCount=count_nodes(tree))
        if rows:
            await repo.patch("MindMaps", rows[0]["Id"], **values)
        else:
            await repo.add("MindMaps", UserId=repo.user_id, DocId=doc_id, **values)


async def list_all():
    if not enabled():
        return mindmap_store.list_all()
    async with repository() as repo:
        rows = await repo.rows("MindMaps", owned=True)
        return [{"doc_id": r["DocId"], "doc_title": r["Title"], "node_count": r["NodeCount"],
                 "saved_at": (r["LastModificationTime"] or r["CreationTime"]).isoformat()} for r in rows]


async def delete(doc_id):
    if not enabled():
        return mindmap_store.delete(doc_id)
    async with repository() as repo:
        table = repo.table("MindMaps")
        rows = await repo.rows("MindMaps", table.c.DocId == doc_id, owned=True)
        if rows:
            await repo.patch("MindMaps", rows[0]["Id"], IsDeleted=True,
                             DeletionTime=now(), DeleterUserId=repo.user_id)
        return bool(rows)

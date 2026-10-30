"""Đọc/ghi bảng AI thực tế; luôn giới hạn tenant, chủ sở hữu và xóa mềm."""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, insert, or_, select, update

from app.core.context import current_principal


def now():
    return datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).replace(tzinfo=None)


def lease_now():
    """Đồng hồ UTC riêng cho lease; giữ tương thích lease đang tồn tại."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def dumps(value):
    return json.dumps(value, ensure_ascii=False, default=str)


class AIRepository:
    def __init__(self, connection, tables):
        self.conn, self.tables = connection, tables
        self.principal = current_principal()
        access = self.principal.access
        if access is None or access.tenant_id != self.principal.tenant_id:
            raise PermissionError("Cần tài khoản ERP hợp lệ trong đúng tenant")
        self.user_id = access.user_id

    def table(self, name):
        return self.tables["AI_" + name]

    async def lock(self, key, shared=False):
        if self.conn.dialect.name == "mssql":
            import hashlib
            from sqlalchemy import text
            scope = (f"{self.principal.tenant_id}:{key}" if shared else key if key.startswith("document:")
                     else f"{self.principal.tenant_id}:{self.user_id}:{key}")
            resource = hashlib.sha256(scope.encode()).hexdigest()
            result = await self.conn.execute(text("DECLARE @r int; EXEC @r=sp_getapplock "
                "@Resource=:key, @LockMode='Exclusive', @LockOwner='Transaction', @LockTimeout=15000; SELECT @r"),
                {"key": "tpv-ai-" + resource})
            if result.scalar_one() < 0:
                raise TimeoutError("Không lấy được khóa ghi AI")

    def scope(self, table, owned=False):
        terms = [table.c.TenantId == self.principal.tenant_id, table.c.IsDeleted == False]
        if owned and table.name == "AI_KnowledgeDocuments":
            terms.append(self.document_scope(table))
        elif owned and not self.principal.is_admin:
            owner = table.c.UserId if "UserId" in table.c else table.c.CreatorUserId
            terms.append(owner == self.user_id)
        return and_(*terms)

    def document_scope(self, table):
        from app.services.partition_access import document_scope
        return document_scope(self, table)

    async def erp_document(self, row_id, version):
        row = await self.document(f"sql:{row_id}", include_unready=True)
        if not row:
            raise PermissionError("Tài liệu không thuộc phạm vi được phép")
        if row.get("AiServerDocId"):
            table = self.table("KnowledgeDocuments")
            count = (await self.conn.execute(select(func.count()).select_from(table).where(
                table.c.AiServerDocId == row["AiServerDocId"], table.c.IsDeleted == False))).scalar_one()
            if count != 1:
                raise ValueError("AiServerDocId đang trùng bản ghi; BE cần đối soát trước khi nạp")
        if row["VersionNo"] != version:
            raise ValueError("version_no không khớp bản ghi ERP")
        return row

    async def add(self, name, **values):
        table = self.table(name)
        audit = dict(TenantId=self.principal.tenant_id, CreatorUserId=self.user_id,
                     CreationTime=now(), IsDeleted=False)
        values = {**{k: v for k, v in audit.items() if k in table.c}, **values}
        return (await self.conn.execute(insert(table).values(**values).returning(table.c.Id))).scalar_one()

    async def patch(self, name, row_id, **values):
        table = self.table(name)
        if "LastModificationTime" in table.c:
            values.update(LastModificationTime=now(), LastModifierUserId=self.user_id)
        await self.conn.execute(update(table).where(table.c.Id == row_id, self.scope(table, True)).values(**values))

    async def rows(self, name, *terms, owned=False):
        table = self.table(name)
        return list((await self.conn.execute(select(table).where(self.scope(table, owned), *terms))).mappings())

    async def number_exists(self, number, exclude_task=None):
        """Số văn bản không trùng trong tenant, gồm cả tác vụ đang thực hiện."""
        docs = self.table("KnowledgeDocuments")
        found = await self.conn.execute(select(docs.c.Id).where(self.scope(docs),
            or_(docs.c.DocumentNumber == number, docs.c.DocCode == number)).limit(1))
        if found.first():
            return True
        tasks = self.table("Tasks")
        def field(column, path):
            return func.json_value(case((func.isjson(column) == 1, column), else_="{}"), path)
        query = select(tasks.c.Id).where(self.scope(tasks), tasks.c.Status.in_(["Queued", "Processing", "Completed"]),
            or_(field(tasks.c.InputJson, "$.inputs.so_ky_hieu") == number,
                field(tasks.c.ResultJson, "$.so_ky_hieu") == number))
        if exclude_task is not None:
            query = query.where(tasks.c.Id != exclude_task)
        return (await self.conn.execute(query.limit(1))).first() is not None

    async def document(self, doc_id, include_unready=False):
        table = self.table("KnowledgeDocuments")
        sql_id = doc_id.removeprefix("sql:") if doc_id.startswith("sql:") else ""
        terms = [table.c.Id == int(sql_id)] if sql_id.isdigit() else [table.c.AiServerDocId == doc_id]
        if not include_unready:
            terms.append(table.c.IndexStatus == "Indexed")
            if not sql_id.isdigit():
                terms.append(self.unique_index())
        rows = await self.rows("KnowledgeDocuments", *terms, owned=True)
        if len(rows) > 1:
            raise ValueError("Trùng AiServerDocId trong cùng phạm vi dữ liệu")
        return dict(rows[0]) if rows else None

    async def document_ids(self):
        table = self.table("KnowledgeDocuments")
        rows = await self.conn.execute(select(table.c.AiServerDocId).where(
            self.scope(table, True), table.c.IndexStatus == "Indexed", self.unique_index()))
        return list(rows.scalars())

    def unique_index(self):
        table = self.table("KnowledgeDocuments")
        unique = select(table.c.AiServerDocId).where(table.c.IsDeleted == False,
            table.c.AiServerDocId.is_not(None), table.c.AiServerDocId != "").group_by(table.c.AiServerDocId)
        return table.c.AiServerDocId.in_(unique.having(func.count() == 1).correlate(None))

    async def partition(self, partition_id=None):
        from app.core.config import get_settings
        table = self.table("DataPartitions")
        partition_id = partition_id or get_settings().ai_default_partition_id
        terms = [self.scope(table), table.c.IsActive == True]
        if partition_id:
            terms.append(table.c.Id == partition_id)
        else:
            terms.extend([table.c.PartitionType == "Personal", table.c.OwnerUserId == self.user_id])
        if not self.principal.is_admin:
            terms.extend([table.c.PartitionType == "Personal", table.c.OwnerUserId == self.user_id])
        row = (await self.conn.execute(select(table.c.Id).where(*terms).order_by(table.c.Id))).first()
        if row is None:
            raise PermissionError("Chưa có phân vùng cá nhân hợp lệ; admin cần chọn phân vùng cùng tenant")
        return row[0]

    async def begin_document(self, doc_id, title, metadata):
        await self.lock("document:" + doc_id)
        existing = await self.document(doc_id, include_unready=True)
        table = self.table("KnowledgeDocuments")
        collisions = (await self.conn.execute(select(func.count()).select_from(table).where(
            table.c.AiServerDocId == doc_id, table.c.IsDeleted == False))).scalar_one()
        if collisions and (existing is None or collisions > 1):
            raise PermissionError("doc_id trùng hoặc thuộc phạm vi dữ liệu khác")
        # Không nhận partition/folder tùy ý từ metadata của client.
        if existing:
            values = dict(IndexStatus="Pending", ProcessingStatus="Extracting")
            if metadata.get("storage_bucket") and metadata.get("storage_object_key"):
                checksum = metadata.get("checksum_sha256")
                if existing["FileHashSha256"] and checksum != existing["FileHashSha256"]:
                    raise ValueError("File khác SHA-256 phải nạp bằng doc_id mới")
                values.update(FileUrl=f"minio://{metadata['storage_bucket']}/{metadata['storage_object_key']}",
                    FileHashSha256=checksum, FileSizeBytes=metadata.get("file_size_bytes", 0))
            await self.patch("KnowledgeDocuments", existing["Id"], **values)
            return existing["Id"]
        partition = await self.partition()
        bucket, key = metadata.get("storage_bucket"), metadata.get("storage_object_key")
        return await self.add("KnowledgeDocuments", AiServerDocId=doc_id,
            DocumentNumber="AI-" + doc_id, PartitionId=partition, Title=title[:500],
            FileName=metadata.get("original_filename") or title[:260],
            FileUrl=f"minio://{bucket}/{key}" if bucket and key else "",
            FileSizeBytes=metadata.get("file_size_bytes", 0), FileHashSha256=metadata.get("checksum_sha256"),
            HasTextLayer=True, ProcessingStatus="Extracting", QualityStatus="Pending",
            VersionNo=1, LifecycleStatus="Active", ApprovalStatus="Draft", IndexStatus="Pending", ChunkCount=0)

    async def templates(self, kind=None):
        table = self.table("ReportTemplate")
        terms = [table.c.IsActive == True, table.c.Status == "Published"]
        if kind:
            terms.append(table.c.TemplateType == kind)
        rows = await self.rows("ReportTemplate", *terms)
        # Chính sách mẫu có phạm vi riêng cần được triển khai trước khi mở cho người khác.
        return [dict(r) for r in rows if self.principal.is_admin or not r["UsageScopeJson"]]

    async def template_version(self, template):
        table = self.table("TemplateVersions")
        rows = await self.rows("TemplateVersions", table.c.TemplateId == template["Id"],
            table.c.VersionNo == template["CurrentVersionNo"], table.c.Status == "Published")
        return dict(rows[0]) if rows else None

    async def conversation(self, key, create=False):
        if create:
            await self.lock("conversation:" + key)
        table = self.table("ChatConversations")
        # Hội thoại luôn thuộc người dùng, kể cả admin không đọc hộ qua API chat.
        terms = [table.c.AiConversationId == key, table.c.UserId == self.user_id]
        rows = await self.rows("ChatConversations", *terms)
        if rows:
            return rows[0]["Id"]
        if create:
            return await self.add("ChatConversations", UserId=self.user_id, Title="Cuộc trò chuyện mới",
                                  AiConversationId=key, AiFeatureType="Chatbot", MessageCount=0)

    async def history(self, key, limit):
        conversation_id = await self.conversation(key)
        if conversation_id is None:
            return []
        table = self.table("ChatMessages")
        rows = list((await self.conn.execute(select(table).where(table.c.ConversationId == conversation_id)
            .order_by(table.c.Id.desc()).limit(limit))).mappings())
        return [{"role": "user" if r["IsFromUser"] else "assistant", "content": r["Message"],
                 "ts": r["CreationTime"].replace(tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")).timestamp()} for r in reversed(rows)]

    async def append_turns(self, key, turns):
        turns = [(role, text) for role, text in turns if role in {"user", "assistant"} and text]
        if not turns:
            return 0
        conversation_id = await self.conversation(key, create=True)
        for role, content in turns:
            await self.add("ChatMessages", ConversationId=conversation_id, Message=content,
                           IsFromUser=role == "user", NoEvidence=False)
        table = self.table("ChatConversations")
        await self.conn.execute(update(table).where(table.c.Id == conversation_id, self.scope(table),
            table.c.UserId == self.user_id).values(LastMessageTime=now(), MessageCount=table.c.MessageCount+len(turns)))
        return len(turns)

    async def clear_history(self, key):
        conversation_id = await self.conversation(key)
        if conversation_id is not None:
            await self.patch("ChatConversations", conversation_id, IsDeleted=True,
                             DeletionTime=now(), DeleterUserId=self.user_id)

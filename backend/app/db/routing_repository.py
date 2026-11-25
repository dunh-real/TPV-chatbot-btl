"""Danh mục và kết quả UC33 trên MSSQL; không tạo bảng hoặc ghi SQLite."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import MetaData, Table, select

from app.core.context import current_principal
from app.db.erp_session import get_erp_engine
from app.db.ai_repository import dumps


def vietnam_now():
    return datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).replace(tzinfo=None)


async def reflected(conn, name):
    try:
        return await conn.run_sync(lambda c: Table(name, MetaData(), autoload_with=c, resolve_fks=False))
    except Exception:
        return None


async def catalogs():
    """Chỉ đọc đơn vị và lĩnh vực còn hiệu lực của đúng tenant."""
    tenant = current_principal().tenant_id
    if type(tenant) is not int:
        raise HTTPException(400, "Cần tenant cụ thể")
    async with get_erp_engine().connect() as conn:
        result = []
        for name in ("Dms_WorkDepartment", "AI_Categories"):
            table = await reflected(conn, name)
            if table is None:
                if name == "AI_Categories":
                    result.append([
                        {"field_code": "HANH_CHINH", "field_name": "Hành chính & Quản trị", "description": "Công tác hành chính, văn thư, quản trị nội bộ"},
                        {"field_code": "HOA_CHAT", "field_name": "Hóa chất & An toàn", "description": "Quản lý hóa chất, an toàn kho và phòng thí nghiệm"},
                        {"field_code": "KE_HOACH", "field_name": "Kế hoạch & Dự án", "description": "Lập kế hoạch, tiến độ và dự án"},
                        {"field_code": "TAI_CHINH", "field_name": "Tài chính & Kế toán", "description": "Tài chính, dự toán, thanh quyết toán"}
                    ])
                else:
                    result.append([])
                continue
            terms = [table.c.TenantId == tenant]
            if "IsDeleted" in table.c:
                terms.append(table.c.IsDeleted == False)
            if "IsActive" in table.c:
                terms.append(table.c.IsActive == True)
            if name == "AI_Categories" and "CategoryType" in table.c:
                terms.append(table.c.CategoryType == "Field")
            rows = (await conn.execute(select(table).where(*terms))).mappings()
            if name == "Dms_WorkDepartment":
                result.append([dict(id=r["Id"], code=(r["Code"] or r["DepartmentCode"] or "").strip(),
                    display_name=r["DisplayName"], description=r["Describe"] or "") for r in rows])
            else:
                result.append([dict(field_code=(r["Code"] or "").strip(), field_name=r["Name"],
                    description=r["Note"] or "") for r in rows])
    return result


async def erp_task(repo, task_id):
    table = repo.table("Tasks")
    rows = await repo.rows("Tasks", table.c.Id == task_id, table.c.UserId == repo.user_id)
    if not rows:
        raise HTTPException(404, "Không tìm thấy tác vụ ERP trong phạm vi tài khoản")
    return rows[0]


async def save(repo, result, inputs):
    """Chế độ trực tiếp tạo tác vụ/sản phẩm; chế độ ERP không đi qua hàm này."""
    stamp = vietnam_now()
    task_id = await repo.add("Tasks", UserId=repo.user_id, TaskType="Routing",
        AgentCode="document_routing", Status="Completed", ProgressPercentage=100,
        NotifyOnFinish=False, InputJson=dumps(inputs), ResultJson=dumps(result),
        StartTime=stamp, EndTime=stamp, CreationTime=stamp)
    table = await reflected(repo.conn, "AI_DocumentRoutings")
    values = dict(TenantId=repo.principal.tenant_id, UserId=repo.user_id, TaskId=task_id,
        SourceFileName=inputs.get("source_name", "Văn bản nhập trực tiếp"),
        DocumentType=result.get("DocumentTypeCode"), Topic=result.get("FieldCode"), TopicKey=result.get("FieldCode"),
        TopicConfidence=result.get("AiConfidence"), Urgency=result.get("UrgencyLevel"),
        Summary=result.get("AiReason"), SuggestionJson=dumps(result), RoutingJson=dumps(result), Status=result.get("Status"),
        CreationTime=stamp, CreatorUserId=repo.user_id, IsDeleted=False)
    doc_id = inputs.get("document_id", "")
    if doc_id.startswith("sql:") and doc_id[4:].isdigit():
        values["KnowledgeDocumentId"] = int(doc_id[4:])
        values["DocumentId"] = int(doc_id[4:])
    filtered_values = {k: v for k, v in values.items() if k in table.c}
    row_id = (await repo.conn.execute(table.insert().values(**filtered_values).returning(table.c.Id))).scalar_one()
    result.update(RoutingId=str(row_id), ai_task_id=task_id)
    update_vals = {}
    if "SuggestionJson" in table.c:
        update_vals["SuggestionJson"] = dumps(result)
    if "RoutingJson" in table.c:
        update_vals["RoutingJson"] = dumps(result)
    if update_vals:
        await repo.conn.execute(table.update().where(table.c.Id == row_id, repo.scope(table, True))
                                .values(**update_vals))
    await repo.patch("Tasks", task_id, ResultJson=dumps(result),
        RelatedEntityType="AI_DocumentRoutings", RelatedEntityId=row_id)
    await repo.add("OutputVersions", OwnerType="Routing", OwnerId=row_id, VersionNo=1,
        ContentJson=dumps(result), ChangeNote="Gợi ý phân luồng UC33", CreationTime=stamp)
    return result


async def load(repo, routing_id):
    table = await reflected(repo.conn, "AI_DocumentRoutings")
    row = (await repo.conn.execute(select(table).where(repo.scope(table, True),
        table.c.Id == routing_id))).mappings().first()
    if not row:
        raise HTTPException(404, "Không tìm thấy gợi ý trong phạm vi tài khoản")
    raw = row.get("RoutingJson") or row.get("SuggestionJson")
    return json.loads(raw)

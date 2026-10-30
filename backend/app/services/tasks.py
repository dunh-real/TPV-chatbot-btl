"""Theo dõi tác vụ UC37, quyền chủ sở hữu và thông báo riêng trên MSSQL."""
import json
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import and_, func, insert, select, update

from app.core.config import get_settings
from app.db.ai_repository import dumps, now, lease_now
from app.agents.quyen import QUYEN_TAC_VU, QUYEN_TAC_VU_XOA, QUYEN_TAC_VU_XEM_TAT_CA

PERMISSIONS = {"Routing": "Ai.Routing", "Review": "Ai.DocumentReview", "Summarize": "Ai.Summarize",
    "TaskExtract": "Ai.TaskExtract", "Draft": "Ai.ReportDrafting",
    "Aggregate": "Ai.ReportSummary.Create", "Slide": "Ai.Slide.Create", "File": "Ai.AiChatbot"}
OPERATIONS = {"Review": "run_document_workflow", "Summarize": "run_summary_workflow",
    "TaskExtract": "run_analysis_workflow", "Draft": "run_draft_workflow",
    "Aggregate": "run_aggregate_workflow", "Slide": "run_presentation_workflow"}
PRODUCTS = {"AI_DocumentRoutings": "Routing", "AI_DocumentReviews": "Review", "AI_DocumentDrafts": "Draft",
    "AI_ReportSummaries": "Aggregate", "AI_Slides": "Slide", "AI_Tasks": "Task"}
TERMINAL = {"Completed", "Failed", "Cancelled"}


def payload(row):
    value = load(row.get("InputJson"), {})
    return value if isinstance(value, dict) else {}


def load(value, default=None):
    try:
        return json.loads(value) if value else default
    except (ValueError, TypeError):
        return default


def metadata(row):
    return payload(row).get("_uc37", {})


def deadline():
    return (lease_now() + timedelta(seconds=get_settings().task_lease_seconds)).isoformat(timespec="seconds")


def clean(value):
    """API không trả đường dẫn nội bộ, bucket/key hoặc lease token."""
    hidden = {"output_path", "file_path", "source_url", "storage_bucket", "storage_object_key", "_uc37"}
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items() if k not in hidden}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def allowed(repo, kind):
    return kind in PERMISSIONS and repo.principal.can(PERMISSIONS[kind])


def require(repo, kind):
    if not allowed(repo, kind):
        raise HTTPException(403, "Không có quyền thực hiện nghiệp vụ của tác vụ")


def require_access(repo):
    if not repo.principal.can(QUYEN_TAC_VU):
        raise HTTPException(403, "Thiếu quyền theo dõi tác vụ AI")


def task_scope(repo, table):
    terms = [repo.scope(table)]
    if not repo.principal.can(QUYEN_TAC_VU_XEM_TAT_CA):
        terms.append(table.c.UserId == repo.user_id)
    return and_(*terms)


async def get_task(repo, task_id, locked=False, internal=False):
    table = repo.table("Tasks")
    if not internal:
        require_access(repo)
    scope = and_(repo.scope(table), table.c.UserId == repo.user_id) if internal else task_scope(repo, table)
    query = select(table).where(scope, table.c.Id == task_id)
    if locked:
        query = query.with_hint(table, "WITH (UPDLOCK, ROWLOCK)", dialect_name="mssql")
    row = (await repo.conn.execute(query)).mappings().first()
    if row is None:
        raise HTTPException(404, "Không tìm thấy tác vụ")
    return dict(row)


def summary(row, repo=None):
    info = metadata(row)
    deleted = bool(info.get("output_deleted"))
    replayable = row["AgentCode"] in set(OPERATIONS.values()) | {"generate_docx", "generate_presentation"}
    args = payload(row)
    needs_source = row["TaskType"] in {"Review", "Summarize", "TaskExtract"} or (
        row["TaskType"] in {"Draft", "Slide"} and args.get("nguon") == "tai_lieu")
    replayable = replayable and (not needs_source or bool(args.get("source_url") or args.get("file_id")))
    return dict(id=row["Id"], kind=row["TaskType"], agent=row["AgentCode"], status=row["Status"],
        progress=row["ProgressPercentage"], created_at=row["CreationTime"], started_at=row["StartTime"],
        ended_at=row["EndTime"], retry_of_task_id=row["RetryOfTaskId"], notify_on_finish=row["NotifyOnFinish"],
        output_deleted=deleted, can_retry=row["Status"] == "Failed" and replayable and not info.get("unreplayable"),
        can_delete=row["Status"] in TERMINAL and not deleted and (repo is None or (
            repo.principal.can(QUYEN_TAC_VU_XOA) and (row["UserId"] == repo.user_id or repo.principal.is_admin))),
        download_url=f"/api/tasks/{row['Id']}/result" if row["Status"] == "Completed" and not deleted else None)


async def list_tasks(repo, page, page_size, status=None, kind=None, since=None, until=None):
    require_access(repo)
    table = repo.table("Tasks")
    terms = [task_scope(repo, table)]
    if status:
        terms.append(table.c.Status == status)
    if kind:
        terms.append(table.c.TaskType == kind)
    if since:
        terms.append(table.c.CreationTime >= since)
    if until:
        terms.append(table.c.CreationTime <= until)
    count = (await repo.conn.execute(select(func.count()).select_from(table).where(*terms))).scalar_one()
    rows = (await repo.conn.execute(select(table).where(*terms).order_by(table.c.Id.desc())
        .offset((page-1)*page_size).limit(page_size))).mappings()
    return dict(items=[summary(dict(r), repo) for r in rows], total=count, page=page, page_size=page_size)


async def output_owner(repo, row):
    table = repo.table("OutputVersions")
    versions = await repo.rows("OutputVersions", table.c.OwnerType == "Task", table.c.OwnerId == row["Id"])
    if versions:
        return "Task", row["Id"]
    return PRODUCTS.get(row["RelatedEntityType"]), row["RelatedEntityId"]


async def latest_output(repo, row):
    owner, owner_id = await output_owner(repo, row)
    table = repo.table("OutputVersions")
    rows = await repo.rows("OutputVersions", table.c.OwnerType == owner, table.c.OwnerId == owner_id)
    return load(max(rows, key=lambda v:v["VersionNo"])["ContentJson"], {}) if rows else load(row["ResultJson"], {})


async def detail(repo, task_id):
    row = await get_task(repo, task_id)
    out = summary(row, repo)
    out["can_retry"] = out["can_retry"] and allowed(repo, row["TaskType"])
    out.update(input=clean(payload(row)), result=clean(load(row["ResultJson"])),
               error=row["ErrorMessage"], versions=[], sources=[], template=None)
    owner_type, owner_id = await output_owner(repo, row)
    if not owner_type or not owner_id or out["output_deleted"]:
        return out
    versions = repo.table("OutputVersions")
    rows = await repo.rows("OutputVersions", versions.c.OwnerType == owner_type,
                           versions.c.OwnerId == owner_id)
    out["versions"] = [dict(id=v["Id"], version=v["VersionNo"], created_at=v["CreationTime"],
        content=clean(load(v["ContentJson"])),
        download_url=f"/api/tasks/{task_id}/result?version_id={v['Id']}") for v in sorted(rows, key=lambda v: v["VersionNo"])]
    refs = repo.table("SourceReferences")
    for ref in await repo.rows("SourceReferences", refs.c.OwnerType == owner_type, refs.c.OwnerId == owner_id):
        source = dict(document_id=ref["DocumentId"], version=ref["DocumentVersionNo"], page=ref["PageNumber"])
        if ref["DocumentId"]:
            doc = await repo.document(f"sql:{ref['DocumentId']}")
            source.update(available=bool(doc), title=doc["Title"] if doc else None,
                          detail_url=f"/api/documents/sql:{ref['DocumentId']}/detail" if doc else None)
        else:
            source.update(available=bool(ref["ExternalSourceRef"] or payload(row).get("source_url")), download_url=f"/api/tasks/{task_id}/source")
        out["sources"].append(source)
    result = await latest_output(repo, row)
    result = result if isinstance(result, dict) else {}
    template_id = result.get("template_id") or row["TemplateId"]
    if template_id:
        table = repo.table("ReportTemplate")
        templates = await repo.rows("ReportTemplate", table.c.Id == template_id)
        if templates:
            out["template"] = dict(id=template_id, code=templates[0]["Code"], name=templates[0]["Name"],
                version=result.get("template_version"), download_url=f"/api/tasks/{task_id}/template"
                if result.get("template_version") else None)
    return out


async def audit(repo, task_id, event):
    await repo.add("ExecutionLogs", UserId=repo.user_id, TaskId=task_id, EventType=event,
                   Status="Completed", HasCitations=False, NoEvidence=False)


async def delete_output(repo, task_id):
    row = await get_task(repo, task_id, locked=True)
    if not repo.principal.can(QUYEN_TAC_VU_XOA) or (row["UserId"] != repo.user_id and not repo.principal.is_admin):
        raise HTTPException(403, "Không có quyền xóa đầu ra tác vụ này")
    if row["Status"] not in TERMINAL:
        raise HTTPException(409, "Không xóa đầu ra khi tác vụ đang chờ hoặc đang chạy")
    info = payload(row)
    if metadata(row).get("output_deleted"):
        return
    owner_type = PRODUCTS.get(row["RelatedEntityType"])
    owner_id = row["RelatedEntityId"]
    if owner_type and owner_id:
        if owner_type != "Task":
            table = repo.table("Tasks")
            shared = (await repo.conn.execute(select(table.c.Id).where(repo.scope(table),
                table.c.RelatedEntityType == row["RelatedEntityType"], table.c.RelatedEntityId == owner_id,
                table.c.Id != task_id))).first()
            if shared:
                raise HTTPException(409, "Sản phẩm còn được tác vụ khác tham chiếu")
            await repo.patch(row["RelatedEntityType"].removeprefix("AI_"), owner_id,
                             IsDeleted=True, DeletionTime=now(), DeleterUserId=repo.user_id)
        for name in ("OutputVersions", "SourceReferences"):
            table = repo.table(name)
            await repo.conn.execute(update(table).where(repo.scope(table, True),
                table.c.OwnerType == owner_type, table.c.OwnerId == owner_id).values(
                    IsDeleted=True, DeletionTime=now(), DeleterUserId=repo.user_id))
    info.setdefault("_uc37", {})["output_deleted"] = True
    # Giữ TextSearch làm tombstone để endpoint download cũ không rơi về file local.
    await repo.patch("Tasks", task_id, ResultJson=None, OutputFileUrl=None, InputJson=dumps(info))
    await audit(repo, task_id, "OutputDeleted")


async def notifications(repo, limit=30):
    require_access(repo)
    table = repo.table("Tasks")
    rows = (await repo.conn.execute(select(table).where(repo.scope(table), table.c.UserId == repo.user_id,
        table.c.TaskType.in_(list(PERMISSIONS)), table.c.NotifyOnFinish == True,
        table.c.Status.in_(list(TERMINAL))).order_by(table.c.EndTime.desc(), table.c.Id.desc()).limit(limit))).mappings()
    return [dict(task_id=r["Id"], status=r["Status"], kind=r["TaskType"], ended_at=r["EndTime"],
        is_read=bool(metadata(r).get("notification_read_at")), link=f"/ui/#tasks/{r['Id']}") for r in rows if metadata(r).get("managed")]


async def mark_read(repo, task_id):
    row = await get_task(repo, task_id, locked=True)
    if row["UserId"] != repo.user_id:
        raise HTTPException(404, "Không tìm thấy thông báo")
    data = payload(row)
    data.setdefault("_uc37", {})["notification_read_at"] = now().isoformat()
    await repo.patch("Tasks", task_id, InputJson=dumps(data))

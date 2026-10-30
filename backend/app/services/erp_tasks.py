"""Dùng chung tác vụ ERP: AI chỉ ghi tiến độ, phiên bản và nguồn."""
import inspect
from pathlib import Path

import anyio
from fastapi import HTTPException
from sqlalchemy import func, select, update

from app.db.ai_repository import dumps
from app.db.ai_session import repository, current_task_id
from app.services.object_storage import ObjectStorage


async def require_task(repo, task_id, kind=None):
    from app.services.tasks import PERMISSIONS
    table = repo.table("Tasks")
    rows = await repo.rows("Tasks", table.c.Id == task_id, table.c.UserId == repo.user_id)
    if not rows:
        raise HTTPException(404, "Không tìm thấy tác vụ ERP trong phạm vi tài khoản")
    row = dict(rows[0])
    if kind and not repo.principal.can(PERMISSIONS[kind]):
        raise HTTPException(403, "Thiếu quyền nghiệp vụ của tác vụ")
    if kind and kind != "File" and row.get("TaskType") not in {None, kind}:
        raise HTTPException(409, "Loại tác vụ ERP không khớp API nghiệp vụ")
    if row["Status"] in {"Completed", "Cancelled"}:
        raise HTTPException(409, "Tác vụ ERP đã kết thúc; BE cần tạo lượt xử lý mới")
    return row


async def progress(repo, task_id, value):
    table = repo.table("Tasks")
    await repo.conn.execute(update(table).where(repo.scope(table), table.c.Id == task_id,
        table.c.UserId == repo.user_id).values(ProgressPercentage=value))


async def append_version(repo, task_id, result, url, inputs):
    from app.services.ai_outputs import public_result
    await repo.lock(f"erp-task:{task_id}", shared=True)
    table = repo.table("OutputVersions")
    version = (await repo.conn.execute(select(func.max(table.c.VersionNo)).where(repo.scope(table),
        table.c.OwnerType == "Task", table.c.OwnerId == task_id))).scalar() or 0
    await repo.add("OutputVersions", OwnerType="Task", OwnerId=task_id, VersionNo=version+1,
        ContentJson=dumps(public_result(result)), FileUrl=url, ChangeNote="Kết quả AI cho tác vụ ERP")
    if inputs.get("source_url"):
        await repo.add("SourceReferences", OwnerType="Task", OwnerId=task_id,
                       ExternalSourceRef=inputs["source_url"])
    for ref in result.get("refs", []) or result.get("all_refs", []):
        if not isinstance(ref, dict):
            continue
        doc = await repo.document(str(ref.get("doc_id") or ""))
        if doc:
            await repo.add("SourceReferences", OwnerType="Task", OwnerId=task_id,
                DocumentId=doc["Id"], DocumentVersionNo=doc["VersionNo"],
                Snippet=ref.get("text") or ref.get("quote"), PageNumber=ref.get("page"))


async def run(function, kind, task_id, args, kwargs):
    """Không lease/worker, không ghi InputJson hoặc các trường BE sở hữu."""
    from app.services.ai_outputs import file_url
    inputs = dict(inspect.signature(function).bind(*args, **kwargs).arguments)
    async with repository() as repo:
        await require_task(repo, task_id, kind)
        await progress(repo, task_id, 1)
    token = current_task_id.set(task_id)
    try:
        source = inputs.get("file_path")
        if not source and inputs.get("file_id"):
            from app.services.storage import resolve
            source = str(resolve(inputs["file_id"], kinds=("upload",)).path)
        if source:
            meta = await anyio.to_thread.run_sync(lambda: ObjectStorage().upload(Path(source), Path(source).name))
            inputs["source_url"] = file_url(meta)
        result = await function(*args, **kwargs)
        # Ghi nhận mẫu thực tế ở phiên bản, không sửa TemplateId của tác vụ ERP.
        template = result.get("template")
        if isinstance(template, dict):
            result.setdefault("template_name", template.get("ten_bao_cao", ""))
        path = Path(result["output_path"]) if result.get("output_path") else None
        url = None
        if path and not result.get("error") and result.get("validation", {}).get("status") != "failed":
            from app.services.storage import resolve_output
            path = resolve_output(str(path)).path
            meta = await anyio.to_thread.run_sync(lambda: ObjectStorage().upload(path, path.name))
            url = file_url(meta)
        result.update(ai_task_id=task_id, output_file_url=url or "")
        async with repository() as repo:
            await require_task(repo, task_id, kind)
            await append_version(repo, task_id, result, url, inputs)
            await progress(repo, task_id, 100)
        return result
    finally:
        current_task_id.reset(token)

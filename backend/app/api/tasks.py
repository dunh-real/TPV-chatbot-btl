"""API UC37: tác vụ, kết quả/phiên bản, chạy lại, xóa đầu ra và thông báo riêng."""
import mimetypes
from datetime import datetime, timezone
from urllib.parse import quote

import anyio
from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.db.ai_session import repository
from app.schemas.tasks import TaskCreate, TaskKind, TaskStatus, TaskNotify
from app.services import tasks, task_queue
from app.services.object_storage import ObjectStorage, parse_reference

router = APIRouter(prefix="/api/tasks", tags=["tasks-uc37"])


def utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value and value.tzinfo else value


@router.get("")
async def list_tasks(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                     status: TaskStatus | None = None, kind: TaskKind | None = None,
                     since: datetime | None = None, until: datetime | None = None):
    if since and until and utc(since) > utc(until):
        raise HTTPException(422, "Khoảng thời gian không hợp lệ")
    async with repository() as repo:
        return await tasks.list_tasks(repo, page, page_size, status, kind, utc(since), utc(until))


@router.post("", status_code=202)
async def create_task(request: TaskCreate):
    try:
        async with repository() as repo:
            task_id = await task_queue.submit(repo, request.kind, request.parameters, request.notify_on_finish)
            return {"task_id": task_id, "status": "Queued", "detail_url": f"/api/tasks/{task_id}"}
    except ValidationError as exc:
        raise HTTPException(422, "Tham số không hợp lệ cho loại tác vụ") from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(422, "Không đọc được file nguồn hoặc tham số tác vụ") from exc


@router.get("/notifications")
async def notifications(limit: int = Query(30, ge=1, le=100)):
    async with repository() as repo:
        return {"items": await tasks.notifications(repo, limit)}


@router.patch("/notifications/{task_id}/read", status_code=204)
async def mark_read(task_id: int):
    async with repository() as repo:
        await tasks.mark_read(repo, task_id)
    return Response(status_code=204)


@router.get("/{task_id}")
async def detail(task_id: int):
    async with repository() as repo:
        return await tasks.detail(repo, task_id)


@router.post("/{task_id}/retry", status_code=202)
async def retry(task_id: int):
    async with repository() as repo:
        new_id = await task_queue.retry(repo, task_id)
        return {"task_id": new_id, "retry_of_task_id": task_id, "status": "Queued"}


@router.patch("/{task_id}/notify")
async def notify(task_id: int, request: TaskNotify):
    async with repository() as repo:
        row = await tasks.get_task(repo, task_id, locked=True)
        if row["UserId"] != repo.user_id:
            raise HTTPException(403, "Chỉ chủ tác vụ được đổi thiết lập thông báo")
        await repo.patch("Tasks", task_id, NotifyOnFinish=request.enabled)
        await tasks.audit(repo, task_id, "TaskNotifyChanged")
    return {"notify_on_finish": request.enabled}


@router.delete("/{task_id}/output", status_code=204)
async def delete_output(task_id: int):
    async with repository() as repo:
        await tasks.delete_output(repo, task_id)
    return Response(status_code=204)


async def stream(reference, filename):
    from app.api.documents import _OriginalResponse
    try:
        bucket, key = parse_reference(reference)
        content = await anyio.to_thread.run_sync(lambda: ObjectStorage().open(bucket, key))
    except Exception as exc:
        raise HTTPException(502, "Không đọc được file trên kho lưu trữ") from exc
    return _OriginalResponse(content, media_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
                 "Cache-Control": "private, no-store"})


@router.get("/{task_id}/result")
async def result(task_id: int, version_id: int | None = None):
    async with repository() as repo:
        row = await tasks.get_task(repo, task_id)
        if tasks.metadata(row).get("output_deleted"):
            raise HTTPException(404, "Đầu ra đã được xóa")
        if row["Status"] != "Completed":
            raise HTTPException(409, "Tác vụ chưa có kết quả hoàn thành")
        reference, content = row["OutputFileUrl"], row["ResultJson"]
        if version_id is not None:
            owner, owner_id = await tasks.output_owner(repo, row)
            table = repo.table("OutputVersions")
            versions = await repo.rows("OutputVersions", table.c.Id == version_id,
                table.c.OwnerType == owner,
                table.c.OwnerId == owner_id)
            if not versions:
                raise HTTPException(404, "Không tìm thấy phiên bản của tác vụ")
            reference, content = versions[0]["FileUrl"], versions[0]["ContentJson"]
    if reference:
        return await stream(reference, row["TextSearch"] or f"task-{task_id}.bin")
    return JSONResponse(tasks.clean(tasks.load(content)), headers={
        "Content-Disposition": f'attachment; filename="task-{task_id}.json"', "Cache-Control": "private, no-store"})


@router.get("/{task_id}/source")
async def source(task_id: int):
    async with repository() as repo:
        row = await tasks.get_task(repo, task_id)
        reference = tasks.payload(row).get("source_url")
        if not reference:
            owner, owner_id = await tasks.output_owner(repo, row)
            refs = repo.table("SourceReferences")
            sources = await repo.rows("SourceReferences", refs.c.OwnerType == owner, refs.c.OwnerId == owner_id)
            reference = next((s["ExternalSourceRef"] for s in sources if s["ExternalSourceRef"]), None)
        if not reference:
            raise HTTPException(404, "Tác vụ không có file nguồn được lưu bền")
        name = tasks.payload(row).get("source_name") or tasks.payload(row).get("file_name") or "source.bin"
    return await stream(reference, name)


@router.get("/{task_id}/template")
async def template(task_id: int):
    async with repository() as repo:
        row = await tasks.get_task(repo, task_id)
        if tasks.metadata(row).get("output_deleted"):
            raise HTTPException(404, "Đầu ra đã được xóa")
        data = await tasks.latest_output(repo, row)
        if not isinstance(data, dict) or not data.get("template_id") or not data.get("template_version"):
            raise HTTPException(404, "Không có phiên bản mẫu được ghi nhận")
        table = repo.table("TemplateVersions")
        versions = await repo.rows("TemplateVersions", table.c.TemplateId == data["template_id"],
                                   table.c.VersionNo == data["template_version"])
        if not versions:
            raise HTTPException(404, "Không tìm thấy phiên bản mẫu đã sử dụng")
    return await stream(versions[0]["FileUrl"], versions[0]["FileName"])

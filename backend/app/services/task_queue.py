"""Hàng đợi MSSQL UC37; khôi phục nguồn MinIO và chạy với quyền hiện hành."""
import asyncio
import json
import logging
import tempfile
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import anyio
from fastapi import HTTPException
from sqlalchemy import case, func, insert, select, update

from app.core.config import get_settings
from app.core.context import Principal, use_principal
from app.db import ai_session
from app.db.ai_repository import dumps, now, lease_now
from app.services import tasks
from app.services.object_storage import ObjectStorage, parse_reference

logger = logging.getLogger(__name__)


class LeaseLost(RuntimeError):
    """Không cho tiến trình cũ ghi kết quả sau khi tác vụ đã được thu hồi."""


async def fence(repo, task_id, token):
    row = await tasks.get_task(repo, task_id, locked=True, internal=True)
    if row["Status"] != "Processing" or tasks.metadata(row).get("lease_token") != token:
        raise LeaseLost("Lần chạy không còn sở hữu tác vụ")
    return row


async def source_file(file_id):
    from app.services import storage
    ref = storage.resolve(file_id, kinds=("upload",))
    meta = await anyio.to_thread.run_sync(lambda: ObjectStorage().upload(ref.path, ref.name))
    return f"minio://{meta['storage_bucket']}/{meta['storage_object_key']}", ref.name


async def submit(repo, kind, parameters, notify=False, retry_of=None):
    tasks.require_access(repo)
    tasks.require(repo, kind)
    params = dict(parameters)
    source_url, source_name = None, None
    if kind in {"Review", "Summarize", "TaskExtract"}:
        from app.schemas.tasks import ReviewParameters
        from app.schemas.summaries import SummaryRequest
        data = (SummaryRequest if kind=="Summarize" else ReviewParameters).model_validate(params)
        source_url, source_name = await source_file(data.file_id)
        params = dict(file_name=source_name, **data.model_dump(exclude={"file_id"}))
    else:
        from app.schemas.reports import DraftRequest, AggregateRequest
        from app.schemas.presentations import PresentationRequest
        schema = {"Draft": DraftRequest, "Aggregate": AggregateRequest, "Slide": PresentationRequest}.get(kind)
        if schema is None:
            raise HTTPException(422, "Loại tác vụ không hỗ trợ tạo mới")
        params = schema.model_validate(params).model_dump()
        if kind in {"Draft", "Slide"} and params.get("nguon") == "tai_lieu":
            source_url, source_name = await source_file(params["file_id"])
            params["file_id"] = ""
    if source_url:
        params.update(source_url=source_url, source_name=source_name)
    number = " ".join(str((params.get("inputs") or {}).get("so_ky_hieu") or "").split())
    if number:
        from app.db.ai_session import DocumentNumberConflict
        params["inputs"]["so_ky_hieu"] = number
        await repo.lock("number:" + number, shared=True)
        if await repo.number_exists(number):
            raise DocumentNumberConflict("Số văn bản đã có trong sổ MSSQL")
    params["_uc37"] = {"managed": True, "origin": "queued"}
    return await repo.add("Tasks", UserId=repo.user_id, TaskType=kind, AgentCode=tasks.OPERATIONS[kind],
        Status="Queued", ProgressPercentage=0, InputJson=dumps(params), NotifyOnFinish=notify, RetryOfTaskId=retry_of)


async def retry(repo, task_id):
    row = await tasks.get_task(repo, task_id, locked=True)
    tasks.require(repo, row["TaskType"])
    if not tasks.summary(row)["can_retry"]:
        raise HTTPException(409, "Chỉ chạy lại tác vụ lỗi có đủ tham số được hỗ trợ")
    table = repo.table("Tasks")
    active = await repo.rows("Tasks", table.c.RetryOfTaskId == task_id,
                             table.c.Status.in_(["Queued", "Processing"]), owned=True)
    if active:
        return active[0]["Id"]
    args = tasks.payload(row)
    needs_source = row["TaskType"] in {"Review", "Summarize", "TaskExtract"} or (
        row["TaskType"] in {"Draft", "Slide"} and args.get("nguon") == "tai_lieu")
    if needs_source and not args.get("source_url"):
        if not args.get("file_id"):
            raise HTTPException(409, "Tác vụ cũ không còn file nguồn để chạy lại")
        args["source_url"], args["source_name"] = await source_file(args["file_id"])
        args.pop("file_id", None)
    # Không nhận code/path thực thi mới từ client; dùng operation đã được backend cho phép.
    args["_uc37"] = {"managed": True, "origin": "queued"}
    number = " ".join(str((args.get("inputs") or {}).get("so_ky_hieu") or "").split())
    if number:
        from app.db.ai_session import DocumentNumberConflict
        await repo.lock("number:" + number, shared=True)
        if await repo.number_exists(number):
            raise DocumentNumberConflict("Số văn bản đang được sử dụng")
    new_id = await repo.add("Tasks", UserId=repo.user_id, TaskType=row["TaskType"], AgentCode=row["AgentCode"],
        Status="Queued", ProgressPercentage=0, InputJson=dumps(args), NotifyOnFinish=row["NotifyOnFinish"],
        RetryOfTaskId=task_id)
    await tasks.audit(repo, task_id, "TaskRetryRequested")
    return new_id


def json_field(column, path):
    return func.json_value(case((func.isjson(column) == 1, column), else_="{}"), path)


async def claim(task_id=None):
    mapped = await ai_session.tables()
    table = mapped["AI_Tasks"]
    async with ai_session.get_engine().begin() as conn:
        query = select(table).where(table.c.IsDeleted == False, table.c.Status == "Queued",
            json_field(table.c.InputJson, "$._uc37.managed") == "true").order_by(table.c.Id).limit(1)
        query = query.where(table.c.Id == task_id) if task_id is not None else query.where(
            json_field(table.c.InputJson, "$._uc37.origin") == "queued")
        query = query.with_hint(table, "WITH (UPDLOCK, READPAST, READCOMMITTEDLOCK)", dialect_name="mssql")
        found = (await conn.execute(query)).mappings().first()
        if found is None:
            return None
        row = dict(found)
        data = tasks.payload(row)
        token = uuid.uuid4().hex
        data["_uc37"].update(lease_token=token, lease_until=tasks.deadline())
        await conn.execute(update(table).where(table.c.Id == row["Id"]).values(Status="Processing",
            StartTime=now(), LastModificationTime=now(), ProgressPercentage=1, InputJson=dumps(data)))
        return {**row, "Status": "Processing", "InputJson": dumps(data)}


async def recover(task_id=None):
    """Lần chạy bị ngắt thành Failed; không tự lặp tác vụ có thể đã có tác dụng phụ."""
    mapped = await ai_session.tables()
    table = mapped["AI_Tasks"]
    async with ai_session.get_engine().begin() as conn:
        query = select(table).where(table.c.IsDeleted == False, table.c.Status == "Processing",
            json_field(table.c.InputJson, "$._uc37.managed") == "true",
            json_field(table.c.InputJson, "$._uc37.lease_until") < lease_now().isoformat(timespec="seconds"))
        query = query.where(table.c.Id == task_id) if task_id is not None else query.where(
            json_field(table.c.InputJson, "$._uc37.origin").in_(["inline", "queued"]))
        rows = (await conn.execute(query.with_hint(table, "WITH (UPDLOCK, READPAST, READCOMMITTEDLOCK)", dialect_name="mssql"))).mappings().all()
        for row in rows:
            await conn.execute(update(table).where(table.c.Id == row["Id"]).values(Status="Failed", EndTime=now(),
                ErrorMessage="Tiến trình xử lý bị ngắt hoặc mất lease; có thể yêu cầu chạy lại"))
            await conn.execute(insert(mapped["AI_ExecutionLogs"]).values(TenantId=row["TenantId"], UserId=row["UserId"],
                TaskId=row["Id"], EventType="TaskInterrupted", AgentCode="task_worker", Status="Failed",
                HasCitations=False, NoEvidence=False, CreationTime=now(), IsDeleted=False))


def runner(operation):
    from app.agents import graph
    from app.tools.document import generate_docx
    from app.tools.presentation import generate_presentation
    functions = {name: getattr(graph, name) for name in set(tasks.OPERATIONS.values())}
    functions.update(generate_docx=generate_docx, generate_presentation=generate_presentation)
    return functions.get(operation)


async def execute(row):
    from app.services.access import resolve_access
    access = await resolve_access(str(row["UserId"]))
    if access is None or access.tenant_id != row["TenantId"]:
        raise PermissionError("Không còn tài khoản hợp lệ để thực hiện tác vụ")
    principal = Principal(access.tenant_id, str(access.user_id), "default", access)
    with use_principal(principal):
        async with ai_session.repository() as repo:
            tasks.require(repo, row["TaskType"])
        fn = runner(row["AgentCode"])
        if fn is None:
            raise ValueError("Operation không nằm trong danh sách cho phép")
        args = tasks.payload(row)
        args.pop("_uc37", None)
        source = args.pop("source_url", None)
        filename = args.pop("source_name", None) or args.get("file_name") or "source.docx"
        args.pop("file_path", None)
        if row["TaskType"] in {"Review", "Summarize", "TaskExtract"}:
            args.pop("file_id", None)
        base = Path(get_settings().upload_dir)
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="uc37-", dir=base) as directory:
            if source:
                bucket, key = parse_reference(source)
                path = Path(directory) / ("source" + Path(filename).suffix)
                await anyio.to_thread.run_sync(lambda: ObjectStorage().download(bucket, key, path, filename))
                if row["TaskType"] in {"Review", "Summarize", "TaskExtract"}:
                    args["file_path"] = str(path.resolve())
                else:
                    args["file_id"] = str(path.resolve())
            token = ai_session.execution_task.set(row)
            try:
                await fn(**args)
            finally:
                ai_session.execution_task.reset(token)


async def fail(row, message):
    mapped = await ai_session.tables()
    table = mapped["AI_Tasks"]
    async with ai_session.get_engine().begin() as conn:
        result = await conn.execute(update(table).where(table.c.Id == row["Id"], table.c.Status == "Processing",
            json_field(table.c.InputJson, "$._uc37.lease_token") == tasks.metadata(row)["lease_token"])
            .values(Status="Failed", EndTime=now(), ErrorMessage=message))
        if result.rowcount:
            await conn.execute(insert(mapped["AI_ExecutionLogs"]).values(TenantId=row["TenantId"], UserId=row["UserId"],
                TaskId=row["Id"], EventType="TaskFailed", AgentCode="task_worker", Status="Failed",
                ErrorMessage=message, HasCitations=False, NoEvidence=False, CreationTime=now(), IsDeleted=False))


async def serve():
    while True:
        try:
            await recover()
            row = await claim()
            if row is None:
                await asyncio.sleep(get_settings().task_worker_poll_seconds)
                continue
            try:
                # Heartbeat bắt đầu trước cả tải nguồn và xác minh lại quyền.
                async with lease(row):
                    await asyncio.wait_for(execute(row), get_settings().task_execution_timeout_seconds)
            except asyncio.CancelledError:
                await asyncio.shield(fail(row, "Worker dừng khi đang xử lý"))
                raise
            except Exception as exc:
                logger.exception("Tác vụ %s thất bại", row["Id"])
                await fail(row, "Không thực hiện được tác vụ: " + type(exc).__name__)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Worker UC37 không truy cập được hàng đợi")
            await asyncio.sleep(max(5, get_settings().task_worker_poll_seconds))


@asynccontextmanager
async def lease(row):
    """Gia hạn bằng JSON_MODIFY để không ghi đè thiết lập thông báo đồng thời."""
    parent = asyncio.current_task()
    failures = []
    async def heartbeat():
        try:
            while True:
                await asyncio.sleep(max(1, get_settings().task_lease_seconds // 3))
                mapped = await ai_session.tables()
                table = mapped["AI_Tasks"]
                async with ai_session.get_engine().begin() as conn:
                    result = await conn.execute(update(table).where(table.c.Id == row["Id"],
                        table.c.Status == "Processing", json_field(table.c.InputJson, "$._uc37.lease_token") ==
                        tasks.metadata(row)["lease_token"]).values(LastModificationTime=now(),
                            InputJson=func.json_modify(table.c.InputJson, "$._uc37.lease_until", tasks.deadline())))
                    if result.rowcount != 1:
                        raise RuntimeError("Mất lease tác vụ")
        except Exception as exc:
            failures.append(exc)
            logger.exception("Không gia hạn được tác vụ %s", row["Id"])
            parent.cancel()
    pulse = asyncio.create_task(heartbeat())
    try:
        try:
            yield
        except asyncio.CancelledError:
            if failures:
                parent.uncancel()
                raise LeaseLost("Không còn duy trì được lease tác vụ") from failures[0]
            raise
    finally:
        pulse.cancel()
        with suppress(asyncio.CancelledError):
            await pulse

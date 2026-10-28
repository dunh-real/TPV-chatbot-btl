"""Lưu tác vụ và sản phẩm workflow vào SQL; file bền trên MinIO."""
import inspect
import time
from functools import wraps
from pathlib import Path

import anyio

from app.db.ai_repository import dumps, now
from app.db.ai_session import repository, current_task_id, DocumentNumberConflict
from app.services.object_storage import ObjectStorage


def file_url(meta):
    return f"minio://{meta['storage_bucket']}/{meta['storage_object_key']}"


def public_result(result):
    # Đường dẫn cục bộ chỉ dùng trong tiến trình; DB chung lưu tham chiếu file.
    return {k: v for k, v in result.items() if k not in {"output_path", "file_path"}}


async def save_product(repo, kind, inputs, result, url):
    title = str(inputs.get("request") or inputs.get("file_name") or "Sản phẩm AI")[:500]
    content = dumps(public_result(result))
    if kind == "File":
        row_id = current_task_id.get()
        await repo.add("OutputVersions", OwnerType="Task", OwnerId=row_id, VersionNo=1,
                       ContentJson=content, FileUrl=url, ChangeNote="Xuất file nghiệp vụ")
        return "Tasks", row_id
    if kind == "Review":
        name = "DocumentReviews"
        values = dict(UserId=repo.user_id, FileName=Path(inputs.get("file_name") or inputs["file_path"]).name,
            FileUrl=inputs.get("source_url"), ResultJson=content, IssuesJson=dumps(result.get("findings", [])),
            IssueCount=len(result.get("findings", [])), AcceptedCount=0, IgnoredCount=0)
    elif kind == "Draft":
        name = "DocumentDrafts"
        if not result.get("template_id"):
            raise ValueError("Đầu ra cần mẫu SQL đã công bố để lưu AI_DocumentDrafts")
        values = dict(UserId=repo.user_id, Title=title, TemplateId=result["template_id"],
            TemplateVersionNo=result["template_version"], Request=inputs["request"][:4000],
            SectionsJson=dumps(result.get("sections", [])), Status="Completed", VersionNo=1)
    elif kind == "Aggregate":
        name = "ReportSummaries"
        values = dict(Title=title, Status="Completed", ResultSummaryJson=content,
            ResultFileUrl=url, TemplateId=result.get("template_id"), VersionNo=1,
            HasDiscrepancy=result.get("has_discrepancy", False), StrictNumbers=True)
    else:
        name = "Slides"
        values = dict(Title=title, Status="Completed", OutlineJson=content, FileUrl=url, VersionNo=1)
    row_id = await repo.add(name, **values)
    await repo.add("OutputVersions", OwnerType=kind, OwnerId=row_id, VersionNo=1,
                   ContentJson=content, FileUrl=url, ChangeNote="Tạo bởi backend AI")
    if inputs.get("source_url"):
        await repo.add("SourceReferences", OwnerType=kind, OwnerId=row_id,
                       ExternalSourceRef=inputs["source_url"])
    for ref in result.get("refs", []) or result.get("all_refs", []):
        if not isinstance(ref, dict):
            continue
        doc = await repo.document(str(ref.get("doc_id") or ""))
        if doc:
            await repo.add("SourceReferences", OwnerType=kind, OwnerId=row_id, DocumentId=doc["Id"],
                DocumentVersionNo=doc["VersionNo"], Snippet=ref.get("text") or ref.get("quote"),
                PageNumber=ref.get("page"))
    return name, row_id


def tracked_workflow(kind):
    def decorate(function):
        @wraps(function)
        async def run(*args, **kwargs):
            bound = inspect.signature(function).bind(*args, **kwargs)
            inputs = dict(bound.arguments)
            for key in ("history", "images", "charts"):
                inputs.pop(key, None)
            input_meta = inputs.get("inputs") or (inputs.get("content") or {}).get("meta") or {}
            number = " ".join(str(input_meta.get("so_ky_hieu") or "").split())
            if number:
                inputs["inputs"] = {**input_meta, "so_ky_hieu": number}
            async with repository() as repo:
                if number:
                    await repo.lock("number:" + number, shared=True)
                    if await repo.number_exists(number):
                        raise DocumentNumberConflict("Số văn bản đã có trong sổ MSSQL")
                task_id = await repo.add("Tasks", UserId=repo.user_id, TaskType=kind,
                    AgentCode=function.__name__, Status="Processing", ProgressPercentage=0,
                    InputJson=dumps({k: v for k, v in inputs.items() if k != "file_path"}),
                    NotifyOnFinish=False, StartTime=now())
            token = current_task_id.set(task_id)
            try:
                # File nguồn của soát tài liệu cũng được lưu bền.
                source = inputs.get("file_path")
                if not source and inputs.get("file_id"):
                    from app.services.storage import resolve
                    source = str(resolve(inputs["file_id"], kinds=("upload",)).path)
                if source:
                    meta = await anyio.to_thread.run_sync(lambda: ObjectStorage().upload(Path(source), Path(source).name))
                    inputs["source_url"] = file_url(meta)
                started = time.perf_counter()
                result = await function(*args, **kwargs)
                from app.documents.the_thuc import doc_ngay_ban_hanh
                result["ngay_van_ban"] = doc_ngay_ban_hanh(input_meta).isoformat()
                failed = result.get("error") or result.get("validation", {}).get("status") == "failed"
                waiting = bool(result.get("missing_input"))
                path = Path(result["output_path"]) if result.get("output_path") else None
                url = None
                if path and not failed and not waiting:
                    from app.services.storage import resolve_output
                    path = resolve_output(str(path)).path
                    meta = await anyio.to_thread.run_sync(lambda: ObjectStorage().upload(path, path.name))
                    url = file_url(meta)
                async with repository() as repo:
                    related = {}
                    if not failed and not waiting and (kind == "Review" or url):
                        name, row_id = await save_product(repo, kind, inputs, result, url)
                        related = dict(RelatedEntityType="AI_" + name, RelatedEntityId=row_id)
                    await repo.patch("Tasks", task_id, Status="Failed" if failed else "Cancelled" if waiting else "Completed",
                        ProgressPercentage=100, EndTime=now(), ResultJson=dumps(public_result(result)),
                        InputJson=dumps({k: v for k, v in inputs.items() if k != "file_path"}),
                        OutputFileUrl=url, TextSearch=path.name if path else None,
                        ErrorMessage=str(failed) if failed else None, **related)
                    await repo.add("ExecutionLogs", UserId=repo.user_id, EventType=kind,
                        TaskId=task_id, AgentCode=function.__name__, Status="Failed" if failed else "Completed",
                        DurationMs=int((time.perf_counter()-started)*1000),
                        HasCitations=bool(result.get("refs") or result.get("all_refs")), NoEvidence=False,
                        OutputFileUrl=url, UserQuery=str(inputs.get("request") or "")[:4000])
                return {**result, "ai_task_id": task_id,
                        "registered_as": f"{name}:{row_id}" if related else result.get("registered_as", "")}
            except BaseException as exc:
                with anyio.CancelScope(shield=True):
                    async with repository() as repo:
                        await repo.patch("Tasks", task_id, Status="Failed", EndTime=now(),
                                         ErrorMessage=type(exc).__name__)
                        await repo.add("ExecutionLogs", UserId=repo.user_id, EventType=kind,
                            TaskId=task_id, Status="Failed", HasCitations=False, NoEvidence=False,
                            ErrorMessage=type(exc).__name__)
                raise
            finally:
                current_task_id.reset(token)
        return run
    return decorate


async def register_file(path):
    """Đăng ký file do endpoint dựng trực tiếp vào MSSQL và MinIO."""
    @tracked_workflow("File")
    async def exported():
        return {"output_path": str(path)}
    return await exported()

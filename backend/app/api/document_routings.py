"""UC33: AI gợi ý, ERP quản lý xác nhận và chuyển đơn vị xử lý."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import anyio
from sqlalchemy import select, func
from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile

from app.agents.quyen import can_quyen
from app.db.ai_session import repository
from app.db import routing_repository as db
from app.rag.converter import DocumentConverter
from app.schemas.routing import RoutingRequest, RoutingResponse
from app.services import document_routing as service
from app.services.object_storage import ObjectStorage, parse_reference

router = APIRouter(prefix="/api/document-routings", tags=["Phân luồng văn bản (UC33)"],
                   dependencies=[Depends(can_quyen("Ai.Routing", "gợi ý phân luồng văn bản"))])


async def read_file(path):
    if path.suffix.lower() not in {".docx", ".pdf", ".txt", ".md"}:
        raise HTTPException(415, "Chỉ hỗ trợ DOCX, PDF, TXT, MD")
    if path.stat().st_size > 20 * 1024**2:
        raise HTTPException(413, "File vượt 20 MB")
    loaded = await anyio.to_thread.run_sync(lambda: DocumentConverter().convert(path))
    if not loaded.text.strip() or len(loaded.text) > 60000:
        raise HTTPException(422, "Nội dung rỗng hoặc vượt 60.000 ký tự; hãy chia nhỏ tài liệu")
    return loaded.text


async def source(request, task_id):
    """Không đọc đường dẫn/URL tùy ý; MinIO phải khớp tài liệu/tác vụ được phép."""
    async with repository() as repo:
        task = await db.erp_task(repo, task_id) if task_id else None
        doc = await repo.document(request.document_id, include_unready=True) if request.document_id else None
        if request.document_id and not doc:
            raise HTTPException(404, "Không được đọc tài liệu nguồn")
        if doc and not repo.principal.can("Ai.AiChatbot"):
            raise HTTPException(403, "Thiếu quyền đọc kho tài liệu")
        url = request.file_url or (doc["FileUrl"] if doc else "")
        if url:
            args = json.loads(task["InputJson"] or "{}") if task else {}
            allowed = {doc["FileUrl"]} if doc else set()
            allowed.update(args.get(k) for k in ("source_url", "FileUrl", "file_url"))
            if url not in allowed:
                raise HTTPException(403, "FileUrl phải khớp nguồn trên tài liệu hoặc tác vụ ERP")
        elif request.extracted_text.strip():
            return request.extracted_text.strip(), ""
        else:
            raise HTTPException(422, "Cần ExtractedText hoặc nguồn file đã đăng ký")
    if request.extracted_text.strip():
        raise HTTPException(422, "Chỉ gửi một nguồn: văn bản hoặc file")
    try:
        bucket, key = parse_reference(url)
        store = ObjectStorage()
        store.validate_bucket(bucket)
        with TemporaryDirectory(prefix="uc33-") as temp:
            path = Path(temp) / ("source" + Path(key).suffix.lower())
            def download():
                stream = store.open(bucket, key)
                try:
                    size = 0
                    with path.open("wb") as target:
                        for chunk in stream.stream(1024 * 1024):
                            size += len(chunk)
                            if size > 20 * 1024**2:
                                raise HTTPException(413, "File vượt 20 MB")
                            target.write(chunk)
                finally:
                    stream.close()
                    stream.release_conn()
            await anyio.to_thread.run_sync(download)
            return await read_file(path), url
    except (ValueError, PermissionError) as exc:
        raise HTTPException(422, "Tham chiếu MinIO không hợp lệ hoặc bucket chưa được cấp phép") from exc


async def run(request, text, source_url, task_id, source_name=""):
    departments, categories = await db.catalogs()
    mapped = await service.suggest(text, departments, categories)
    result = service.response(mapped, document_id=request.document_id, task_id=task_id,
                              departments=departments, categories=categories)
    async with repository() as repo:
        if task_id:
            await db.erp_task(repo, task_id)
            # BE sở hữu trạng thái, sản phẩm và quyết định cuối; AI chỉ lưu phiên bản gợi ý.
            await repo.lock(f"routing:{task_id}", shared=True)
            versions = repo.table("OutputVersions")
            version = (await repo.conn.execute(select(func.max(versions.c.VersionNo)).where(
                repo.scope(versions), versions.c.OwnerType == "Task", versions.c.OwnerId == task_id))).scalar() or 0
            await repo.add("OutputVersions", OwnerType="Task", OwnerId=task_id, VersionNo=version + 1,
                ContentJson=json.dumps(result, ensure_ascii=False), ChangeNote="Gợi ý UC33",
                CreationTime=db.vietnam_now())
        else:
            result = await db.save(repo, result, dict(extracted_text=text, source_url=source_url,
                source_name=source_name, document_id=request.document_id))
        if source_url:
            await repo.add("SourceReferences", OwnerType="Task" if task_id else "Routing",
                           OwnerId=task_id or int(result["RoutingId"]),
                           ExternalSourceRef=source_url, CreationTime=db.vietnam_now())
    return result


@router.post("/suggest", response_model=RoutingResponse, summary="Gợi ý đơn vị xử lý từ văn bản hoặc nguồn ERP")
async def suggest(request: RoutingRequest, task_id: int | None = Header(None, alias="X-Erp-Task-Id", gt=0)):
    if request.departments or request.categories:
        raise HTTPException(422, "Danh mục lấy từ ERP; không nhận danh mục tùy ý")
    text, url = await source(request, task_id)
    return await run(request, text, url, task_id)


@router.post("/suggest-file", response_model=RoutingResponse, summary="Đính kèm file để gợi ý, không nạp kho tri thức")
async def suggest_file(file: UploadFile = File(...),
                       task_id: int | None = Header(None, alias="X-Erp-Task-Id", gt=0)):
    if task_id:
        async with repository() as repo:
            await db.erp_task(repo, task_id)
    try:
        with TemporaryDirectory(prefix="uc33-") as temp:
            name = Path(file.filename or "source").name
            path = Path(temp) / ("source" + Path(name).suffix.lower())
            size = 0
            with path.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > 20 * 1024**2:
                        raise HTTPException(413, "File vượt 20 MB")
                    output.write(chunk)
            text = await read_file(path)
            meta = await anyio.to_thread.run_sync(lambda: ObjectStorage().upload(path, name))
            url = f"minio://{meta['storage_bucket']}/{meta['storage_object_key']}"
            return await run(RoutingRequest(), text, url, task_id, name)
    finally:
        await file.close()


@router.get("/{routing_id}", response_model=RoutingResponse)
async def detail(routing_id: int):
    async with repository() as repo:
        return await db.load(repo, routing_id)


@router.post("/{routing_id}/rerun", response_model=RoutingResponse)
async def rerun(routing_id: int):
    async with repository() as repo:
        result = await db.load(repo, routing_id)
        task = await db.erp_task(repo, result["ai_task_id"])
        inputs = json.loads(task["InputJson"])
    request = RoutingRequest(document_id=inputs.get("document_id", ""))
    text, url = inputs["extracted_text"], inputs.get("source_url", "")
    if request.document_id:
        text, url = await source(RoutingRequest(document_id=request.document_id), None)
    return await run(request, text, url, None,
                     inputs.get("source_name", ""))

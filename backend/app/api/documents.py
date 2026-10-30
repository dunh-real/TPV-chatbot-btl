"""API nạp và quản lý tài liệu trong kho tri thức (nguồn dữ liệu cho workflow 1)."""

from __future__ import annotations

import logging
import time
import shutil
import tempfile
import mimetypes

import anyio
from dataclasses import asdict
from pathlib import Path

from urllib.parse import quote

from fastapi import Depends, APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse, Response
from minio.error import S3Error

from app.api.files import output_file_response
from app.core.config import get_settings
from app.rag.ingestion import SUPPORTED_SUFFIXES, get_ingestion_pipeline, make_doc_id_file, make_legacy_doc_id_file
from app.rag.vectorstore import get_vector_store
from app.agents.graph import run_document_workflow
from app.schemas.summaries import SummaryRequest, SummaryResponse
from app.db.erp_repository import ErpDonViRepository
from app.db.erp_session import erp_session_scope
from app.documents.giao_viec import MetaGiaoViec, build_giao_viec
from app.documents.rules import available_rule_sets
from app.services import storage
from app.services import document_lists
from app.services.object_storage import ObjectStorage
from app.services.document_search import describe, search_documents, related_documents
from app.schemas.documents import (
    BatchDeleteRequest,
    BatchDeleteResponse,
    CollectionStats,
    DocumentItem,
    DocumentPage,
    GiaoViecRequest,
    GiaoViecResponse,
    IngestResponse,
    MinioIngestRequest,
    DocumentSearchRequest,
    DocumentSearchResponse,
    DocumentDetail,
    DocumentSelection,
    SaveDocumentList,
    SavedDocumentList,
    SavedDocumentListDetail,
    RelatedDocuments,
    IngestTextRequest,
    ReviewResponse,
    RuleSetInfo,
)

from app.agents import quyen

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])

_READ = [Depends(quyen.can_quyen(quyen.QUYEN_KHO_DOC, "đọc tài liệu trong kho"))]


async def _enrich(items):
    legal = await _legal_info([item.doc_id for item in items])
    for item in items:
        for key, value in legal.get(item.doc_id, {}).items():
            setattr(item, key, value)


async def _list_io(action, *args):
    try:
        return await anyio.to_thread.run_sync(lambda: action(*args))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Không tìm thấy danh mục")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Không được phép truy cập danh mục")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Danh mục hoặc định danh không hợp lệ") from exc
    except OSError:
        logger.exception("Không truy cập được danh mục đã lưu")
        raise HTTPException(status_code=503, detail="Không truy cập được danh mục đã lưu")


async def _details(doc_ids):
    try:
        metadata = await get_vector_store().document_metadata(doc_ids)
    except Exception:
        logger.exception("Không đọc được metadata danh mục")
        raise HTTPException(status_code=503, detail="Không truy cập được kho tài liệu")
    items = {key: describe(key, value) for key, value in metadata.items()}
    await _enrich(list(items.values()))
    return items


@router.post("/saved", response_model=SavedDocumentList, status_code=201, dependencies=_READ)
async def save_document_list(request: SaveDocumentList):
    return await _list_io(document_lists.save, request)


@router.get("/saved", response_model=list[SavedDocumentList], dependencies=_READ)
async def saved_document_lists():
    return await _list_io(document_lists.list_saved)


@router.get("/saved/{saved_id}", response_model=SavedDocumentListDetail, dependencies=_READ)
async def saved_document_list(saved_id: str):
    record = await _list_io(document_lists.load, saved_id)
    details = await _details(record.doc_ids)
    return SavedDocumentListDetail(**record.model_dump(),
        items=[details[key] for key in record.doc_ids if key in details],
        missing_doc_ids=[key for key in record.doc_ids if key not in details])


@router.delete("/saved/{saved_id}", status_code=204, dependencies=_READ)
async def delete_saved_document_list(saved_id: str):
    await _list_io(document_lists.delete, saved_id)
    return Response(status_code=204)


@router.post("/export", dependencies=_READ, response_class=Response,
             responses={200: {"content": {"text/csv": {}}}})
async def export_documents(request: DocumentSelection):
    details = await _details(request.doc_ids)
    content = await anyio.to_thread.run_sync(lambda: document_lists.export_csv(request.doc_ids, details))
    return Response(content, media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="documents.csv"', "Cache-Control": "private, no-store"})


@router.post("/search", response_model=DocumentSearchResponse, dependencies=_READ)
async def search(request: DocumentSearchRequest):
    try:
        result = await search_documents(request)
    except Exception:
        logger.exception("Tìm kiếm tài liệu thất bại")
        raise HTTPException(status_code=503, detail="Không truy cập được kho tìm kiếm")
    await _enrich(result.items)
    return result


async def _metadata(doc_id):
    try:
        metadata = (await get_vector_store().document_metadata([doc_id])).get(doc_id)
    except Exception:
        logger.exception("Đọc metadata tài liệu thất bại")
        raise HTTPException(status_code=503, detail="Không truy cập được kho tài liệu")
    if metadata is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu")
    return metadata


@router.get("/{doc_id}/detail", response_model=DocumentDetail, dependencies=_READ)
async def detail(doc_id: str):
    metadata = await _metadata(doc_id)
    legal = await _legal_info([doc_id])
    return describe(doc_id, metadata, legal.get(doc_id))


@router.get("/{doc_id}/related", response_model=RelatedDocuments, dependencies=_READ)
async def related(doc_id: str, limit: int = Query(default=5, ge=1, le=20)):
    metadata = await _metadata(doc_id)
    try:
        result = await related_documents(doc_id, metadata, limit)
    except Exception:
        logger.exception("Không tìm được tài liệu liên quan")
        raise HTTPException(status_code=503, detail="Không truy cập được kho tìm kiếm")
    await _enrich([*result.same_topic, *result.same_number, *result.legal_relations])
    return result


class _OriginalResponse(StreamingResponse):
    """Đóng MinIO kể cả ngắt kết nối trước khi đọc chunk đầu tiên."""
    def __init__(self, stream, **kwargs):
        self.stream = stream
        super().__init__(iter(lambda: stream.read(64 * 1024), b""), **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            def close():
                try:
                    self.stream.close()
                finally:
                    self.stream.release_conn()
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(close)


@router.get("/{doc_id}/original", dependencies=_READ)
async def original(doc_id: str, download: bool = False):
    metadata = await _metadata(doc_id)
    bucket, key = metadata.get("storage_bucket"), metadata.get("storage_object_key")
    if not bucket or not key:
        raise HTTPException(status_code=404, detail="Tài liệu chưa liên kết bản gốc MinIO")
    filename = storage.safe_name(metadata.get("original_filename") or metadata.get("source") or "document")
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    disposition = "inline" if media_type == "application/pdf" and not download else "attachment"
    try:
        stream = await anyio.to_thread.run_sync(lambda: ObjectStorage().open(bucket, key))
    except PermissionError:
        raise HTTPException(status_code=403, detail="Bucket nguồn chưa được phép đọc")
    except S3Error as exc:
        if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
            raise HTTPException(status_code=404, detail="Không tìm thấy file gốc trên MinIO")
        logger.exception("Đọc MinIO thất bại")
        raise HTTPException(status_code=502, detail="Không đọc được file gốc")
    except Exception:
        logger.exception("Đọc MinIO thất bại")
        raise HTTPException(status_code=502, detail="Không đọc được file gốc")
    return _OriginalResponse(stream, media_type=media_type, headers={
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(filename, safe='')}",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


async def _ingest_source(path, filename, metadata, doc_type, force):
    started = time.perf_counter()
    store = get_vector_store()
    await store.ensure_collection()
    from app.db.ai_session import erp_document
    if erp_document.get():
        # ERP sở hữu metadata; luôn thay index của đúng bản ghi/phiên bản.
        result = await get_ingestion_pipeline().ingest_file(path, source=filename, metadata=metadata, doc_type=doc_type)
        return IngestResponse(**asdict(result))
    doc_id = make_doc_id_file(filename, path)
    existing = await store.document_summary(doc_id)
    from app.db.ai_session import enabled, repository
    if existing is None and not enabled():
        existing = await store.document_summary(make_legacy_doc_id_file(filename, path))
    if existing is not None:
        try:
            await store.link_source(existing["doc_id"], metadata)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not force:
            if enabled():
                async with repository() as repo:
                    row_id = await repo.begin_document(existing["doc_id"], existing["doc_title"], metadata)
                    from app.db.ai_repository import now
                    await repo.patch("KnowledgeDocuments", row_id, IndexStatus="Indexed",
                        ProcessingStatus="Extracted", ChunkCount=existing["chunk_count"], LastIndexedTime=now())
            return IngestResponse(**existing, already_exists=True,
                                  elapsed_ms=(time.perf_counter() - started) * 1000)
        doc_id = existing["doc_id"]
    result = await get_ingestion_pipeline().ingest_file(
        path, doc_id=doc_id, source=filename, metadata=metadata, doc_type=doc_type)
    if result.chunk_count == 0:
        raise ValueError("Tài liệu không tạo được đoạn nội dung; file gốc vẫn được giữ trên MinIO")
    return IngestResponse(**asdict(result))


def _check_filename(filename):
    name = storage.safe_name(filename)
    if Path(name).suffix.lower() not in SUPPORTED_SUFFIXES:
        raise HTTPException(status_code=415, detail=f"Chỉ hỗ trợ: {', '.join(sorted(SUPPORTED_SUFFIXES))}")
    return name


@router.post("/upload", response_model=IngestResponse, summary="Lưu MinIO và nạp vào kho",
             dependencies=[Depends(quyen.can_quyen(quyen.QUYEN_KHO_GHI, "nạp tài liệu vào kho"))])
async def upload_document(
    file: UploadFile = File(...), doc_type: str = Form(default=""),
    force_reingest: bool = Form(default=False),
    erp_doc_id: int | None = Form(default=None, gt=0),
    version_no: int | None = Form(default=None, gt=0),
) -> IngestResponse:
    from app.db.ai_session import erp_document, repository
    token = None
    try:
        if (erp_doc_id is None) != (version_no is None):
            raise HTTPException(422, "Cần gửi đồng thời erp_doc_id và version_no")
        if erp_doc_id:
            async with repository() as repo:
                await repo.erp_document(erp_doc_id, version_no)
            token = erp_document.set((erp_doc_id, version_no))
        filename = _check_filename(file.filename or "")
        with tempfile.TemporaryDirectory(prefix="tpv-ingest-") as folder:
            path = Path(folder) / filename
            def copy():
                with path.open("wb") as target:
                    shutil.copyfileobj(file.file, target)
            await anyio.to_thread.run_sync(copy)
            if erp_doc_id:
                async with repository() as repo:
                    row = await repo.erp_document(erp_doc_id, version_no)
                checksum = await anyio.to_thread.run_sync(lambda: ObjectStorage.checksum(path))
                if row["FileHashSha256"] and row["FileHashSha256"] != checksum:
                    raise HTTPException(409, "SHA-256 file không khớp phiên bản ERP")
                metadata = {}
            else:
                metadata = await anyio.to_thread.run_sync(lambda: ObjectStorage().upload(path, filename))
            result = await _ingest_source(path, filename, metadata, doc_type, force_reingest)
            from app.services.ai_documents import public_ids
            ids = await public_ids([result.doc_id])
            return result.model_copy(update={"doc_id": f"sql:{erp_doc_id}" if erp_doc_id else ids.get(result.doc_id, result.doc_id)})
    except HTTPException:
        raise
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        logger.exception("Lưu hoặc nạp tài liệu thất bại")
        raise HTTPException(status_code=502, detail="Không lưu/nạp được tài liệu. Kiểm tra MinIO và Qdrant.")
    finally:
        if token is not None:
            erp_document.reset(token)
        await file.close()


@router.post("/ingest-minio", response_model=IngestResponse, summary="Nạp object MinIO có sẵn",
             dependencies=[Depends(quyen.can_quyen(quyen.QUYEN_KHO_GHI, "nạp tài liệu vào kho"))])
async def ingest_minio(request: MinioIngestRequest) -> IngestResponse:
    filename = _check_filename(request.object_key)
    try:
        with tempfile.TemporaryDirectory(prefix="tpv-ingest-") as folder:
            path = Path(folder) / filename
            metadata = await anyio.to_thread.run_sync(lambda: ObjectStorage().download(
                request.bucket, request.object_key, path, filename))
            return await _ingest_source(path, filename, metadata, request.doc_type, request.force_reingest)
    except HTTPException:
        raise
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        logger.exception("Nạp object MinIO thất bại")
        raise HTTPException(status_code=502, detail="Không đọc/nạp được object. File gốc được giữ nguyên trên MinIO.")


@router.post("/ingest-text", response_model=IngestResponse, summary="Nạp văn bản thô",
             dependencies=[Depends(quyen.can_quyen(quyen.QUYEN_KHO_GHI, "nạp tài liệu vào kho"))])
async def ingest_text(request: IngestTextRequest) -> IngestResponse:
    try:
        result = await get_ingestion_pipeline().ingest_text(
            text=request.text,
            doc_title=request.doc_title,
            doc_id=request.doc_id,
            source=request.source,
            doc_type=request.doc_type,
            metadata=request.metadata,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Nạp văn bản thất bại")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return IngestResponse(**asdict(result))


@router.get("/rule-sets", response_model=list[RuleSetInfo],
            summary="Các bộ tiêu chí cấu trúc có thể chọn")
async def rule_sets() -> list[RuleSetInfo]:
    """Mỗi file YAML trong `config/rules/` là một bộ tiêu chí.

    Thêm một bộ mới = thả thêm một file vào đó, không phải sửa code cũng không
    phải khởi động lại để giao diện thấy nó.
    """
    return [RuleSetInfo(**item) for item in available_rule_sets()]


@router.post("/summarize", response_model=SummaryResponse, summary="Tóm tắt độc lập (UC31)",
             dependencies=[Depends(quyen.can_quyen("Ai.Summarize", "tóm tắt văn bản"))])
async def summarize_document(request: SummaryRequest):
    from app.agents.graph import run_summary_workflow
    try:
        ref = storage.resolve(request.file_id, kinds=("upload",))
        if ref.suffix not in {".docx", ".pdf", ".txt", ".md"}:
            raise HTTPException(415, "Chỉ hỗ trợ DOCX, PDF, TXT, MD")
        if ref.path.stat().st_size > 20 * 1024**2:
            raise HTTPException(413, "File vượt giới hạn 20 MB")
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(422, "Không đọc được file nguồn đã upload") from exc
    return await run_summary_workflow(file_path=str(ref.path),file_name=ref.name,
        **request.model_dump(exclude={"file_id"}))


@router.post("/review", response_model=ReviewResponse, summary="Soát tài liệu (workflow 2)",
             dependencies=[Depends(quyen.can_quyen("Ai.DocumentReview", "soát tài liệu"))])
async def review_document(
    file: UploadFile = File(...),
    noi_gui: str = Form(default=""),
    rule_set: str = Form(default=""),
) -> ReviewResponse:
    """Kiểm tra cấu trúc, soát chữ nghĩa, phân loại và phân rã nhiệm vụ.

    Nhận MỌI loại tài liệu, không riêng văn bản hành chính. Hai nhánh dùng hai
    nguồn dữ liệu khác nhau: rule engine đọc cách tổ chức và định dạng thật của
    file (dàn ý, thứ bậc mục, đánh số, phông chữ, lề trang), còn LLM chỉ soát chữ
    nghĩa - chính tả, ngữ pháp, diễn đạt, logic.

    `rule_set` để trống là dùng bộ tiêu chí mặc định trong `config/rules`.
    """
    ten_sach = storage.safe_name(file.filename or "")
    if Path(ten_sach).suffix.lower() not in {".docx", ".pdf", ".txt", ".md"}:
        await file.close()
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Workflow 2 chỉ xử lý .docx, .pdf, .txt, .md",
        )

    try:
        target = storage.save_upload(file.file, file.filename or "tai-lieu", kind="upload").path
    finally:
        await file.close()

    # Danh mục phòng ban lấy từ CSDL - LLM chỉ được chọn trong danh sách này.
    departments: list[dict[str, str]] = []
    try:
        async with erp_session_scope() as session:
            departments = await ErpDonViRepository(session).catalog()
    except Exception as exc:  # noqa: BLE001 - mất CSDL thì bỏ định tuyến, không chết cả API
        logger.warning("Không đọc được danh mục phòng ban: %s", exc)

    result = await run_document_workflow(
        file_path=str(target), file_name=target.name,
        noi_gui=noi_gui, departments=departments, rule_set=rule_set,
    )
    return ReviewResponse(**result)


@router.post("/giao-viec", response_model=GiaoViecResponse,
             summary="Soạn công văn / quyết định giao nhiệm vụ từ bảng phân công",
             dependencies=[Depends(quyen.can_quyen("Ai.DocumentReview", "soạn văn bản giao việc"))])
async def giao_viec(request: GiaoViecRequest) -> GiaoViecResponse:
    """Đổ bảng phân công của `/review` ra một văn bản .docx để cán bộ sửa rồi trình ký.

    Không gọi LLM. Phần lời của hai mẫu là văn khuôn viết sẵn, phần thay đổi chỉ
    là bảng phân công - mà bảng đó lấy nguyên từ `tasks` gửi lên. Nhờ vậy nội dung
    file luôn khớp bảng người dùng nhìn thấy trên màn hình.

    Trường thể thức nào để trống thì file in dấu chấm lửng đúng lối văn thư, KHÔNG
    tự đặt số ký hiệu hay tên người ký: một số văn bản bịa trông y như số thật là
    thứ nguy hiểm nhất có thể nhét vào bản trình ký.
    """
    import anyio

    cfg = get_settings()
    meta = MetaGiaoViec(
        co_quan=request.co_quan, co_quan_chu_quan=request.co_quan_chu_quan,
        so_ky_hieu=request.so_ky_hieu, dia_danh=request.dia_danh, ngay=request.ngay,
        trich_yeu=request.trich_yeu, can_cu=list(request.can_cu),
        nguoi_ky=request.nguoi_ky, chuc_vu_ky=request.chuc_vu_ky,
        deadline=request.deadline, mo_dau=request.mo_dau,
    )
    tasks = [task.model_dump() for task in request.tasks]

    stem = storage.versioned_stem(
        "CV_GIAOVIEC" if request.loai == "cong_van" else "QD_GIAOVIEC")
    output_path = Path(cfg.output_dir) / f"{stem}.docx"

    try:
        path = await anyio.to_thread.run_sync(
            lambda: build_giao_viec(request.loai, meta, tasks, output_path))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Soạn văn bản giao việc thất bại")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=str(exc)) from exc

    from app.services.ai_outputs import register_file
    registered = await register_file(path)
    return GiaoViecResponse(
        ai_task_id=registered["ai_task_id"],
        loai=request.loai, file_name=path.name,
        download_url=f"/api/documents/download/{quote(path.name)}",
        task_count=len(tasks),
    )


@router.get("/download/{filename}", summary="Tải văn bản giao việc đã sinh")
async def download(filename: str) -> FileResponse:
    # Chặn cả vượt thư mục, đọc chéo thuê bao, lẫn bản cũ trong cache trình duyệt.
    return await output_file_response(filename)


@router.get("", response_model=DocumentPage, summary="Danh sách tài liệu đã nạp (phân trang)",
            dependencies=[Depends(quyen.can_quyen(quyen.QUYEN_KHO_DOC, "xem danh sách tài liệu"))])
async def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    q: str = Query("", max_length=200, description="Lọc theo tên / file nguồn / số hiệu (không phân biệt dấu)"),
    doc_type: str = Query("", max_length=100),
) -> DocumentPage:
    try:
        docs = await get_vector_store().list_documents()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Không đọc được danh sách tài liệu")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Không đọc được kho tài liệu") from exc
    legal = await _legal_info([d["doc_id"] for d in docs])
    items = [DocumentItem(**d, **legal.get(d["doc_id"], {})) for d in docs]
    if q.strip():
        from app.legal.identity import strip_accents

        key = strip_accents(q).lower().strip()
        items = [d for d in items if key in strip_accents(f"{d.doc_title} {d.source} {d.so_hieu}").lower()]
    if doc_type:
        items = [d for d in items if d.doc_type == doc_type]
    items.sort(key=lambda d: d.ingested_at or 0, reverse=True)         # mới nạp lên đầu
    start = (page - 1) * page_size
    return DocumentPage(items=items[start:start + page_size], total=len(items), page=page, page_size=page_size)


async def _legal_info(doc_ids: list[str]) -> dict[str, dict]:
    if not get_settings().legal_versioning_enabled or not doc_ids:
        return {}
    try:
        from sqlalchemy import select
        from app.db.models import LegalDocument
        from app.db.session import session_scope
        async with session_scope() as s:
            rows = (await s.execute(select(LegalDocument).where(LegalDocument.doc_id.in_(doc_ids)))).scalars().all()
    except Exception:  # noqa: BLE001 - thiếu thông tin VBQPPL không được làm hỏng danh sách
        logger.exception("Không đọc được thông tin VBQPPL cho danh sách tài liệu")
        return {}
    return {r.doc_id: {"so_hieu": r.so_hieu, "loai": r.loai,
                       "ngay_hieu_luc": r.ngay_hieu_luc.isoformat() if r.ngay_hieu_luc else None} for r in rows}


async def remove_document(doc_id: str) -> list[str]:
    """Xoá tài liệu khỏi kho: chunk trong Qdrant + cây phiên bản VBQPPL (nếu bật). Trả về doc_id các
    văn bản gốc đã dựng lại chunk vì văn bản sửa đổi của chúng vừa bị xoá."""
    from app.db.ai_session import enabled, repository
    if enabled():
        async with repository() as repo:
            row = await repo.document(doc_id, include_unready=True)
            if row is None:
                raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu")
            index_id = row["AiServerDocId"]
            await repo.lock(f"erp-document:{row['Id']}", shared=True)
            if index_id:
                await get_vector_store().delete_document(index_id)
            await repo.patch("KnowledgeDocuments", row["Id"], IndexStatus="NotIndexed", ChunkCount=0)
        return []
    await get_vector_store().delete_document(doc_id)
    if not get_settings().legal_versioning_enabled:
        return []
    from app.db.session import session_scope
    from app.legal import hooks, service

    async with session_scope() as s:
        targets = await service.delete_document(s, doc_id)
        packs = [await hooks.pack(s, t) for t in targets]
    pipe = get_ingestion_pipeline()
    for snap_doc in packs:
        await pipe.refresh_legal_document(snap_doc)
    return [p.doc_id for p in packs]


@router.post("/delete-batch", response_model=BatchDeleteResponse, summary="Xoá nhiều tài liệu khỏi kho",
             dependencies=[Depends(quyen.can_admin("xoá tài liệu khỏi kho"))])
async def delete_batch(request: BatchDeleteRequest) -> BatchDeleteResponse:
    result = BatchDeleteResponse()
    for doc_id in dict.fromkeys(request.doc_ids):          # bỏ trùng, giữ thứ tự
        try:
            result.rebuilt += await remove_document(doc_id)
            result.deleted.append(doc_id)
        except Exception as exc:  # noqa: BLE001 - một tài liệu lỗi không dừng cả lô
            logger.exception("Xoá %s thất bại", doc_id)
            result.errors[doc_id] = f"{type(exc).__name__}: {exc}"[:300]
    result.rebuilt = [d for d in dict.fromkeys(result.rebuilt) if d not in result.deleted]
    logger.info("Xoá hàng loạt: %d xong, %d lỗi, dựng lại %d", len(result.deleted), len(result.errors),
                len(result.rebuilt))
    return result


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Xoá tài liệu khỏi kho",
               dependencies=[Depends(quyen.can_admin("xoá tài liệu khỏi kho"))])
async def delete_document(doc_id: str) -> None:
    await remove_document(doc_id)


@router.get("/stats", response_model=CollectionStats, summary="Thống kê collection",
             dependencies=[Depends(quyen.can_quyen(quyen.QUYEN_KHO_DOC, "xem thống kê kho"))])
async def stats() -> CollectionStats:
    store = get_vector_store()
    try:
        info = await store.client.get_collection(store.collection)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"Không kết nối được Qdrant: {exc}") from exc

    vectors = list((info.config.params.vectors or {}).keys())
    vectors += list((info.config.params.sparse_vectors or {}).keys())
    return CollectionStats(
        collection=store.collection,
        points=info.points_count or 0,
        vectors=vectors,
    )

"""API nạp và quản lý tài liệu trong kho tri thức (nguồn dữ liệu cho workflow 1)."""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path

from urllib.parse import quote

from fastapi import Depends, APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from app.api.files import output_file_response
from app.core.config import get_settings
from app.rag.ingestion import SUPPORTED_SUFFIXES, get_ingestion_pipeline
from app.rag.vectorstore import get_vector_store
from app.agents.graph import run_document_workflow
from app.db.erp_repository import ErpDonViRepository
from app.db.erp_session import erp_session_scope
from app.documents.giao_viec import MetaGiaoViec, build_giao_viec
from app.documents.rules import available_rule_sets
from app.services import storage
from app.schemas.documents import (
    CollectionStats,
    GiaoViecRequest,
    GiaoViecResponse,
    IngestResponse,
    IngestTextRequest,
    ReviewResponse,
    RuleSetInfo,
)

from app.agents import quyen

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("/upload", response_model=IngestResponse, summary="Tải file lên và nạp vào kho",
             dependencies=[Depends(quyen.can_quyen(quyen.QUYEN_KHO_GHI, "nạp tài liệu vào kho"))])
async def upload_document(
    file: UploadFile = File(...),
    doc_type: str = Form(default=""),
) -> IngestResponse:
    # Đuôi file phải đọc trên tên ĐÃ làm sạch, không phải tên thô: cổng ABP gửi
    # tên tiếng Việt dưới dạng "=?utf-8?B?...?=" và đuôi thật nằm trong base64.
    ten_sach = storage.safe_name(file.filename or "")
    suffix = Path(ten_sach).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        await file.close()
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Chỉ hỗ trợ: {', '.join(sorted(SUPPORTED_SUFFIXES))}",
        )

    try:
        target = storage.save_upload(file.file, file.filename or "tai-lieu", kind="upload").path
    finally:
        await file.close()

    try:
        result = await get_ingestion_pipeline().ingest_file(target, doc_type=doc_type)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Nạp tài liệu thất bại")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return IngestResponse(**asdict(result))


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


@router.post("/review", response_model=ReviewResponse, summary="Soát tài liệu (workflow 2)",
             dependencies=[Depends(quyen.can_quyen("Ai.AiChatbot", "soát tài liệu"))])
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
             dependencies=[Depends(quyen.can_quyen("Ai.AiChatbot", "soạn văn bản giao việc"))])
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

    return GiaoViecResponse(
        loai=request.loai, file_name=path.name,
        download_url=f"/api/documents/download/{quote(path.name)}",
        task_count=len(tasks),
    )


@router.get("/download/{filename}", summary="Tải văn bản giao việc đã sinh")
async def download(filename: str) -> FileResponse:
    # Chặn cả vượt thư mục, đọc chéo thuê bao, lẫn bản cũ trong cache trình duyệt.
    return output_file_response(filename)


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Xoá tài liệu khỏi kho",
               dependencies=[Depends(quyen.can_admin("xoá tài liệu khỏi kho"))])
async def delete_document(doc_id: str) -> None:
    await get_vector_store().delete_document(doc_id)


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

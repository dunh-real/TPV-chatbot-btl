"""API workflow 3: soạn văn bản theo mẫu."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from app.agents.graph import run_aggregate_workflow, run_draft_workflow
from app.api.files import output_file_response
from app.db.repository import TemplateRepository
from app.db.session import session_scope
from app.schemas.reports import (
    AggregateRequest,
    AggregateResponse,
    DraftRequest,
    DraftResponse,
    TemplateSummary,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/templates", response_model=list[TemplateSummary], summary="Danh sách mẫu báo cáo")
async def list_templates() -> list[TemplateSummary]:
    async with session_scope() as session:
        return [TemplateSummary(**item) for item in await TemplateRepository(session).catalog()]


@router.post("/draft", response_model=DraftResponse, summary="Soạn báo cáo theo mẫu")
async def draft(request: DraftRequest) -> DraftResponse:
    """Số liệu lấy từ CSDL, LLM chỉ viết văn quanh số liệu đó.

    Con số nào trong phần LLM viết mà không truy được về dữ liệu gốc thì hệ thống
    cho viết lại; vẫn sai thì không xuất file và trả về `validation.status=failed`.
    """
    result = await run_draft_workflow(
        request=request.request, ma_don_vi=request.ma_don_vi, inputs=request.inputs,
    )

    if result.get("output_path"):
        result["download_url"] = f"/api/reports/download/{quote(Path(result['output_path']).name)}"
    return DraftResponse(**result)


@router.post("/aggregate", response_model=AggregateResponse,
             summary="Tổng hợp báo cáo nhiều đơn vị (workflow 4)")
async def aggregate(request: AggregateRequest) -> AggregateResponse:
    """Số liệu do data tool truy vấn, biểu đồ do code vẽ, LLM chỉ viết nhận xét.

    Trường `data` trả về nguyên kết quả của các data tool: mọi con số trong báo cáo
    đều đối chiếu được với nó.
    """
    result = await run_aggregate_workflow(request.request, inputs=request.inputs)
    if result.get("output_path"):
        result["download_url"] = f"/api/reports/download/{quote(Path(result['output_path']).name)}"
    return AggregateResponse(**result)


@router.get("/download/{filename}", summary="Tải file báo cáo đã sinh")
async def download(filename: str) -> FileResponse:
    # Chặn cả vượt thư mục, đọc chéo thuê bao, lẫn bản cũ trong cache trình duyệt.
    return output_file_response(filename)

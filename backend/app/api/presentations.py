"""API workflow 5: tạo bộ slide."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.agents.graph import run_presentation_workflow
from app.api.files import output_file_response
from app.schemas.presentations import PresentationRequest, PresentationResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/presentations", tags=["presentations"])



@router.post("/create", response_model=PresentationResponse, summary="Tạo bộ slide báo cáo")
async def create(request: PresentationRequest) -> PresentationResponse:
    """LLM chỉ sinh dàn ý và chữ; biểu đồ và file .pptx đều do code dựng.

    `outline` trả về là JSON trung gian đã lọc - xem được hệ thống định dựng slide
    nào trước khi mở file.
    """
    result = await run_presentation_workflow(request.request, inputs=request.inputs)
    if result.get("output_path"):
        result["download_url"] = (
            f"/api/presentations/download/{quote(Path(result['output_path']).name)}"
        )
    return PresentationResponse(**result)


@router.get("/download/{filename}", summary="Tải file .pptx đã tạo")
async def download(filename: str) -> FileResponse:
    """Chỉ trả file thuộc về thuê bao đang gọi.

    Tên file đoán được (`SLIDE_2026-08__t64.pptx`), nên nếu chỉ kiểm "có nằm trong
    thư mục output không" thì bất kỳ ai cũng tải được bộ slide của đơn vị khác.
    """
    return output_file_response(filename)

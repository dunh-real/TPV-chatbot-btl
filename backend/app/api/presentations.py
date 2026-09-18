"""API workflow 5: tạo bộ slide."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from app.agents.graph import run_presentation_workflow
from app.core.config import get_settings
from app.schemas.presentations import PresentationRequest, PresentationResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/presentations", tags=["presentations"])

PPTX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)


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
    cfg = get_settings()
    output_dir = Path(cfg.output_dir).resolve()
    target = (output_dir / Path(filename).name).resolve()
    if output_dir not in target.parents or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy file")
    return FileResponse(target, media_type=PPTX_MEDIA_TYPE, filename=target.name)

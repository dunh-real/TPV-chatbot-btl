"""API workflow 5: tạo bộ slide."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, APIRouter
from fastapi.responses import FileResponse

from app.agents.graph import run_presentation_workflow
from app.api.files import output_file_response
from app.schemas.presentations import PresentationRequest, PresentationResponse

from app.agents import quyen

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/presentations", tags=["presentations"])



@router.post("/create", response_model=PresentationResponse, summary="Tạo bộ slide báo cáo",
             dependencies=[Depends(quyen.can_quyen("Ai.Slide.Create", "tạo slide"))])
async def create(request: PresentationRequest) -> PresentationResponse:
    """Presenton dựng slide từ bản tóm tắt số liệu do hệ thống chốt sẵn.

    Mất khoảng 60-90 giây - giao diện nên hiện tiến trình, `elapsed_seconds` có
    trong kết quả. `brief` trả về là toàn bộ thứ Presenton nhìn thấy: đối chiếu
    ở đó để biết một con số lạ trên slide là do số liệu sai hay do bên kia viết
    thêm. `engine` cho biết file đến từ Presenton hay từ đường lùi tự dựng.
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

"""API của agent tổng: một endpoint cho cả năm workflow.

Các router `chat` / `documents` / `reports` / `presentations` vẫn giữ nguyên - chúng
là cửa vào khi CLIENT đã biết mình cần workflow nào (màn hình "soạn báo cáo" thì gọi
thẳng /api/reports/draft). Router này dành cho khung chat: người dùng gõ một câu,
agent tự chọn nghiệp vụ.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from app.agents.graph import run_agent
from app.schemas.agent import (
    AgentRequest,
    AgentResponse,
    Artifact,
    RoutingInfo,
    ToolInfo,
    UploadResponse,
)
from app.services import storage
from app.tools.registry import catalog

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent"])

MEDIA_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".pdf": "application/pdf",
}


@router.post("/chat", response_model=AgentResponse, summary="Hỏi hoặc giao việc cho agent")
async def chat(request: AgentRequest) -> AgentResponse:
    """Tự định tuyến: tra cứu, soát văn bản, soạn văn bản, tổng hợp hay làm slide.

    Gửi kèm `file_id` (lấy từ `POST /api/agent/upload`) khi muốn agent xử lý một
    văn bản cụ thể - không có file thì agent không đi nhánh soát văn bản.
    """
    result = await run_agent(
        request=request.request,
        conversation_id=request.conversation_id,
        file_id=request.file_id,
        ma_don_vi=request.ma_don_vi,
        inputs=request.inputs,
    )

    artifacts = [
        Artifact(
            **item,
            download_url=f"/api/agent/download/{quote(item['file_name'])}",
        )
        for item in result.get("artifacts", [])
    ]
    return AgentResponse(
        answer=result["answer"],
        conversation_id=result["conversation_id"],
        intent=result.get("intent", ""),
        routing=RoutingInfo(**result.get("routing", {})),
        citations=result.get("citations", []),
        refs=result.get("refs", []),
        artifacts=artifacts,
        missing_input=result.get("missing_input", []),
        result=result.get("result", {}),
        trace=result.get("trace") if request.include_trace else None,
        error=result.get("error", ""),
    )


@router.post("/upload", response_model=UploadResponse, summary="Tải file cho agent xử lý")
async def upload(file: UploadFile = File(...), kind: str = Form(default="upload")) -> UploadResponse:
    if kind not in storage.KINDS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"kind phải là một trong {list(storage.KINDS)}")
    try:
        ref = storage.save_upload(file.file, file.filename or "file", kind=kind)  # type: ignore[arg-type]
    finally:
        await file.close()
    return UploadResponse(**ref.as_dict())


@router.get("/tools", response_model=list[ToolInfo], summary="Những việc agent làm được")
async def tools() -> list[ToolInfo]:
    return [ToolInfo(**item) for item in catalog()]


@router.get("/download/{filename}", summary="Tải file agent vừa tạo")
async def download(filename: str) -> FileResponse:
    try:
        ref = storage.resolve(filename, kinds=("output",))
    except storage.StorageError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return FileResponse(
        ref.path,
        media_type=MEDIA_TYPES.get(ref.suffix, "application/octet-stream"),
        filename=ref.name,
    )

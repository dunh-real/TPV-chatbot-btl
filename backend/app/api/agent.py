"""API của agent tổng: một endpoint cho cả năm workflow.

Các router `chat` / `documents` / `reports` / `presentations` vẫn giữ nguyên - chúng
là cửa vào khi CLIENT đã biết mình cần workflow nào (màn hình "soạn báo cáo" thì gọi
thẳng /api/reports/draft). Router này dành cho khung chat: người dùng gõ một câu,
agent tự chọn nghiệp vụ.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse

from app.agents import progress
from app.services import errors
from app.agents.graph import run_agent
from app.schemas.agent import (
    AgentRequest,
    AgentResponse,
    Artifact,
    PlanModel,
    RoutingInfo,
    StepResultModel,
    ToolInfo,
    UploadResponse,
)
from app.api.files import output_file_response
from app.services import storage
from app.tools.registry import catalog

logger = logging.getLogger(__name__)

from app.core.context import current_principal

router = APIRouter(prefix="/api/agent", tags=["agent"])



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
    return _response(result, request.include_trace)


def _response(result: dict, include_trace: bool) -> AgentResponse:
    """Dựng phản hồi từ kết quả thô. Dùng chung cho `/chat` và `/chat/stream`.

    Hai đường phải trả về ĐÚNG một hình dạng: client chỉ có một hàm dựng giao
    diện, và nó không nên biết mình đang xem kết quả streaming hay không.
    """
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
        plan=PlanModel(**result.get("plan", {})),
        steps=[StepResultModel(**step) for step in result.get("steps", [])],
        citations=result.get("citations", []),
        refs=result.get("refs", []),
        artifacts=artifacts,
        missing_input=result.get("missing_input", []),
        result=result.get("result", {}),
        trace=result.get("trace") if include_trace else None,
        error=result.get("error", ""),
    )


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/chat/stream", summary="Như /chat nhưng phát tiến trình theo thời gian thực (SSE)")
async def chat_stream(request: AgentRequest) -> StreamingResponse:
    """Phát từng bước agent đang chạy, rồi mới tới câu trả lời cuối.

    Một yêu cầu nhiều bước mất 8-30 giây. Không có kênh này thì suốt thời gian đó
    giao diện chỉ có một vòng xoay, và người dùng không phân biệt được "đang tổng
    hợp số liệu" với "đã treo".

    Sự kiện: `plan` → (`step_start` | `thinking` | `step_retry` | `step_done`)* →
    `done`. Payload của `done` chính là `AgentResponse` của `POST /chat`, nên phía
    client dùng lại đúng một hàm dựng giao diện cho cả hai đường.

    KHÔNG có sự kiện nào mang kết quả công cụ: số liệu thô chưa qua van đối chiếu
    `check_numbers`, đẩy lên màn hình là mời người đọc tin vào con số mà chính hệ
    thống chưa xác nhận.
    """
    channel = progress.Channel()

    async def run() -> None:
        try:
            with progress.collecting(channel.put):
                result = await run_agent(
                    request=request.request,
                    conversation_id=request.conversation_id,
                    file_id=request.file_id,
                    ma_don_vi=request.ma_don_vi,
                    inputs=request.inputs,
                )
            channel.put("done", _response(result, request.include_trace).model_dump())
        except Exception as exc:  # noqa: BLE001 - lỗi phải tới được client, không chỉ vào log
            # `str(exc)` ở đây là nguyên văn ngoại lệ - với lỗi CSDL thì đó là cả
            # câu SQL kèm tên bảng. Client nhận câu đã dịch; chi tiết vào log.
            channel.put("error", {"detail": errors.as_error(exc, "chạy agent")})
        finally:
            channel.close()

    async def event_stream() -> AsyncIterator[str]:
        task = asyncio.create_task(run())
        try:
            while True:
                item = await channel.queue.get()
                if item is None:
                    break
                event, data = item
                yield _sse(event, data)
        finally:
            # Client đóng tab giữa chừng: dừng luôn agent thay vì để nó chạy nốt
            # một vòng lặp công cụ mà không ai đọc kết quả.
            if not task.done():
                task.cancel()
        if channel.dropped:
            logger.info("Đã bỏ %d sự kiện tiến trình do client đọc chậm", channel.dropped)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
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


@router.get("/accounts", summary="Tài khoản ERP để giao diện demo chọn danh tính")
async def accounts() -> dict:
    """Danh sách tài khoản của tenant hiện tại, kèm vai trò và số quyền.

    Chỉ phục vụ DEMO, khi hệ thống chưa có đăng nhập thật: giao diện cho chọn một
    người rồi gửi `X-User-Id`. Có đăng nhập rồi thì endpoint này nên tắt - nó bày
    ra danh sách nhân sự cho bất kỳ ai gọi được API.

    Không trả về email của người khác ngoài thứ cần để nhận diện, và không bao giờ
    trả về bất cứ thứ gì liên quan tới mật khẩu.
    """
    from app.services.access import list_demo_accounts

    principal = current_principal()
    return {
        "tenant_id": principal.tenant_id,
        "current_user_id": principal.user_id,
        "accounts": await list_demo_accounts(principal.tenant_id),
    }


@router.get("/tools", response_model=list[ToolInfo], summary="Những việc agent làm được")
async def tools() -> list[ToolInfo]:
    return [ToolInfo(**item) for item in catalog()]


@router.get("/download/{filename}", summary="Tải file agent vừa tạo")
async def download(filename: str) -> FileResponse:
    return output_file_response(filename)

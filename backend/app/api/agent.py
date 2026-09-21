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
import re
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile, status
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
from app.rag.converter import SUPPORTED_SUFFIXES
from app.rag.ingestion import get_ingestion_pipeline
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
        doc_ids=request.doc_ids,
        sources=request.sources,
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
        session_files=result.get("session_files", []),
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
                    doc_ids=request.doc_ids,
                    sources=request.sources,
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


# Hậu tố GUID mà phía gọi gắn thêm để hai lượt tải cùng một file không đè nhau:
# "Em_Day_A_4b3fba0179e741dc851f404932d6b7e5.pdf". Nó là chuyện lưu trữ, không
# phải tên tài liệu - gỡ ra trước khi đem đi làm nhãn trích dẫn.
_HAU_TO_GUID = re.compile(r"_[0-9a-fA-F]{32}(?=\.[^.]+$)")


def ten_that(ten_tren_dia: str) -> str:
    """Tên người dùng nhìn thấy, gỡ hậu tố GUID nếu có."""
    return _HAU_TO_GUID.sub("", ten_tren_dia)


@router.post("/upload", response_model=UploadResponse, summary="Tải file cho agent xử lý")
async def upload(background: BackgroundTasks, file: UploadFile = File(...),
                 kind: str = Form(default="upload")) -> UploadResponse:
    """Lưu file cho agent, ĐỒNG THỜI nạp nó vào kho tri thức.

    Hai việc này từng nằm ở hai cửa: cửa này lưu file để soát/trích dẫn, còn
    `/api/documents/upload` mới nạp vào kho. Phía gọi phải nhớ gọi cả hai, và
    quên một cái thì hỏng lặng lẽ - tài liệu nằm trên đĩa, giao diện hiện tên nó
    đang được chọn, nhưng hỏi gì cũng ra "không tìm thấy trong kho". Đã xảy ra
    thật nhiều lần trong một buổi, mỗi lần đều mất công truy mới ra.

    Nạp kèm ở đây không sinh bản trùng: `doc_id` băm theo TÊN THẬT (đã gỡ GUID)
    cộng bytes file, nên gọi cả hai cửa vẫn ra đúng một bản.

    Nạp chạy SAU khi đã trả lời, không bắt người dùng đợi. Tài liệu scan phải qua
    OCR: một bản 5 trang mất 16 giây, mà trước đây cửa này trả về trong một phần
    mười giây vì chỉ ghi file. Giao diện đã quen với tốc độ đó - bắt nó đợi thêm
    16 giây là mời một cơn hết giờ chờ, và lượt hỏi kèm theo file không bao giờ
    được gửi đi.

    Nạp hỏng KHÔNG làm hỏng lượt tải: nhánh soát văn bản chỉ cần file trên đĩa,
    và chặn cả việc đính kèm chỉ vì kho không ghi được là thiệt hơn.
    """
    if kind not in storage.KINDS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"kind phải là một trong {list(storage.KINDS)}")
    try:
        ref = storage.save_upload(file.file, file.filename or "file", kind=kind)  # type: ignore[arg-type]
    finally:
        await file.close()

    ten = ten_that(ref.path.name)
    if Path(ten).suffix.lower() in SUPPORTED_SUFFIXES:
        background.add_task(nap_vao_kho, ref.path, ten)
    return UploadResponse(**ref.as_dict())


async def nap_vao_kho(path: Path, ten: str) -> None:
    """Nạp file vừa tải lên vào kho tri thức. Chạy nền, nuốt mọi lỗi."""
    try:
        ket = await get_ingestion_pipeline().ingest_file(path, source=ten)
        logger.info("Đã nạp %s vào kho: %s (%d chunk)", ten, ket.doc_id, ket.chunk_count)
    except Exception:  # noqa: BLE001 - kho hỏng không được chặn việc đính kèm
        logger.exception("Không nạp được %s vào kho, file vẫn dùng được để soát", ten)


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

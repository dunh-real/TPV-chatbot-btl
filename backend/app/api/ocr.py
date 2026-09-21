"""API OCR tài liệu: ảnh trang -> Markdown, đọc bằng chính model đang chạy.

MÔ HÌNH NÀO ĐỌC

Không có model riêng cho OCR. `OCR_BASE_URL` trỏ về đúng server vLLM đang phục vụ
cả hệ thống (Qwen3.6-35B-A3B có sẵn vision encoder), nên trang scan được đọc bằng
chính trọng số đã nằm sẵn trên card - không nạp thêm gì, không chiếm thêm VRAM.

VÌ SAO CÓ HAI CỬA

    POST /extract          một phát ăn ngay, trả về cả tài liệu. Cho bên thứ ba
                           gọi bằng script, và cho test.
    POST /extract/stream   trả từng trang qua SSE. Cho giao diện.

Tài liệu 30 trang scan mất vài phút. Dồn hết vào một phản hồi thì người xem nhìn
màn hình trống suốt thời gian đó, và quan trọng hơn: đường công khai qua Cloudflare
cắt mọi request im lặng quá 125 giây (lỗi 524, chỉ bản Enterprise mới nâng được).
Stream giữ cho dữ liệu chảy liên tục nên không chạm trần đó.

Hai cửa dùng CHUNG một bộ máy `_chay`, chỉ khác cách đóng gói - để không có
chuyện sửa một bên rồi bên kia lặng lẽ trả về thứ khác.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import anyio
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from app.agents import quyen
from app.core.config import get_settings
from app.documents.ocr_tai_lieu import (
    KeHoach,
    OcrError,
    TrangKetQua,
    doc_trang_ocr,
    dung_day_du,
    ghep_markdown,
    kiem_tra_dinh_dang,
    lap_ke_hoach,
    trang_digital,
)
from app.rag.ocr import get_ocr_service
from app.schemas.ocr import OcrRequest, OcrResponse, OcrStatus, OcrTrang
from app.services import storage
from app.documents.ocr_tai_lieu import SUPPORTED_SUFFIXES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ocr", tags=["ocr"])

OCR_QUYEN = Depends(quyen.can_quyen(quyen.QUYEN_KHO_DOC, "OCR tài liệu"))


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _trang_payload(trang: TrangKetQua) -> dict[str, Any]:
    return OcrTrang(
        so_trang=trang.so_trang,
        nguon=trang.nguon,  # type: ignore[arg-type]
        so_ky_tu=trang.so_ky_tu,
        markdown=trang.markdown,
    ).model_dump()


async def _bom(sync_iter: Iterator[TrangKetQua]) -> AsyncIterator[TrangKetQua]:
    """Kéo một iterator đồng bộ sang async, mỗi bước chạy trong thread riêng.

    `doc_trang_ocr` chặn ở lời gọi HTTP tới vLLM. Gọi thẳng trong vòng lặp sự kiện
    thì treo cả backend - mọi request khác đứng im cho tới khi OCR xong.
    """
    het = object()
    while True:
        item = await anyio.to_thread.run_sync(next, sync_iter, het)
        if item is het:
            return
        yield item  # type: ignore[misc]


async def _chay(
    path: Path, ten_file: str, che_do: str
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Sinh lần lượt: `start` -> nhiều `trang` -> `done`, hoặc `error`.

    Trang digital trả ngay (chỉ đọc lớp text), trang scan trả dần theo tốc độ của
    mô hình. `done` KHÔNG kèm Markdown gộp: client đã nhận đủ từng trang rồi, gửi
    lại toàn bộ lần nữa là nhân đôi lưu lượng của thứ nặng nhất trong phản hồi.
    """
    cfg = get_settings()
    t0 = time.perf_counter()

    try:
        ke_hoach: KeHoach = await anyio.to_thread.run_sync(lap_ke_hoach, path, che_do)
    except OcrError as exc:
        yield "error", {"detail": str(exc)}
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("Không lập được kế hoạch OCR cho %s", ten_file)
        yield "error", {"detail": f"Không đọc được {ten_file}: {exc}"}
        return

    yield "start", {
        "file_name": ten_file,
        "che_do": che_do,
        "model": cfg.ocr_model,
        "so_trang": ke_hoach.so_trang,
        "so_trang_ocr": len(ke_hoach.trang_can_ocr),
        "so_trang_digital": ke_hoach.so_trang - len(ke_hoach.trang_can_ocr),
    }

    da_doc: dict[int, TrangKetQua] = {}
    for trang in trang_digital(ke_hoach):
        da_doc[trang.so_trang] = trang
        yield "trang", _trang_payload(trang)

    try:
        async for trang in _bom(doc_trang_ocr(path, ke_hoach)):
            da_doc[trang.so_trang] = trang
            yield "trang", _trang_payload(trang)
    except OcrError as exc:
        yield "error", {"detail": str(exc)}
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("OCR %s thất bại", ten_file)
        yield "error", {"detail": f"OCR thất bại: {exc}"}
        return

    # Trang chưa từng phát ra là trang đọc hỏng. Phát nốt để client không phải tự
    # suy ra chỗ thiếu - và để hai cửa (/extract và /extract/stream) mô tả cùng một
    # tài liệu bằng cùng một danh sách trang.
    day_du = dung_day_du(ke_hoach, da_doc)
    for trang in day_du:
        if trang.so_trang not in da_doc:
            yield "trang", _trang_payload(trang)

    yield "done", {
        "file_name": ten_file,
        "che_do": che_do,
        "model": cfg.ocr_model,
        "so_trang": ke_hoach.so_trang,
        # Đếm trên kết quả thật, không trên kế hoạch: trang OCR lỗi thì không
        # được tính là đã đọc, dù nó có nằm trong danh sách phải đọc.
        "so_trang_ocr": sum(1 for t in day_du if t.nguon == "ocr"),
        "so_trang_digital": sum(1 for t in day_du if t.nguon == "digital"),
        "so_trang_trong": sum(1 for t in day_du if t.nguon == "trong"),
        "so_trang_loi": sum(1 for t in day_du if t.nguon == "loi"),
        "giay": round(time.perf_counter() - t0, 1),
    }


def _mo_file(file_id: str) -> storage.FileRef:
    try:
        ref = storage.resolve(file_id)
    except storage.StorageError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    try:
        kiem_tra_dinh_dang(ref.name)
    except OcrError as exc:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            detail=str(exc)) from exc
    return ref


# --------------------------------------------------------------------------- #
@router.get("/status", response_model=OcrStatus, dependencies=[OCR_QUYEN],
            summary="Cấu hình OCR đang chạy")
async def ocr_status() -> OcrStatus:
    cfg = get_settings()
    service = get_ocr_service()
    return OcrStatus(
        enabled=service.enabled,
        model=cfg.ocr_model,
        base_url=cfg.ocr_base_url,
        max_concurrency=cfg.ocr_max_concurrency,
        render_scale=cfg.ocr_render_scale,
        dinh_dang=sorted(SUPPORTED_SUFFIXES),
    )


@router.post("/extract", response_model=OcrResponse, dependencies=[OCR_QUYEN],
             summary="OCR một tài liệu, trả về Markdown")
async def extract(
    file: UploadFile = File(...),
    che_do: str = Form(default="auto"),
) -> OcrResponse:
    """Chạy hết rồi mới trả lời.

    Tài liệu dài có thể mất vài phút - qua Cloudflare thì nên dùng `/extract/stream`
    để khỏi chạm trần 125 giây.
    """
    # Kiểm trên tên ĐÃ làm sạch, đúng cái tên rồi sẽ nằm trên đĩa: tên thô có
    # thể là "=?utf-8?B?...?=" của cổng ABP, đuôi thật nằm trong phần base64.
    try:
        kiem_tra_dinh_dang(storage.safe_name(file.filename or ""))
    except OcrError as exc:
        await file.close()
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            detail=str(exc)) from exc

    try:
        ref = storage.save_upload(file.file, file.filename or "tai-lieu", kind="upload")  # type: ignore[arg-type]
    finally:
        await file.close()

    trang_da_doc: dict[int, dict[str, Any]] = {}
    tong_ket: dict[str, Any] = {}
    async for event, payload in _chay(ref.path, ref.name, che_do):
        if event == "error":
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                                detail=payload["detail"])
        if event == "trang":
            trang_da_doc[payload["so_trang"]] = payload
        elif event == "done":
            tong_ket = payload

    trang = [
        OcrTrang(**trang_da_doc.get(so, {"so_trang": so, "nguon": "trong",
                                         "so_ky_tu": 0, "markdown": ""}))
        for so in range(1, int(tong_ket.get("so_trang", 0)) + 1)
    ]
    return OcrResponse(
        file_name=tong_ket.get("file_name", ref.name),
        che_do=tong_ket.get("che_do", che_do),
        model=tong_ket.get("model", ""),
        so_trang=tong_ket.get("so_trang", 0),
        so_trang_ocr=tong_ket.get("so_trang_ocr", 0),
        so_trang_digital=tong_ket.get("so_trang_digital", 0),
        so_trang_loi=tong_ket.get("so_trang_loi", 0),
        giay=tong_ket.get("giay", 0.0),
        markdown=ghep_markdown([
            TrangKetQua(so_trang=t.so_trang, nguon=t.nguon, markdown=t.markdown)
            for t in trang
        ]),
        trang=trang,
    )


@router.post("/extract/stream", dependencies=[OCR_QUYEN],
             summary="OCR một tài liệu, trả từng trang qua SSE")
async def extract_stream(request: OcrRequest) -> StreamingResponse:
    """File phải được tải lên trước qua `POST /api/agent/upload`.

    Sự kiện: `start` (đã biết có mấy trang, trang nào phải OCR) → nhiều `trang`
    (không theo thứ tự, client tự xếp theo `so_trang`) → `done` hoặc `error`.
    """
    ref = _mo_file(request.file_id)

    async def event_stream() -> AsyncIterator[str]:
        async for event, payload in _chay(ref.path, ref.name, request.che_do):
            yield _sse(event, payload)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

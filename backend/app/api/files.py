"""Trả file đầu ra cho client - dùng chung cho cả ba cửa tải về.

Tên file đầu ra cố định theo kỳ và tenant ("SLIDE_2026-08__t64.pptx"), nên lần
tạo sau ghi đè lần trước TRÊN CÙNG MỘT URL. Với trình duyệt, cùng URL mà không có
chỉ dẫn cache nào là lời mời dùng lại bản đã tải: người dùng bấm "tạo lại", thấy
thông báo thành công, mở file ra vẫn là nội dung cũ - và đi tìm lỗi ở backend.

Đây đúng cái bẫy mà `FreshStaticFiles` trong `app.main` đã xử cho file giao diện;
file tải về cần y như vậy, chỉ là trước giờ bị bỏ sót.

`no-cache` không phải "cấm lưu" mà là "lưu thì lưu, nhưng phải hỏi lại trước khi
dùng". Kèm ETag sẵn có của `FileResponse`, lần hỏi lại nào không đổi cũng chỉ tốn
một 304 rỗng.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from fastapi.responses import FileResponse

from app.services import storage

MEDIA_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".pdf": "application/pdf",
}

NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}


def output_file_response(filename: str) -> FileResponse:
    """File trong thư mục output: đã chặn vượt thư mục, đọc chéo tenant, và cache cũ."""
    try:
        ref = storage.resolve_output(filename)
    except storage.StorageError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return FileResponse(
        ref.path,
        media_type=MEDIA_TYPES.get(ref.suffix, "application/octet-stream"),
        filename=ref.name,
        headers=NO_CACHE,
    )

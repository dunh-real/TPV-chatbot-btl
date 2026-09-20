"""Nhớ những tài liệu đã tải lên trong một hội thoại.

VÌ SAO CẦN

`file_id` đi theo từng request. Người dùng đính kèm một tài liệu ở lượt 1, sang
lượt 2 gõ "tổng hợp lại tài liệu đó thành báo cáo" thì request ấy không còn
`file_id` nào - agent coi như chưa từng thấy file, rơi về đọc CSDL, và trả về số
liệu chẳng liên quan gì tới thứ người dùng vừa gửi.

KHI NÀO LẤY RA DÙNG LẠI, VÀ VÌ SAO KHÔNG PHẢI LÚC NÀO CŨNG DÙNG

Gắn lại file cho MỌI lượt sau là sai: đính kèm một tài liệu rồi hỏi "nhân sự
toàn công ty tháng 8 bao nhiêu" thì câu đó hỏi CSDL, không hỏi file. Tự gắn file
vào sẽ đẩy nó sang nhánh đọc tài liệu và trả lời sai nguồn.

Nên chỉ dùng lại khi câu chữ TRỎ TỚI một tài liệu: "tài liệu đó", "file vừa gửi",
"văn bản trên". Người dùng muốn dùng file thì nói ra, còn im lặng thì hiểu là
hỏi nguồn mặc định.
"""

from __future__ import annotations

import logging
import re
import time

from app.services.cache import get_cache

logger = logging.getLogger(__name__)

PREFIX = "chat:files:"
GIOI_HAN = 10  # nhớ tối đa 10 tài liệu gần nhất mỗi hội thoại


def _key(conversation_id: str) -> str:
    return f"{PREFIX}{conversation_id}"


# Câu chữ trỏ tới một tài liệu đã có, chứ không phải một nguồn số liệu nào khác.
_TRO_TOI_TAI_LIEU = re.compile(
    r"\b(tài liệu|tai lieu|văn bản|van ban|file|tệp|tep|đính kèm|dinh kem"
    r"|báo cáo (?:vừa|đã) (?:gửi|tải|upload)|vừa gửi|vua gui|vừa tải|vua tai)\b",
    re.IGNORECASE,
)


def nhac_toi_tai_lieu(request: str) -> bool:
    """Câu này có đang trỏ tới một tài liệu đã tải lên không."""
    return bool(_TRO_TOI_TAI_LIEU.search(request or ""))


async def ghi_nho(conversation_id: str, file_id: str, ten: str = "") -> None:
    """Ghi nhớ một tài liệu vừa được dùng trong hội thoại.

    Trùng `file_id` thì đẩy lên đầu thay vì thêm dòng mới - gửi lại cùng một file
    nghĩa là nó đang được quan tâm, không phải là có thêm một file nữa.
    """
    if not conversation_id or not file_id:
        return
    cache = get_cache()
    key = _key(conversation_id)
    hien_co = await cache.get_json(key) or []
    hien_co = [f for f in hien_co if f.get("file_id") != file_id]
    hien_co.insert(0, {
        "file_id": file_id,
        "ten": ten or file_id.split(":", 1)[-1],
        "ts": time.time(),
    })
    # TTL bằng lịch sử hội thoại: tài liệu không nên biến mất trước các lượt chat
    # nhắc tới nó.
    await cache.set_json(key, hien_co[:GIOI_HAN],
                         ttl=cache.settings.cache_ttl_seconds * 24)


async def danh_sach(conversation_id: str) -> list[dict]:
    """Tài liệu của hội thoại, mới nhất trước."""
    if not conversation_id:
        return []
    return await get_cache().get_json(_key(conversation_id)) or []


async def moi_nhat(conversation_id: str) -> str:
    """`file_id` của tài liệu gần nhất, hoặc chuỗi rỗng."""
    ds = await danh_sach(conversation_id)
    return ds[0]["file_id"] if ds else ""


async def quen_het(conversation_id: str) -> None:
    if conversation_id:
        await get_cache().set_json(_key(conversation_id), [], ttl=1)


async def file_cho_yeu_cau(conversation_id: str, request: str, file_id: str) -> str:
    """`file_id` nên dùng cho lượt này.

    Request tự mang file thì dùng luôn cái đó. Không mang mà câu chữ trỏ tới tài
    liệu thì lấy cái gần nhất của hội thoại. Còn lại trả rỗng - để nguyên hành vi
    cũ, tức đọc CSDL hoặc kho tri thức.
    """
    if file_id:
        return file_id
    if not nhac_toi_tai_lieu(request):
        return ""
    nho = await moi_nhat(conversation_id)
    if nho:
        logger.info("Dùng lại tài liệu %r của hội thoại %s", nho, conversation_id)
    return nho

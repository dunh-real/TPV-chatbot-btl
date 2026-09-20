"""Danh tính của request: thuê bao (tenant) và người dùng đang gọi.

Mọi truy vấn ERP đều phải bị giới hạn trong đúng một tenant. Trước đây giá trị đó
lấy thẳng từ `Settings.erp_tenant_id` - một hằng số toàn tiến trình. Điều đó chỉ
đúng khi cả hệ thống phục vụ một đơn vị duy nhất; khi giao diện của đội dev gắn
đăng nhập, mỗi request sẽ mang một tenant khác nhau và một biến toàn cục thì
không có chỗ để đặt sự khác nhau ấy.

Nên danh tính nằm ở `ContextVar`: đặt một lần ở rìa ứng dụng, đọc được ở mọi độ
sâu bên dưới mà không phải luồn tham số qua mười lớp hàm - kể cả trong các task
mà `/chat/stream` sinh ra, vì `asyncio.create_task` sao chép ngữ cảnh lúc tạo.

Thứ tự lấy danh tính, và đây là CHỖ DUY NHẤT quyết định điều đó:

    1. Tài khoản đăng nhập  - chưa có; `_from_token` là điểm cắm sẵn.
    2. Header X-Tenant-Id / X-User-Id  - đường đang dùng để chạy thử.
    3. ERP_TENANT_ID trong .env  - mặc định khi client không nói gì.

Bước 2 là một lỗ hổng có chủ ý và chỉ chấp nhận được khi hệ thống còn chạy trong
mạng nội bộ: ai gọi được API cũng tự xưng được tenant bất kỳ. Khi auth lên, đặt
TRUST_IDENTITY_HEADERS=false - header lập tức bị bỏ qua và chỉ token nói được
danh tính. Không xoá nhánh header đi: môi trường test vẫn cần nó.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)

TENANT_HEADER = "x-tenant-id"
USER_HEADER = "x-user-id"


class IdentityError(ValueError):
    """Client gửi danh tính sai định dạng - chặn ngay, không lặng lẽ dùng mặc định.

    Bỏ qua một `X-Tenant-Id` viết sai rồi rơi về tenant mặc định là cách chắc chắn
    nhất để trả số liệu của đơn vị khác mà không ai nhận ra.
    """


@dataclass(frozen=True, slots=True)
class Principal:
    """Ai đang gọi, và được nhìn thấy dữ liệu của thuê bao nào.

    `tenant_id = None` nghĩa là KHÔNG lọc theo tenant, tức cộng gộp số liệu của
    mọi thuê bao trên cùng máy chủ. Đó gần như luôn là sai với báo cáo của một
    đơn vị, nên nó chỉ nên xuất hiện khi có người cố ý đặt như vậy.
    """

    tenant_id: int | None = None
    user_id: str | None = None
    # "token" | "header" | "default" - đi vào log để khi số liệu lệch còn truy được
    # request đó đã lấy tenant từ đâu.
    source: str = "default"
    # Quyền đọc từ ERP. `None` nghĩa là CHƯA TRA ĐƯỢC, khác hẳn "tra rồi và rỗng":
    # chưa tra được thì không có căn cứ để chặn ai, còn tra rồi mà rỗng thì đúng
    # là người này không có quyền gì. Hai trường hợp xử lý khác nhau ở `can()`.
    access: Any | None = None

    @property
    def is_admin(self) -> bool:
        return bool(self.access and self.access.is_static_admin)

    def can(self, permission: str) -> bool:
        """Được làm việc này không.

        Chưa tra được quyền (`access is None`) thì trả True - hệ thống chạy như
        trước khi có phân quyền. Chặn người dùng chỉ vì ERP tạm thời không tra
        được là đổi một sự cố hạ tầng thành một sự cố nghiệp vụ.
        """
        if self.access is None:
            return True
        return self.access.has(permission)

    def describe(self) -> str:
        vai = ""
        if self.access is not None:
            vai = (" · toàn quyền" if self.access.is_static_admin
                   else f" · {len(self.access.permissions)} quyền")
        return (f"tenant={self.tenant_id} user={self.user_id or '-'} "
                f"(nguồn: {self.source}){vai}")


_current: ContextVar[Principal | None] = ContextVar("principal", default=None)


def default_principal() -> Principal:
    """Danh tính khi không có request nào đặt: lấy từ .env.

    Dùng cho script, tác vụ nền và test - những chỗ không đi qua HTTP nhưng vẫn
    phải đọc ERP với đúng một tenant.
    """
    return Principal(tenant_id=get_settings().erp_tenant_id, source="default")


def current_principal() -> Principal:
    return _current.get() or default_principal()


def current_tenant_id() -> int | None:
    return current_principal().tenant_id


@contextmanager
def use_principal(principal: Principal) -> Iterator[Principal]:
    """Đặt danh tính cho một khối lệnh. Luôn trả lại giá trị cũ khi thoát."""
    token = _current.set(principal)
    try:
        yield principal
    finally:
        _current.reset(token)


def _parse_tenant(raw: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise IdentityError(
            f"{TENANT_HEADER} phải là số nguyên, nhận được {raw!r}"
        ) from exc


def _from_token(headers: dict[str, str]) -> Principal | None:
    """Danh tính từ tài khoản đăng nhập.

    Điểm cắm cho lúc giao diện của đội dev gắn đăng nhập: giải mã JWT ở
    `Authorization: Bearer ...`, lấy claim tenant và user, trả về `Principal` với
    `source="token"`. Trả `None` nghĩa là request này không mang token.

    Chưa nối nên luôn `None`; giữ hàm ở đây để khi nối thì chỉ sửa đúng một chỗ.
    """
    return None


def resolve_principal(headers: dict[str, str]) -> Principal:
    """Danh tính của một request, theo đúng thứ tự ưu tiên ghi ở đầu file."""
    from_token = _from_token(headers)
    if from_token is not None:
        return from_token

    fallback = default_principal()
    if not get_settings().trust_identity_headers:
        # Đã có auth: header không còn là lời nói có trọng lượng.
        return fallback

    tenant_raw = headers.get(TENANT_HEADER, "").strip()
    user_raw = headers.get(USER_HEADER, "").strip()
    if not tenant_raw and not user_raw:
        return fallback

    return Principal(
        tenant_id=_parse_tenant(tenant_raw) if tenant_raw else fallback.tenant_id,
        user_id=user_raw or None,
        source="header",
    )

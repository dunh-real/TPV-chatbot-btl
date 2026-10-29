"""Dịch sự cố kỹ thuật thành câu người dùng đọc được.

Một câu lệnh SQL kèm traceback văng ra giữa khung chat vừa xấu vừa vô dụng với
người dùng cuối: họ không sửa được quyền CSDL, và đọc xong vẫn không biết nên làm
gì tiếp. Tệ hơn, nó khoe ra tên bảng, tên schema và cấu trúc truy vấn - thứ không
có lý do gì để hiện trên một trang có thể mở ra Internet.

Nguyên tắc: người dùng nhận MỘT câu nói đúng chuyện gì đã xảy ra và ai xử lý được;
chi tiết kỹ thuật đi vào log, nơi người trực hệ thống thật sự đọc nó.

    lỗi gốc ──> logger.exception(...)     đầy đủ, cho người trực
            └─> friendly(exc)             một câu, cho người dùng

Không nuốt lỗi: mọi chỗ gọi `friendly` đều đã ghi log trước đó.
"""

from __future__ import annotations

import logging

from app.tools.base import ToolError

logger = logging.getLogger(__name__)

# Câu trả lời cuối cùng khi không nhận ra lỗi thuộc nhóm nào. Cố tình không nói
# "lỗi không xác định": với người dùng thì mọi lỗi đều không xác định, câu đó chỉ
# thêm chữ chứ không thêm thông tin.
DEFAULT = "Hệ thống gặp sự cố khi xử lý yêu cầu này. Vui lòng thử lại hoặc báo quản trị hệ thống."

DATABASE = ("Chưa truy vấn được cơ sở dữ liệu nghiệp vụ, nên phần số liệu chưa lấy được. "
            "Vui lòng báo quản trị hệ thống kiểm tra kết nối và quyền truy cập.")

VECTOR_STORE = ("Kho tài liệu đang không truy vấn được, nên chưa tra cứu được nội dung. "
                "Vui lòng thử lại sau ít phút.")

LLM = "Chưa gọi được mô hình ngôn ngữ, vui lòng thử lại sau ít phút."

# Nhận dạng theo TÊN MODULE của lớp ngoại lệ chứ không import từng thư viện: chỗ
# này chỉ để phân loại, không đáng kéo theo phụ thuộc cứng vào sqlalchemy/qdrant.
_BY_MODULE: tuple[tuple[str, str], ...] = (
    ("sqlalchemy", DATABASE),
    ("pyodbc", DATABASE),
    ("aioodbc", DATABASE),
    ("asyncpg", DATABASE),
    ("qdrant_client", VECTOR_STORE),
    ("grpc", VECTOR_STORE),
    ("httpx", LLM),
)


def friendly(exc: BaseException) -> str:
    """Một câu tiếng Việt cho người dùng cuối, không lộ chi tiết hạ tầng."""
    # `ToolError` do chính mình ném ra và đã viết sẵn bằng tiếng người ("Kỳ phải
    # có dạng YYYY-MM"), nên giữ nguyên - đó là thứ giúp người dùng sửa đầu vào.
    if isinstance(exc, ToolError):
        return str(exc)

    from app.services.llm import LLMError

    if isinstance(exc, LLMError):
        return LLM

    for cls in type(exc).__mro__:
        module = (getattr(cls, "__module__", "") or "").split(".")[0]
        for prefix, message in _BY_MODULE:
            if module == prefix:
                return message
    return DEFAULT


def as_error(exc: BaseException, context: str = "") -> str:
    """Ghi log đầy đủ rồi trả về câu cho người dùng.

    Dùng ở những chỗ bắt ngoại lệ rộng, để không ai vô tình vừa nuốt lỗi vừa
    đẩy nguyên traceback ra ngoài.
    """
    logger.exception("Sự cố%s", f" khi {context}" if context else "")
    return friendly(exc)

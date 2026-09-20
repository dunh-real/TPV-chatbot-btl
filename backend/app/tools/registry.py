"""Danh mục tool của agent - tất cả những gì hệ thống làm được, gom về một chỗ.

Đây là mặt tiền duy nhất mà tầng agent nhìn thấy. Giá trị của nó không nằm ở chỗ
gọi hộ hàm, mà ở ba ràng buộc đặt đúng một lần cho mọi tool:

  1. Tên tool phải có thật  - LLM không gọi được thứ không tồn tại.
  2. Tham số phải hợp lệ    - sai tên là chặn ngay, không "đoán ý" rồi chạy nhầm.
  3. Kết quả là dữ liệu thuần - `AggregateResult` được đổi về dict, nên mọi con số
     đi vào prompt đều đối chiếu ngược lại được.

`app.tools.data.call_tool` vẫn tồn tại song song và nhận sẵn một `AsyncSession`:
node của workflow 4 dùng nó vì cần đối tượng `AggregateResult` (còn `.is_consistent`,
`.metrics`) chứ không phải dict. Registry này thì tự mở phiên CSDL, dành cho người
gọi chỉ cần kết quả.
"""

from __future__ import annotations

import logging
from typing import Any

from app.db.erp_session import erp_session_scope
from app.db.session import session_scope
from app.tools import data as data_tools
from app.tools.base import ToolError, ToolSpec
from app.tools.document import analyze_document, generate_docx
from app.tools.presentation import generate_presentation
from app.tools.rag import get_document, search_documents
from app.tools.templates import get_template

logger = logging.getLogger(__name__)


TOOLS: dict[str, ToolSpec] = {
    # --- tra cứu tài liệu (workflow 1) ---
    "search_documents": ToolSpec(
        name="search_documents",
        description="Tìm các đoạn tài liệu liên quan tới câu hỏi trong kho tri thức "
                    "(hybrid 3 nhánh + rerank), trả về kèm nguồn trích dẫn",
        parameters={"query": "câu hỏi hoặc từ khoá, bắt buộc",
                    "document_type": "lọc theo loại tài liệu; bỏ trống = toàn kho",
                    "top_n": "số đoạn trả về; bỏ trống = theo cấu hình",
                    "doc_ids": "giới hạn trong các tài liệu này"},
        func=search_documents,
    ),
    "get_document": ToolSpec(
        name="get_document",
        description="Đọc lại nguyên văn một tài liệu theo doc_id trong kho hoặc số ký "
                    "hiệu trong sổ văn bản",
        parameters={"document_id": "doc_id hoặc số ký hiệu văn bản, bắt buộc",
                    "max_chars": "giới hạn độ dài trả về"},
        func=get_document,
    ),
    # --- xử lý và sinh văn bản (workflow 2, 3) ---
    "analyze_document": ToolSpec(
        name="analyze_document",
        description="Soát một tài liệu đã tải lên: cấu trúc, chữ nghĩa, phân loại, "
                    "đề xuất phòng ban xử lý và phân rã nhiệm vụ",
        parameters={"file_id": "định danh file đã tải lên, bắt buộc",
                    "noi_gui": "nơi gửi văn bản, giúp bước định tuyến chính xác hơn"},
        func=analyze_document,
    ),
    "get_template": ToolSpec(
        name="get_template",
        description="Tra mẫu báo cáo theo mã, theo loại hoặc theo tên; trả về cấu trúc "
                    "các mục và những trường bắt buộc người dùng nhập",
        parameters={"template_type": "mã mẫu, loại báo cáo hoặc tên gọi, bắt buộc"},
        func=get_template,
    ),
    "generate_docx": ToolSpec(
        name="generate_docx",
        description="Đổ nội dung đã chốt ra file .docx theo mẫu - không tự thêm số liệu",
        parameters={"template_id": "mã mẫu; để trống thì dựng bằng code",
                    "content": "{meta: {...}, sections: [{id, title, paragraphs, table}]}",
                    "filename": "tên file mong muốn, không bắt buộc"},
        func=generate_docx,
        side_effect=True,
    ),
    # --- số liệu nghiệp vụ (workflow 4) ---
    **data_tools.TOOLS,
    # --- trình chiếu (workflow 5) ---
    "generate_presentation": ToolSpec(
        name="generate_presentation",
        description="Dựng bộ slide .pptx từ đặc tả JSON; biểu đồ do code vẽ, truyền "
                    "riêng chứ không nằm trong đặc tả",
        parameters={"data": "{title, subtitle, slides: [{kind, title, bullets, ...}]}",
                    "template_id": "mẫu .pptx để kế thừa theme, không bắt buộc",
                    "filename": "tên file mong muốn, không bắt buộc"},
        func=generate_presentation,
        side_effect=True,
    ),
}


def tool_schemas(names: list[str] | None = None) -> list[dict[str, Any]]:
    """Schema gửi cho model ở trường `tools` của API chat completions."""
    return [TOOLS[n].to_json_schema() for n in (names or TOOLS) if n in TOOLS]


def is_side_effect(name: str) -> bool:
    spec = TOOLS.get(name)
    return bool(spec and spec.side_effect)


def tool_names() -> list[str]:
    return list(TOOLS)


def describe_tools(names: list[str] | None = None) -> str:
    """Mô tả tool để nhúng vào prompt."""
    specs = [TOOLS[n] for n in (names or TOOLS) if n in TOOLS]
    return "\n".join(spec.describe() for spec in specs)


def catalog() -> list[dict[str, Any]]:
    """Danh mục tool dạng dữ liệu - cho API và giao diện hiển thị."""
    return [
        {"name": spec.name, "description": spec.description,
         "parameters": spec.parameters, "reads_database": spec.needs_session}
        for spec in TOOLS.values()
    ]


def _plain(result: Any) -> Any:
    """Đưa kết quả về dữ liệu thuần để nhúng được vào prompt và JSON response."""
    if hasattr(result, "as_dict"):
        return result.as_dict()
    if isinstance(result, list):
        return [_plain(item) for item in result]
    return result


async def call_tool(name: str, **kwargs: Any) -> Any:
    """Gọi tool theo tên. Tên lạ, tham số lạ đều bị chặn tại đây."""
    spec = TOOLS.get(name)
    if spec is None:
        raise ToolError(f"Không có công cụ tên {name!r}. Chỉ dùng được: {', '.join(TOOLS)}")

    kwargs = data_tools.resolve_aliases(spec, kwargs)
    unknown = set(kwargs) - set(spec.parameters)
    if unknown:
        raise ToolError(f"{name}: tham số không hợp lệ {sorted(unknown)}")

    if spec.needs_session:
        # Tool số liệu đọc ERP; phiên ERP chặn ghi ngay tại engine.
        scope = erp_session_scope if name in data_tools.TOOLS else session_scope
        async with scope() as session:
            return _plain(await spec.func(session, **kwargs))
    return _plain(await spec.func(**kwargs))

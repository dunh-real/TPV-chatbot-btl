"""Công cụ truy vấn ERP bằng câu hỏi tự nhiên qua engine Text-to-SQL discovery."""
from app.services.erp_discovery import query_erp
from app.tools.base import ToolSpec

TOOLS = {
    "query_erp": ToolSpec(
        name="query_erp",
        description="Truy vấn dữ liệu ERP bằng câu hỏi nghiệp vụ. Hệ thống tự khám phá schema, "
                    "kiểm tra quyền bảng/cột/dòng, sinh SQL chỉ đọc và trả QueryResult. "
                    "Nêu đủ chỉ tiêu, kỳ, phạm vi, cách nhóm và thứ tự mong muốn; "
                    "không tự suy ra bảng/cột. Kết quả bị cắt chỉ dùng để xem trước.",
        parameters={"question": "Câu hỏi đầy đủ về dữ liệu ERP, bắt buộc"},
        func=query_erp,
    ),
}

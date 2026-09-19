from __future__ import annotations

from typing import Any

from src.services.presentation.markdown_parser import SourceGraph
from src.services.presentation.source_compactor import compact_source_json


def slide_range(options: dict[str, Any]) -> tuple[int, int]:
    value = options.get("slide_count", {})
    if isinstance(value, dict):
        minimum = int(value.get("min", 5))
        maximum = int(value.get("max", 8))
    else:
        minimum, maximum = 5, 8
    minimum = max(2, minimum)
    return minimum, max(minimum, maximum)


def build_planner_prompt(
    source_graph: SourceGraph,
    options: dict[str, Any] | None = None,
    source_context: str | None = None,
) -> str:
    opts = options or {}
    minimum, maximum = slide_range(opts)
    audience = opts.get("audience", "ban lãnh đạo / người nghe chung")
    purpose = opts.get("purpose", "báo cáo / thuyết trình")
    language = opts.get("language", "vi")
    max_block = int(opts.get("max_text_chars_per_block", 650))
    max_slide = int(opts.get("max_text_chars_per_slide", 900))
    min_visuals = int(opts.get("min_visual_slides", 1 if maximum >= 6 else 0))
    context = source_context or compact_source_json(source_graph)
    divider_rule = (
        "Có thể dùng cover làm section divider khi thực sự cần."
        if opts.get("allow_section_dividers", False)
        else "Chỉ slide đầu tiên được dùng kind='cover'."
    )

    return f"""Bạn là Presentation Content Planner. Hãy tạo DeckSpec ngắn gọn, có mạch kể chuyện và bám sát nguồn.

Cấu hình:
- Tiêu đề: {source_graph.title}
- Ngôn ngữ: {language}
- Đối tượng: {audience}
- Mục đích: {purpose}
- Số slide: {minimum}-{maximum}

Quy tắc:
- Slide 1 là cover, id='s01', title là tiêu đề deck. {divider_rule}
- Mỗi slide có một ý chính; không tạo slide chỉ để lặp heading.
- Không lặp title ở hai slide liên tiếp.
- Ưu tiên 3-5 bullet ngắn. TextBlock <= {max_block} ký tự; tổng text/slide <= {max_slide} ký tự.
- Chỉ dùng chart/table khi nguồn có dữ liệu phù hợp; không bịa số liệu hoặc quan hệ nhân quả.
- Dùng DiagramBlock để diễn đạt cấu trúc rõ hơn: timeline cho 4-7 bước tuần tự; three_columns cho 3 nhóm;
  hub_spoke cho một ý trung tâm và 4-6 nhánh; relationship cho quan hệ trái-trung tâm-phải;
  paired_grid cho 4-6 chủ đề ngang hàng.
- Tạo ít nhất {min_visuals} slide trực quan bằng diagram/chart/table/image_text khi nội dung phù hợp.
- Mỗi DiagramItem có title <= 50 ký tự, description <= 120 ký tự. Không ép sơ đồ nếu nội dung không phù hợp.
- Giữ nguyên số liệu, identifier kỹ thuật và ý quan trọng.
- ID slide/block phải duy nhất, tăng tuần tự. Chỉ trả về JSON đúng schema.

Source context:
{context}"""

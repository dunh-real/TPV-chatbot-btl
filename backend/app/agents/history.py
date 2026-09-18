"""Dựng khối lịch sử hội thoại để nhét vào prompt.

Một chỗ duy nhất vì cả năm workflow đều cần cùng một định dạng: đổi cách hiển thị
ở đây là đổi cho tất cả, không phải đi sửa từng node.
"""

from __future__ import annotations

# Bốn lượt (hai vòng hỏi-đáp) đủ để giải "số liệu đó", "đơn vị đó". Dài hơn thì
# bước trích tham số bắt đầu bám vào kỳ báo cáo của lượt cũ đã hết liên quan.
DEFAULT_TURNS = 4

_ROLE_VI = {"user": "Người dùng", "assistant": "Trợ lý"}

EMPTY = "(chưa có)"


def format_history(history: list[dict[str, str]] | None, turns: int = DEFAULT_TURNS) -> str:
    recent = (history or [])[-turns:] if turns > 0 else []
    if not recent:
        return EMPTY
    return "\n".join(
        f"{_ROLE_VI.get(m.get('role', ''), m.get('role', ''))}: {m.get('content', '')}"
        for m in recent
    )

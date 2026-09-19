"""Kênh phát tiến trình: cho người dùng thấy agent đang làm gì, ngay lúc nó làm.

Một yêu cầu nhiều bước chạy 8-30 giây. Suốt thời gian đó giao diện cũ chỉ có một
vòng xoay, nên người dùng không phân biệt được "đang tổng hợp số liệu" với "đã
treo". Tệ hơn: họ không thấy agent đã tra ở đâu, nên câu trả lời cuối trông như
rút từ trong mũ ra.

Cách nối dây: một `ContextVar` giữ hàm phát. Node nào muốn báo thì gọi `emit`,
không cần biết ai đang nghe và không phải nhận thêm tham số. Không ai nghe thì
`emit` là lệnh rỗng - đường chạy cũ (`POST /api/agent/chat`) không đổi một dòng
nào và không trả giá gì.

    endpoint SSE ──> collecting(queue.put_nowait) ──> run_agent
                                                        ├─ planner  -> emit("plan")
                                                        ├─ _run_step-> emit("step_start")
                                                        └─ toolloop -> emit("step_progress")

ContextVar đi theo task con khi `asyncio.gather` tạo task, nên các bước chạy song
song vẫn phát về đúng một hàng đợi.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger(__name__)

Emitter = Callable[[str, dict[str, Any]], None]

_emitter: ContextVar[Emitter | None] = ContextVar("agent_progress_emitter", default=None)

# Các loại sự kiện. Giữ ở một chỗ để backend và giao diện không lệch tên nhau.
#
# Cố tình KHÔNG có sự kiện nào mang kết quả công cụ. Số liệu thô chưa qua van đối
# chiếu của `check_numbers`; đẩy nó lên màn hình là mời người đọc tin vào con số
# mà chính hệ thống chưa xác nhận. Tiến trình chỉ nói agent đang làm GÌ, còn nói
# số liệu bao nhiêu là việc của câu trả lời cuối.
#
# `step_progress` mang TÊN công cụ chứ không mang tham số hay kết quả. Tên là
# nhãn việc ("đang tra quân số"), không phải dữ liệu - mà thiếu nó thì một vòng
# lặp công cụ 90 giây chỉ hiện đúng một dòng "đang chạy…", tức là quay về đúng
# cái vòng xoay mà tính năng này sinh ra để thay thế.
EVENTS = (
    "plan",         # kế hoạch đã lập xong: các bước và phụ thuộc
    "thinking",     # suy luận thô của model - hiện được, không phải câu trả lời
    "step_start",   # một bước bắt đầu chạy
    "step_progress",# nhịp tim trong một bước dài: lượt thứ mấy, đang gọi công cụ nào
    "step_retry",   # bước không ra kết quả, đang đổi nghiệp vụ chạy lại
    "step_done",    # một bước xong
    "answer_delta", # một mảnh câu trả lời, ngay lúc model sinh ra nó
    "answer_reset", # chữ vừa phát là câu dẫn trước khi gọi tool - xoá đi
    "done",         # toàn bộ xong, kèm payload đầy đủ
    "error",
)


def emit(event: str, **data: Any) -> None:
    """Báo một sự kiện tiến trình. Không ai nghe thì đây là lệnh rỗng.

    Không bao giờ ném lỗi: tiến trình là thứ phụ trợ, hỏng nó mà làm chết cả yêu
    cầu thì đổi một phiền toái nhỏ lấy một phiền toái lớn.
    """
    fn = _emitter.get()
    if fn is None:
        return
    try:
        fn(event, data)
    except Exception:  # noqa: BLE001
        logger.debug("Phát tiến trình %r thất bại", event, exc_info=True)


@contextmanager
def collecting(fn: Emitter) -> Iterator[None]:
    """Bật kênh phát trong phạm vi khối `with`."""
    token = _emitter.set(fn)
    try:
        yield
    finally:
        _emitter.reset(token)


class Channel:
    """Hàng đợi sự kiện cho một yêu cầu, kèm hàm phát để đưa vào `collecting`.

    Có trần: mất vài sự kiện khi giao diện đọc chậm vẫn hơn là để bộ nhớ phình
    theo một vòng lặp công cụ chạy dài. Trần rộng vì `answer_delta` về rất dày -
    một câu trả lời 1500 ký tự là khoảng 500 mảnh trong vài giây. Mất mảnh chỉ
    làm chữ đang chảy bị khuyết một thoáng: `done` mang câu trả lời đầy đủ và
    giao diện dựng lại từ đó, nên không có gì sai sót đọng lại.
    """

    def __init__(self, maxsize: int = 4096) -> None:
        self.queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue(maxsize)
        self.dropped = 0

    def put(self, event: str, data: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait((event, data))
        except asyncio.QueueFull:
            self.dropped += 1

    def close(self) -> None:
        """Đánh dấu hết sự kiện; bên đọc thấy `None` thì dừng."""
        try:
            self.queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

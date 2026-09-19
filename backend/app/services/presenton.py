"""Client gọi Presenton - công cụ dựng bộ slide chạy trong Docker.

    tóm tắt số liệu (do code dựng) ──> Presenton ──> .pptx tải về data/output

VÌ SAO DÙNG PRESENTON THAY VÌ TỰ DỰNG

Bản tự dựng (`app.documents.pptx_builder`) vẫn còn và vẫn là đường lùi, nhưng nó
để LLM viết gạch đầu dòng, và ba lần chạy liên tiếp đều ra những câu như "Phòng
Kinh doanh và Kỹ thuật thiếu số liệu" - tên đơn vị model tự chọn, không có trong
dữ liệu. Presenton nhận một bản tóm tắt số liệu ĐÃ CHỐT và chỉ làm việc trình
bày, nên phần bịa đặt thu hẹp lại đúng bằng phần chữ dẫn dắt.

RANH GIỚI KHÔNG ĐỔI

Số liệu vẫn do SQL lấy và do `app.tools.data` tính. Presenton không được nối vào
CSDL, không nhận câu hỏi gốc của người dùng, chỉ nhận bản tóm tắt. Và bộ slide nó
trả về vẫn bị đối chiếu số một lần nữa trước khi giao cho người dùng.

XÁC THỰC

Bản Presenton mới bắt đăng nhập trên `/api/v1/*` và trả về **cookie phiên**
(`presenton_session`), không phải bearer token. `httpx.AsyncClient` tự giữ cookie
nên chỉ cần đăng nhập một lần cho mỗi client; phiên hết hạn thì lần gọi sau nhận
401/428 và ta đăng nhập lại đúng một lần.

Tài khoản được tạo lần đầu qua `/api/v1/auth/setup` - xem `docker/presenton.yml`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Mã Presenton trả về khi chưa có tài khoản nào được tạo.
_SETUP_REQUIRED = 428


class PresentonError(RuntimeError):
    """Không dựng được bộ slide bằng Presenton (mạng, xác thực, hoặc render hỏng)."""


@dataclass(slots=True)
class Deck:
    """Bộ slide Presenton vừa dựng xong."""

    presentation_id: str
    content: bytes
    remote_path: str
    edit_path: str = ""
    elapsed_seconds: float = 0.0


class PresentonClient:
    def __init__(self, settings: Settings | None = None,
                 client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings or get_settings()
        # Timeout của TỪNG request, không phải của cả lượt dựng slide: việc dựng
        # kéo dài vài phút và được theo dõi bằng vòng poll bên dưới.
        self._client = client or httpx.AsyncClient(
            base_url=self.settings.presenton_url.rstrip("/"),
            timeout=httpx.Timeout(60.0, connect=10.0),
            follow_redirects=True,
        )
        self._logged_in = False

    async def close(self) -> None:
        await self._client.aclose()

    # ---------------------------------------------------------------- auth --
    async def _login(self, *, force: bool = False) -> None:
        if self._logged_in and not force:
            return
        username = self.settings.presenton_username
        password = self.settings.presenton_password
        if not username or not password:
            # Presenton bản cũ không bắt đăng nhập; bản mới sẽ tự báo 428 ở
            # request đầu tiên và thông báo đó rõ hơn bất cứ câu đoán nào ở đây.
            self._logged_in = True
            return

        try:
            response = await self._client.post(
                "/api/v1/auth/login", json={"username": username, "password": password})
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == _SETUP_REQUIRED:
                raise PresentonError(
                    "Presenton chưa được khởi tạo tài khoản. Chạy một lần:\n"
                    f'  curl -X POST {self.settings.presenton_url}/api/v1/auth/setup '
                    f'-H "Content-Type: application/json" '
                    f"-d '{{\"username\":\"{username}\",\"password\":\"...\"}}'"
                ) from exc
            raise PresentonError(
                f"Đăng nhập Presenton thất bại ({exc.response.status_code}). "
                "Kiểm tra PRESENTON_USERNAME / PRESENTON_PASSWORD trong .env."
            ) from exc
        except httpx.HTTPError as exc:
            raise PresentonError(f"Không kết nối được Presenton: {exc}") from exc

        self._logged_in = True

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Gọi API, tự đăng nhập lại đúng một lần khi phiên hết hạn."""
        await self._login()
        response = await self._client.request(method, url, **kwargs)
        if response.status_code in (401, 403, _SETUP_REQUIRED):
            await self._login(force=True)
            response = await self._client.request(method, url, **kwargs)
        return response

    # ----------------------------------------------------------- dựng slide --
    async def generate(self, content: str, *, n_slides: int, instructions: str = "",
                       template: str | None = None, language: str = "Vietnamese") -> Deck:
        """Dựng bộ slide và tải file về dạng bytes. Chặn cho tới khi xong hoặc lỗi."""
        started = time.monotonic()
        task_id = await self._start(content, n_slides=n_slides, instructions=instructions,
                                    template=template, language=language)
        result = await self._wait(task_id)

        remote_path = str(result.get("path") or "")
        if not remote_path:
            raise PresentonError("Presenton báo xong nhưng không trả về đường dẫn file.")

        return Deck(
            presentation_id=str(result.get("presentation_id") or ""),
            content=await self._download(remote_path),
            remote_path=remote_path,
            edit_path=str(result.get("edit_path") or ""),
            elapsed_seconds=round(time.monotonic() - started, 1),
        )

    async def _start(self, content: str, *, n_slides: int, instructions: str,
                     template: str | None, language: str) -> str:
        body: dict[str, Any] = {
            "content": content,
            "n_slides": n_slides,
            "template": template or self.settings.presenton_template,
            "export_as": "pptx",
            "include_title_slide": True,
            "language": language,
        }
        if instructions:
            body["instructions"] = instructions

        try:
            response = await self._request(
                "POST", "/api/v1/ppt/presentation/generate/async", json=body)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise PresentonError(
                f"Presenton từ chối yêu cầu ({exc.response.status_code}): "
                f"{exc.response.text[:200]}") from exc
        except httpx.HTTPError as exc:
            raise PresentonError(f"Không gọi được Presenton: {exc}") from exc

        task_id = str(data.get("id") or "")
        if not task_id:
            raise PresentonError("Presenton không trả về mã tác vụ.")
        logger.info("Presenton bắt đầu dựng slide, task=%s", task_id)
        return task_id

    async def _wait(self, task_id: str) -> dict[str, Any]:
        """Hỏi trạng thái tới khi xong. Trạng thái lỗi của Presenton là "error"."""
        deadline = time.monotonic() + self.settings.presenton_max_wait_seconds
        while True:
            if time.monotonic() > deadline:
                raise PresentonError(
                    f"Presenton chưa dựng xong sau "
                    f"{int(self.settings.presenton_max_wait_seconds)}s.")
            await asyncio.sleep(self.settings.presenton_poll_interval)

            try:
                response = await self._request(
                    "GET", f"/api/v1/ppt/presentation/status/{task_id}")
                response.raise_for_status()
                payload = response.json()
            except httpx.HTTPError as exc:
                raise PresentonError(f"Mất liên lạc với Presenton khi đang dựng: {exc}") from exc

            status = str(payload.get("status") or "")
            if status == "completed":
                return payload.get("data") or payload
            if status in ("error", "failed"):
                raise PresentonError(f"Presenton dựng slide thất bại: {_ly_do(payload)}")

    async def _download(self, remote_path: str) -> bytes:
        """Tải file .pptx. Đường dẫn Presenton trả về cũng chính là đường dẫn HTTP."""
        try:
            response = await self._request("GET", "/" + remote_path.lstrip("/"))
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise PresentonError(f"Không tải được file slide từ Presenton: {exc}") from exc

        if not response.content:
            raise PresentonError("Presenton trả về file rỗng.")
        return response.content


def _ly_do(payload: dict[str, Any]) -> str:
    """Lôi câu lỗi thật ra khỏi phản hồi lỗi của Presenton.

    Chi tiết hữu ích nằm trong `error.detail` (thường là stderr của bước export),
    còn `message` chỉ nói "Presentation generation failed" - đọc xong không biết
    hỏng ở LLM hay ở khâu render.
    """
    error = payload.get("error")
    if isinstance(error, dict):
        detail = error.get("detail") or error.get("message") or ""
        if detail:
            return str(detail)[:300]
    if error:
        return str(error)[:300]
    return str(payload.get("message") or "không rõ nguyên nhân")[:300]


_client: PresentonClient | None = None


def get_presenton() -> PresentonClient:
    global _client
    if _client is None:
        _client = PresentonClient()
    return _client


async def close_presenton() -> None:
    """Đóng client dùng chung - gọi khi tắt ứng dụng hoặc kết thúc test."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None

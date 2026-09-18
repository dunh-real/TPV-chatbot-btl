"""OCR trang scan qua mô hình thị giác (VLM) phục vụ trên vLLM.

PDF thực tế thường lai: vài trang xuất từ Word (có text số hoá), vài trang là ảnh
chụp/scan. Chạy OCR toàn bộ thì chậm và làm hỏng chất lượng trang digital, nên
mỗi trang được phân loại trước và chỉ trang scan mới đi qua VLM.

Phân loại dựa trên 3 tín hiệu, cần ít nhất 2 đồng thuận mới coi là trang scan:
    1. số ký tự trích được quá ít (ngưỡng tuyệt đối)
    2. không có font nhúng (trang digital luôn nhúng font)
    3. số ký tự thấp bất thường so với trung bình của chính tài liệu đó
"""

from __future__ import annotations

import base64
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import httpx
import pypdfium2 as pdfium

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"}
OCR_PROMPT = (
    "Trích xuất toàn bộ nội dung văn bản trong ảnh và trả về dưới dạng Markdown. "
    "Bảng biểu phải được giữ nguyên cấu trúc dưới dạng bảng Markdown. "
    "Không thêm lời giải thích, chỉ trả về nội dung."
)


class OCRService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._pool: ThreadPoolExecutor | None = None
        self._semaphore: threading.Semaphore | None = None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.settings.ocr_enabled and bool(self.settings.ocr_base_url)

    def _ensure_pool(self) -> tuple[ThreadPoolExecutor, threading.Semaphore]:
        if self._pool is None:
            with self._lock:
                if self._pool is None:
                    limit = self.settings.ocr_max_concurrency
                    self._pool = ThreadPoolExecutor(max_workers=limit, thread_name_prefix="ocr")
                    self._semaphore = threading.Semaphore(limit)
        return self._pool, self._semaphore  # type: ignore[return-value]

    # ------------------------------------------------------ phân loại ----- #
    def classify_pages(self, path: Path) -> list[bool]:
        """True = trang cần OCR. Danh sách theo đúng thứ tự trang."""
        cfg = self.settings
        suffix = path.suffix.lower()
        if suffix in IMAGE_SUFFIXES:
            return [True]
        if suffix != ".pdf":
            return [False]

        try:
            pdf = pdfium.PdfDocument(str(path))
        except Exception as exc:  # noqa: BLE001 - file hỏng thì coi như cần OCR
            logger.warning("Không mở được PDF %s: %s", path.name, exc)
            return [True]

        try:
            metrics: list[tuple[int, int]] = []  # (số ký tự, số font)
            for page in pdf:
                textpage = page.get_textpage()
                try:
                    char_count = len((textpage.get_text_range() or "").strip())
                finally:
                    textpage.close()

                fonts: set[str] = set()
                try:
                    for obj in page.get_objects():
                        if obj.type == pdfium.raw.FPDF_PAGEOBJ_TEXT:
                            try:
                                name = obj.get_font().name
                            except Exception:  # noqa: BLE001
                                continue
                            if name:
                                fonts.add(name)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Không đọc được font trang: %s", exc)

                metrics.append((char_count, len(fonts)))
        finally:
            pdf.close()

        if not metrics:
            return []

        avg_chars = sum(c for c, _ in metrics) / len(metrics)
        adaptive = avg_chars * cfg.ocr_char_threshold_ratio

        needs_ocr: list[bool] = []
        for char_count, font_count in metrics:
            votes = 0
            if char_count < cfg.ocr_char_threshold_abs:
                votes += 1
            if font_count < cfg.ocr_min_fonts:
                votes += 1
            if avg_chars > 0 and char_count < adaptive:
                votes += 1
            needs_ocr.append(votes >= 2)

        logger.info(
            "Phân loại %s: %d/%d trang cần OCR (trung bình %.0f ký tự/trang)",
            path.name, sum(needs_ocr), len(needs_ocr), avg_chars,
        )
        return needs_ocr

    # ----------------------------------------------------------- OCR ------ #
    def ocr_pages(self, path: Path, page_numbers: list[int]) -> dict[int, str]:
        """OCR đúng những trang được chỉ định; trả về {số trang (0-based): markdown}."""
        if not page_numbers or not self.enabled:
            return {}

        images = self._render_pages(path, page_numbers)
        if not images:
            return {}

        pool, semaphore = self._ensure_pool()
        futures = {
            pool.submit(self._ocr_one, image, semaphore): page_number
            for page_number, image in zip(page_numbers, images, strict=True)
        }

        results: dict[int, str] = {}
        for future, page_number in futures.items():
            try:
                text = future.result(timeout=self.settings.ocr_timeout)
            except Exception as exc:  # noqa: BLE001 - một trang lỗi không dừng cả file
                logger.warning("OCR trang %d lỗi: %s", page_number + 1, exc)
                continue
            if text.strip():
                results[page_number] = text
        logger.info("OCR xong %d/%d trang của %s", len(results), len(page_numbers), path.name)
        return results

    def _render_pages(self, path: Path, page_numbers: list[int]) -> list[bytes]:
        """Render trang thành PNG; ảnh rời thì đọc thẳng file."""
        if path.suffix.lower() in IMAGE_SUFFIXES:
            return [path.read_bytes()]

        images: list[bytes] = []
        try:
            pdf = pdfium.PdfDocument(str(path))
        except Exception as exc:  # noqa: BLE001
            logger.error("Không render được %s: %s", path.name, exc)
            return []
        try:
            for page_number in page_numbers:
                bitmap = pdf[page_number].render(scale=self.settings.ocr_render_scale)
                buffer = BytesIO()
                image = bitmap.to_pil()
                try:
                    image.save(buffer, format="PNG")
                finally:
                    image.close()
                images.append(buffer.getvalue())
        finally:
            pdf.close()
        return images

    def _ocr_one(self, image_bytes: bytes, semaphore: threading.Semaphore) -> str:
        cfg = self.settings
        payload = {
            "model": cfg.ocr_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,"
                                + base64.b64encode(image_bytes).decode()
                            },
                        },
                        {"type": "text", "text": OCR_PROMPT},
                    ],
                }
            ],
            "max_tokens": cfg.ocr_max_tokens,
            "temperature": 0.0,
        }
        with semaphore:
            response = httpx.post(
                f"{cfg.ocr_base_url.rstrip('/')}/chat/completions",
                json=payload,
                timeout=cfg.ocr_timeout,
                headers={"Authorization": f"Bearer {cfg.ocr_api_key}"},
            )
            response.raise_for_status()
            data = response.json()
        return data["choices"][0]["message"]["content"] or ""


_ocr: OCRService | None = None


def get_ocr_service() -> OCRService:
    global _ocr
    if _ocr is None:
        _ocr = OCRService()
    return _ocr

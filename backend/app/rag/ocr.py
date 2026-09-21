"""OCR trang scan qua mô hình thị giác (VLM) phục vụ trên vLLM.

PDF thực tế thường lai: vài trang xuất từ Word (có text số hoá), vài trang là ảnh
chụp/scan. Chạy OCR toàn bộ thì chậm và làm hỏng chất lượng trang digital, nên
mỗi trang được phân loại trước và chỉ trang scan mới đi qua VLM.

Trước hết xét CÁI GÌ ĐƯỢC VẼ trên trang: một ảnh phủ trọn khổ giấy, bên trên
không có chữ nào nhìn thấy được, thì trang đó là ảnh chụp - kết luận ngay, bất kể
trích ra được bao nhiêu ký tự. Chỉ khi trang không rơi vào hình dạng đó mới xét
tiếp 3 tín hiệu đếm chữ, cần ít nhất 2 đồng thuận:
    1. số ký tự trích được quá ít (ngưỡng tuyệt đối)
    2. không có font nhúng (trang digital luôn nhúng font)
    3. số ký tự thấp bất thường so với trung bình của chính tài liệu đó

Vì sao phải xét hình dạng trang trước: rất nhiều PDF scan đã bị chạy qua Tesseract
rồi đóng lại kèm một lớp chữ VÔ HÌNH nằm khớp trên ảnh. Lớp chữ đó đủ dày để mọi
tín hiệu đếm chữ đều báo "trang digital", nên tài liệu trượt khỏi OCR và hệ thống
đọc thẳng bản Tesseract - thứ sai be bét đúng ở chỗ nguy hiểm nhất: số hiệu văn
bản, số điều luật, tên người. Đếm chữ không phân biệt nổi chữ tốt với chữ rác;
nhìn vào bố cục trang thì phân biệt được.
"""

from __future__ import annotations

import base64
import logging
import re
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import httpx
import pypdfium2 as pdfium
from PIL import Image

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"}
# Lời dặn cố ý NHẸ TAY ở chỗ "đọc được bao nhiêu chép bấy nhiêu".
#
# Bản trước bảo "trích xuất TOÀN BỘ nội dung". Với trang sạch thì không sao,
# nhưng gặp vùng mờ, bị che hay đóng dấu đè lên thì đó là một mệnh lệnh không
# thể hoàn thành trung thực - và model lấp đầy bằng chữ nó đoán. Trang scan mờ
# là lúc cần đúng nhất, lại là lúc mệnh lệnh đó hại nhất.
#
# Nói rõ "bỏ qua chỗ không đọc được" cho model một đường thoát trung thực, nên
# nó không phải chọn giữa việc cãi lời và việc bịa.
OCR_PROMPT = (
    "Bạn là công cụ OCR tiếng Việt, tối ưu cho Qwen-VL để đọc văn bản scan.\n\n"

    "NHIỆM VỤ:\n"
    "- Đọc ảnh và chép lại tối đa trung thành với văn bản gốc.\n"
    "- Ưu tiên tuyệt đối độ đúng của chữ, số, ngày tháng, số hiệu, tên cơ quan, "
    "họ tên, địa chỉ.\n"
    "- Giữ đúng thứ tự xuất hiện của nội dung trên trang.\n"
    "- Chỉ được phép chép lại những gì nhìn thấy trên ảnh.\n"
    "- Nếu không nhìn thấy hoặc không chắc chắn, KHÔNG được suy đoán.\n"
    "- Không cần sinh Markdown. Trả về văn bản thường, sạch, dễ đọc.\n"
    "- Không giải thích, không bình luận, không mô tả ảnh, không XML, "
    "không code fence.\n\n"

    "GIỮ BỐ CỤC KHỐI:\n"
    "- Trên trang, chữ nằm thành từng khối tách rời nhau. Chép mỗi khối thành một "
    "đoạn riêng, giữa hai đoạn để một dòng trống.\n"
    "- Hai khối nằm CẠNH NHAU theo chiều ngang (ví dụ tên cơ quan bên trái, quốc "
    "hiệu bên phải): chép trọn khối bên trái trước, xuống một dòng trống, rồi mới "
    "chép khối bên phải. Tuyệt đối không trộn xen kẽ dòng của hai khối.\n"
    "- Chữ đóng trong con dấu, khung hoặc ô viền là một khối riêng, tách khỏi "
    "phần thân.\n"
    "- Đây chỉ là quan sát vị trí chữ trên trang, không phải suy đoán ý nghĩa: "
    "không thêm nhãn, không thêm tiêu đề để mô tả khối.\n\n"

    "QUY TẮC OCR:\n"
    "- Chép lại nguyên văn tối đa; không tóm tắt, không diễn giải, "
    "không viết lại theo ý hiểu.\n"
    "- Không được tự bổ sung nội dung không có trong ảnh, kể cả khi thấy thiếu.\n"
    "- Giữ nguyên dòng, đoạn, danh sách, câu đánh số, tiêu mục nếu nhìn thấy.\n"
    "- Nếu thấy bảng biểu, chép lại theo dạng văn bản giữ đủ dữ liệu; "
    "không ép sang Markdown.\n"
    "- Chuẩn hóa nhẹ các lỗi OCR rõ ràng giữa chữ và số nếu chắc chắn từ ngữ cảnh "
    "(ví dụ O/0, I/1, l/1, 2/Z, 5/S); nếu không chắc → giữ nguyên.\n"
    "- Thuật ngữ chuyên ngành phải đúng chính tả và đúng hoa/thường nếu nhìn thấy rõ.\n"
    "- Nếu một cụm khó đọc hoặc mờ → dùng [không rõ] đúng vị trí.\n"
    "- Nếu mất cả dòng hoặc không thể nhận dạng → dùng [mất dòng].\n"
    "- Không suy diễn nội dung bị thiếu.\n"
    "- Không sinh thêm tiêu đề, không thêm cấu trúc mới nếu không có trên ảnh.\n\n"

    "RÀNG BUỘC CHỐNG HALLUCINATION:\n"
    "- Tuyệt đối không tạo nội dung mới ngoài những gì nhìn thấy.\n"
    "- Không lặp lại chuỗi vô nghĩa, không sinh ký tự bất thường.\n"
    "- Nếu nội dung ngắn hoặc thiếu, vẫn giữ nguyên, không được kéo dài.\n"
    "- Khi không chắc chắn, ưu tiên giữ nguyên hoặc đánh dấu [không rõ], "
    "KHÔNG đoán.\n\n"

    "ĐẦU RA MONG MUỐN:\n"
    "- Chỉ trả về nội dung OCR cuối cùng.\n"
    "- Văn bản thường, xuống dòng rõ ràng, giữ bố cục logic của trang.\n"
    "- Không thêm bất kỳ câu dẫn nhập hoặc kết luận nào."
)


@dataclass(frozen=True)
class DoTrang:
    """Số đo của một trang PDF, dùng để quyết định có OCR trang đó không."""

    so_ky_tu: int
    so_font: int
    # Phần diện tích trang bị ẢNH lớn nhất phủ (0..1).
    ti_le_anh: float
    # Có ít nhất một đối tượng chữ được vẽ ở chế độ nhìn thấy được.
    co_chu_nhin_thay: bool

    def anh_phu_trang(self, nguong: float) -> bool:
        return self.ti_le_anh >= nguong


# Vài model vẫn nhả khối suy luận dù đã tắt qua chat_template_kwargs, và khi
# `--reasoning-parser` không tách được thì khối đó chảy thẳng vào nội dung trang.
_THE_THINKING = re.compile(r"<(thinking|think)>.*?</\1>", re.DOTALL | re.IGNORECASE)
_THE_THINKING_HO = re.compile(r"<(thinking|think)>.*", re.DOTALL | re.IGNORECASE)


def go_the_suy_luan(text: str) -> str:
    """Bỏ khối <thinking>/<think> lọt vào kết quả OCR."""
    text = _THE_THINKING.sub("", text)
    text = _THE_THINKING_HO.sub("", text)
    return re.sub(r"</?(thinking|think)>", "", text, flags=re.IGNORECASE).strip()


def go_rao_markdown(text: str) -> str:
    """Bỏ cặp ```...``` mà model quấn quanh TOÀN BỘ câu trả lời.

    Prompt đã dặn "chỉ trả về nội dung", nhưng model vẫn hay đóng gói câu trả lời
    thành một khối mã - thói quen của mọi model chat khi được hỏi xin Markdown.
    Để nguyên thì cả trang văn bản biến thành một khối code: tiêu đề không còn là
    tiêu đề, bảng không còn là bảng, và bước chunk theo cấu trúc phía sau cũng mất
    luôn chỗ bám.

    Chỉ gỡ khi dòng đầu MỞ rào và dòng cuối ĐÓNG rào - đúng hình dạng "cả câu trả
    lời nằm trong một khối". Rào nằm giữa bài là khối mã thật của tài liệu, giữ nguyên.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text

    lines = stripped.split("\n")
    if len(lines) < 2 or lines[-1].strip() != "```":
        return text
    # Dòng mở phải là rào trơn hoặc rào kèm tên ngôn ngữ, không phải ``` giữa câu.
    if lines[0].strip().removeprefix("```").strip().count(" ") > 0:
        return text
    return "\n".join(lines[1:-1]).strip()


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
            metrics = [self._do_trang(page) for page in pdf]
        finally:
            pdf.close()

        if not metrics:
            return []

        avg_chars = sum(m.so_ky_tu for m in metrics) / len(metrics)
        adaptive = avg_chars * cfg.ocr_char_threshold_ratio

        needs_ocr: list[bool] = []
        so_trang_anh = 0
        for m in metrics:
            # Ảnh phủ kín trang + không chữ nào nhìn thấy được = trang chụp.
            # Chốt luôn, không cho tín hiệu đếm chữ lật lại: lớp chữ vô hình của
            # Tesseract nằm đúng ở đây và nó dư sức thắng cả 3 phiếu kia.
            if m.anh_phu_trang(cfg.ocr_scan_image_coverage) and not m.co_chu_nhin_thay:
                so_trang_anh += 1
                needs_ocr.append(True)
                continue

            votes = 0
            if m.so_ky_tu < cfg.ocr_char_threshold_abs:
                votes += 1
            if m.so_font < cfg.ocr_min_fonts:
                votes += 1
            if avg_chars > 0 and m.so_ky_tu < adaptive:
                votes += 1
            needs_ocr.append(votes >= 2)

        logger.info(
            "Phân loại %s: %d/%d trang cần OCR (%d trang là ảnh chụp, "
            "trung bình %.0f ký tự/trang)",
            path.name, sum(needs_ocr), len(needs_ocr), so_trang_anh, avg_chars,
        )
        return needs_ocr

    @staticmethod
    def _do_trang(page) -> "DoTrang":  # noqa: ANN001 - pdfium.PdfPage
        """Đo một trang: đếm chữ, đếm font, và xem trang được vẽ bằng gì."""
        textpage = page.get_textpage()
        try:
            so_ky_tu = len((textpage.get_text_range() or "").strip())
        finally:
            textpage.close()

        try:
            dien_tich = max(page.get_width() * page.get_height(), 1.0)
        except Exception:  # noqa: BLE001
            dien_tich = 1.0

        fonts: set[str] = set()
        phu_lon_nhat = 0.0
        co_chu_nhin_thay = False
        try:
            for obj in page.get_objects():
                if obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE:
                    try:
                        left, bottom, right, top = obj.get_bounds()
                    except Exception:  # noqa: BLE001
                        continue
                    phan = abs((right - left) * (top - bottom)) / dien_tich
                    phu_lon_nhat = max(phu_lon_nhat, phan)
                elif obj.type == pdfium.raw.FPDF_PAGEOBJ_TEXT:
                    try:
                        che_do = pdfium.raw.FPDFTextObj_GetTextRenderMode(obj.raw)
                    except Exception:  # noqa: BLE001
                        che_do = pdfium.raw.FPDF_TEXTRENDERMODE_UNKNOWN
                    if che_do != pdfium.raw.FPDF_TEXTRENDERMODE_INVISIBLE:
                        co_chu_nhin_thay = True
                    # get_base_name() chứ không phải .name: bản pypdfium2 đang dùng
                    # không có thuộc tính đó, nên vòng try cũ nuốt lỗi và MỌI trang
                    # đều bị đếm 0 font - phiếu "không có font nhúng" luôn trúng.
                    try:
                        ten = obj.get_font().get_base_name()
                    except Exception:  # noqa: BLE001
                        continue
                    if ten:
                        fonts.add(ten)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Không đọc được đối tượng trên trang: %s", exc)

        return DoTrang(
            so_ky_tu=so_ky_tu,
            so_font=len(fonts),
            ti_le_anh=phu_lon_nhat,
            co_chu_nhin_thay=co_chu_nhin_thay,
        )

    # ----------------------------------------------------------- OCR ------ #
    def ocr_pages(self, path: Path, page_numbers: list[int]) -> dict[int, str]:
        """OCR đúng những trang được chỉ định; trả về {số trang (0-based): markdown}."""
        results = {
            page_number: text
            for page_number, text in self.ocr_pages_stream(path, page_numbers)
            if text.strip()
        }
        if page_numbers:
            logger.info("OCR xong %d/%d trang của %s", len(results), len(page_numbers), path.name)
        return results

    def ocr_pages_stream(
        self, path: Path, page_numbers: list[int]
    ) -> Iterator[tuple[int, str]]:
        """Như `ocr_pages` nhưng trả từng trang NGAY khi trang đó xong.

        Hai khác biệt so với việc gom cả mẻ rồi mới trả:

        1. Người xem thấy trang đầu sau vài giây thay vì nhìn màn hình trống suốt
           cả phút - và đường công khai qua Cloudflare cắt ở 125s, nên dồn tất cả
           vào một phản hồi duy nhất ở cuối là tự chuốc lỗi 524 với tài liệu dài.
        2. Ảnh render theo từng mẻ đúng bằng mức chạy song song, thay vì render cả
           tài liệu vào RAM trước. Trang A4 300 DPI nặng vài MB, một file 100
           trang gom hết một lượt là vài trăm MB không để làm gì.

        Trang lỗi bị bỏ qua (có ghi log), không làm hỏng cả tài liệu. Thứ tự trả
        về theo trang nào xong trước, nên nơi gọi phải tự sắp lại nếu cần.
        """
        if not page_numbers or not self.enabled:
            return

        pool, semaphore = self._ensure_pool()
        # Mẻ đúng bằng mức chạy song song: cả mẻ cùng khởi hành, không trang nào
        # phải xếp hàng chờ semaphore - nhờ vậy hạn giờ dưới đây mới đúng nghĩa
        # "một trang quá lâu", chứ không phải "cả hàng đợi quá lâu".
        batch_size = max(1, self.settings.ocr_max_concurrency)

        for start in range(0, len(page_numbers), batch_size):
            group = page_numbers[start:start + batch_size]
            images = self._render_pages(path, group)
            if not images:
                continue

            futures = {
                pool.submit(self._ocr_one, image, semaphore): page_number
                for page_number, image in zip(group, images, strict=True)
            }
            try:
                for future in as_completed(futures, timeout=self.settings.ocr_timeout + 30):
                    page_number = futures[future]
                    try:
                        text = future.result()
                    except Exception as exc:  # noqa: BLE001 - một trang lỗi không dừng cả file
                        logger.warning("OCR trang %d lỗi: %s", page_number + 1, exc)
                        continue
                    yield page_number, text
            except TimeoutError:
                treo = [futures[f] for f in futures if not f.done()]
                logger.warning("OCR quá hạn ở các trang: %s",
                               ", ".join(str(p + 1) for p in treo))

    def _render_pages(self, path: Path, page_numbers: list[int]) -> list[bytes]:
        """Render trang thành JPEG đã resize; ảnh rời cũng chuẩn hoá như PDF.

        OCR_HVKS render 300 DPI rồi co cạnh dài về ~1568 px trước khi gọi Qwen-VL.
        Gửi ảnh A4 300 DPI nguyên cỡ buộc server/model tự downsample, vừa tốn
        prefill vừa dễ làm chữ nhỏ bị xử lý kém ổn định hơn.
        """
        if path.suffix.lower() in IMAGE_SUFFIXES:
            try:
                with Image.open(BytesIO(path.read_bytes())) as image:
                    return [self._encode_image(image)]
            except Exception as exc:  # noqa: BLE001
                logger.error("Không đọc được ảnh %s: %s", path.name, exc)
                return []

        images: list[bytes] = []
        try:
            pdf = pdfium.PdfDocument(str(path))
        except Exception as exc:  # noqa: BLE001
            logger.error("Không render được %s: %s", path.name, exc)
            return []
        try:
            for page_number in page_numbers:
                bitmap = pdf[page_number].render(scale=self.settings.ocr_render_scale)
                image = bitmap.to_pil()
                try:
                    images.append(self._encode_image(image))
                finally:
                    image.close()
        finally:
            pdf.close()
        return images

    def _encode_image(self, image: Image.Image) -> bytes:
        img = image.convert("RGB")
        max_side = max(1, self.settings.ocr_image_max_side)
        width, height = img.size
        if max(width, height) > max_side:
            ratio = max_side / max(width, height)
            img = img.resize((int(width * ratio), int(height * ratio)), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=95)
        return buffer.getvalue()

    def _ocr_one(self, image_bytes: bytes, semaphore: threading.Semaphore) -> str:
        cfg = self.settings
        payload = {
            "model": cfg.ocr_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": OCR_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/jpeg;base64,"
                                + base64.b64encode(image_bytes).decode()
                            },
                        },
                    ],
                }
            ],
            "max_tokens": cfg.ocr_max_tokens,
            "temperature": cfg.ocr_temperature,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        payload["top_p"] = cfg.ocr_top_p
        # Phạt lặp token là van dự phòng khi model kẹt trên vùng ảnh mờ và nhả mãi
        # một token ("3 3 3 3 3..."). Mặc định tắt để giữ hành vi giống OCR_HVKS;
        # văn bản hành chính lặp lại thật rất nhiều ("Quyết định số...", "xã Hành
        # Thịnh"), phạt mạnh sẽ ép model đổi chữ ở đúng chỗ nó nên chép nguyên.
        #
        # Đây là phần mở rộng của vLLM, không có trong API OpenAI thuần - nên chỉ
        # gửi khi thật sự bật, để đặt về 1.0 là quay lại payload hợp lệ với mọi
        # máy chủ tương thích OpenAI.
        if cfg.ocr_repetition_penalty != 1.0:
            payload["repetition_penalty"] = cfg.ocr_repetition_penalty

        # Gọi lại khi lỗi thay vì bỏ trang. Một trang trượt giữa bản án là mất
        # hẳn một đoạn lập luận, và phía trên không có cách nào biết mà bù - chỗ
        # rẻ nhất để chịu đựng sự cố mạng là ngay tại đây. Chờ giãn dần để không
        # nện thêm vào một vLLM đang quá tải.
        url = f"{cfg.ocr_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {cfg.ocr_api_key}"}
        so_lan = max(1, cfg.ocr_max_retries)
        loi_cuoi: Exception | None = None
        for lan in range(so_lan):
            try:
                with semaphore:
                    response = httpx.post(
                        url, json=payload, timeout=cfg.ocr_timeout, headers=headers
                    )
                    response.raise_for_status()
                    data = response.json()
                noi_dung = data["choices"][0]["message"]["content"] or ""
                return go_rao_markdown(go_the_suy_luan(noi_dung))
            except Exception as exc:  # noqa: BLE001 - thử lại mọi lỗi tạm thời
                loi_cuoi = exc
                if lan < so_lan - 1:
                    logger.warning(
                        "OCR lỗi (lần %d/%d), thử lại: %s", lan + 1, so_lan, exc
                    )
                    time.sleep(1.5 * (lan + 1))

        raise RuntimeError(f"OCR thất bại sau {so_lan} lần: {loi_cuoi}") from loi_cuoi


_ocr: OCRService | None = None


def get_ocr_service() -> OCRService:
    global _ocr
    if _ocr is None:
        _ocr = OCRService()
    return _ocr

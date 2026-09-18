"""Chấm điểm lại ứng viên sau RRF bằng cross-encoder tiếng Việt.

RRF chỉ gộp thứ hạng nên không "đọc" được quan hệ câu hỏi - đoạn văn. Cross-encoder
đưa cả cặp (query, chunk) qua cùng một lượt attention nên chính xác hơn nhiều, và
đủ rẻ vì chỉ chạy trên vài chục ứng viên.

Dùng trực tiếp `transformers` thay vì `sentence_transformers.CrossEncoder`: repo
AITeamVN/Vietnamese_Reranker không kèm `tokenizer.json`/processor config nên
CrossEncoder của ST 6.x không khởi tạo được.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RerankResult:
    index: int      # vị trí trong danh sách ứng viên đầu vào
    score: float    # điểm liên quan đã đưa về [0, 1]


class CrossEncoderReranker:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._model = None
        self._tokenizer = None
        self._device = None
        self._lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            cfg = self.settings
            device = cfg.reranker_device
            if device == "cuda" and not torch.cuda.is_available():
                device = "cpu"
            logger.info("Đang nạp reranker %s trên %s", cfg.reranker_model, device)

            dtype = torch.float16 if device == "cuda" else torch.float32
            self._tokenizer = AutoTokenizer.from_pretrained(cfg.reranker_model)
            self._model = AutoModelForSequenceClassification.from_pretrained(
                cfg.reranker_model, dtype=dtype
            ).to(device).eval()
            self._device = device

    def score(self, query: str, documents: list[str]) -> list[float]:
        """Điểm liên quan trong [0, 1] cho từng cặp (query, document)."""
        if not documents:
            return []
        self._ensure_loaded()
        import torch

        cfg = self.settings
        scores: list[float] = []
        with torch.inference_mode():
            for start in range(0, len(documents), cfg.reranker_batch_size):
                batch = documents[start : start + cfg.reranker_batch_size]
                encoded = self._tokenizer(
                    [query] * len(batch),
                    batch,
                    padding=True,
                    truncation="longest_first",
                    max_length=cfg.reranker_max_length,
                    return_tensors="pt",
                ).to(self._device)
                logits = self._model(**encoded).logits.view(-1).float()
                scores.extend(torch.sigmoid(logits).cpu().tolist())
        return scores

    def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int | None = None,
        score_threshold: float | None = None,
    ) -> list[RerankResult]:
        """Xếp hạng lại và cắt còn `top_n` ứng viên vượt ngưỡng.

        Có thể trả về danh sách rỗng khi không ứng viên nào đủ liên quan - đó là
        tín hiệu để pipeline trả lời "không tìm thấy căn cứ" thay vì bịa.
        """
        results = [RerankResult(index=i, score=s) for i, s in enumerate(self.score(query, documents))]
        results.sort(key=lambda r: r.score, reverse=True)

        threshold = (
            self.settings.rerank_score_threshold if score_threshold is None else score_threshold
        )
        if threshold > 0:
            results = [r for r in results if r.score >= threshold]

        limit = top_n if top_n is not None else self.settings.rerank_top_n
        return results[:limit]


_reranker: CrossEncoderReranker | None = None
_lock = threading.Lock()


def get_reranker() -> CrossEncoderReranker:
    global _reranker
    if _reranker is None:
        with _lock:
            if _reranker is None:
                _reranker = CrossEncoderReranker()
    return _reranker

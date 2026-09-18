"""Sinh biểu diễn cho 3 nhánh tìm kiếm hybrid.

- `VietnameseM3Embedder`: một lần forward cho ra đồng thời
    * dense  (1024-d, CLS pooling + L2 normalize)  -> nhánh vector search
    * lexical (sparse, trọng số token học được)     -> nhánh index search
  Backbone là AITeamVN/Vietnamese_Embedding (fine-tune từ BGE-M3 cho tiếng Việt),
  còn đầu `sparse_linear` lấy từ BAAI/bge-m3 - hai model dùng chung kiến trúc
  XLM-R 1024-d và cùng tokenizer nên ghép được, tránh phải nạp 2 backbone 2.2GB.
- `BM25Encoder`: sparse thống kê cổ điển -> nhánh keyword search.
  Phía client chỉ tính term-frequency đã chuẩn hoá theo công thức BM25; phần IDF
  do Qdrant tự tính nhờ `Modifier.IDF` trên sparse vector.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

import torch
import torch.nn as nn
from transformers import AutoModel, AutoTokenizer

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SparseEmbedding:
    """Vector thưa dạng (indices, values) đúng định dạng Qdrant."""

    indices: list[int] = field(default_factory=list)
    values: list[float] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.indices)

    def as_dict(self) -> dict[int, float]:
        return dict(zip(self.indices, self.values, strict=True))


@dataclass(slots=True)
class HybridEmbedding:
    """Bộ biểu diễn đầy đủ của một đoạn văn bản cho cả 3 nhánh."""

    dense: list[float]
    lexical: SparseEmbedding
    bm25: SparseEmbedding


# --------------------------------------------------------------------------- #
# Dense + lexical sparse (BGE-M3 style)
# --------------------------------------------------------------------------- #
class VietnameseM3Embedder:
    """Nạp backbone tiếng Việt + đầu sparse của BGE-M3, dùng chung một forward."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._lock = threading.Lock()
        self._model: AutoModel | None = None
        self._tokenizer: AutoTokenizer | None = None
        self._sparse_linear: nn.Linear | None = None
        self._device: torch.device | None = None
        self._special_ids: set[int] = set()

    # -- khởi tạo lười: chỉ nạp model khi thực sự cần ----------------------- #
    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return

            cfg = self.settings
            device = torch.device(
                cfg.embedding_device
                if cfg.embedding_device != "cuda" or torch.cuda.is_available()
                else "cpu"
            )
            logger.info("Đang nạp embedding model %s trên %s", cfg.embedding_model, device)

            tokenizer = AutoTokenizer.from_pretrained(cfg.embedding_model)
            dtype = torch.float16 if (cfg.embedding_fp16 and device.type == "cuda") else torch.float32
            model = AutoModel.from_pretrained(cfg.embedding_model, dtype=dtype)
            model.to(device).eval()

            sparse_linear = self._load_sparse_linear(model.config.hidden_size, device, dtype)

            self._tokenizer = tokenizer
            self._model = model
            self._sparse_linear = sparse_linear
            self._device = device
            self._special_ids = {
                tid for tid in tokenizer.all_special_ids if tid is not None
            }
            logger.info("Embedding model sẵn sàng (dtype=%s, sparse_linear=%s)",
                        dtype, "có" if sparse_linear is not None else "không")

    def _load_sparse_linear(
        self, hidden_size: int, device: torch.device, dtype: torch.dtype
    ) -> nn.Linear | None:
        """Tải `sparse_linear.pt` (Linear(hidden,1)) từ repo BGE-M3."""
        from huggingface_hub import hf_hub_download

        cfg = self.settings
        try:
            path = hf_hub_download(repo_id=cfg.sparse_linear_repo, filename=cfg.sparse_linear_file)
        except Exception:  # pragma: no cover - phụ thuộc mạng
            logger.exception(
                "Không tải được %s/%s - nhánh lexical sẽ bị vô hiệu hoá",
                cfg.sparse_linear_repo, cfg.sparse_linear_file,
            )
            return None

        state = torch.load(path, map_location="cpu", weights_only=True)
        layer = nn.Linear(hidden_size, 1)
        layer.load_state_dict(state)
        return layer.to(device=device, dtype=dtype).eval()

    @property
    def tokenizer(self):  # dùng lại cho chunking
        self._ensure_loaded()
        return self._tokenizer

    @property
    def has_lexical(self) -> bool:
        self._ensure_loaded()
        return self._sparse_linear is not None

    # -- encode ------------------------------------------------------------- #
    @torch.inference_mode()
    def encode(self, texts: list[str]) -> tuple[list[list[float]], list[SparseEmbedding]]:
        """Trả về (dense_vectors, lexical_sparse_vectors) cho danh sách văn bản."""
        if not texts:
            return [], []
        self._ensure_loaded()
        cfg = self.settings
        dense_out: list[list[float]] = []
        lexical_out: list[SparseEmbedding] = []

        for start in range(0, len(texts), cfg.embedding_batch_size):
            batch = texts[start : start + cfg.embedding_batch_size]
            encoded = self._tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=cfg.embedding_max_length,
                return_tensors="pt",
            ).to(self._device)

            hidden = self._model(**encoded).last_hidden_state  # [B, L, H]

            # dense: CLS pooling + L2 normalize (đúng pipeline của model gốc)
            dense = torch.nn.functional.normalize(hidden[:, 0], p=2, dim=-1)
            dense_out.extend(dense.float().cpu().tolist())

            if self._sparse_linear is None:
                lexical_out.extend(SparseEmbedding() for _ in batch)
                continue

            # lexical: relu(W·h) cho từng token, gom max theo token id
            weights = torch.relu(self._sparse_linear(hidden)).squeeze(-1)  # [B, L]
            weights = weights * encoded["attention_mask"]
            lexical_out.extend(
                self._to_sparse(ids.tolist(), w.float().cpu().tolist())
                for ids, w in zip(encoded["input_ids"], weights, strict=True)
            )

        return dense_out, lexical_out

    def _to_sparse(self, token_ids: list[int], weights: list[float]) -> SparseEmbedding:
        best: dict[int, float] = {}
        for tid, w in zip(token_ids, weights, strict=True):
            if w <= 0.0 or tid in self._special_ids:
                continue
            if w > best.get(tid, 0.0):
                best[tid] = w
        if not best:
            return SparseEmbedding()
        items = sorted(best.items())
        return SparseEmbedding(indices=[i for i, _ in items], values=[round(v, 6) for _, v in items])


# --------------------------------------------------------------------------- #
# BM25 sparse (keyword search)
# --------------------------------------------------------------------------- #
# Stopword tiếng Việt: chỉ những hư từ tần suất rất cao, giữ lại danh/động từ.
VIETNAMESE_STOPWORDS: frozenset[str] = frozenset("""
là và của có được cho trong khi này đó các những một với từ đến về theo như tại
thì mà nhưng hoặc nếu vì nên do bởi sẽ đã đang cũng rất quá lắm nữa chỉ còn
tôi bạn anh chị em họ nó chúng ta mình ông bà ai gì nào đâu sao
ở ra vào lên xuống qua lại trên dưới trước sau giữa
không chưa chẳng phải rồi ạ nhé nhỉ à ừ vâng dạ
để khi nào bao giờ thế ấy kia đây vậy nay
""".split())

_TOKEN_RE = re.compile(r"[0-9a-zA-ZÀ-ỹ]+", re.UNICODE)


def _hash_term(term: str) -> int:
    """Băm ổn định term -> uint32 id (không phụ thuộc PYTHONHASHSEED)."""
    digest = hashlib.blake2b(term.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big") & 0x7FFFFFFF


class BM25Encoder:
    """Sinh sparse vector BM25; phần IDF để Qdrant tính (Modifier.IDF)."""

    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or get_settings()
        self.k1 = cfg.bm25_k1
        self.b = cfg.bm25_b
        self.avg_len = cfg.bm25_avg_len
        self.use_bigrams = cfg.bm25_use_bigrams

    def tokenize(self, text: str) -> list[str]:
        normalized = unicodedata.normalize("NFC", text).lower()
        words = [w for w in _TOKEN_RE.findall(normalized) if len(w) > 1 or w.isdigit()]
        tokens = [w for w in words if w not in VIETNAMESE_STOPWORDS]
        if self.use_bigrams:
            # Tiếng Việt đơn âm: ghép bigram để giữ cụm từ ("hoá đơn", "bảo hiểm").
            tokens += [f"{a}_{b}" for a, b in zip(words, words[1:], strict=False)
                       if a not in VIETNAMESE_STOPWORDS or b not in VIETNAMESE_STOPWORDS]
        return tokens

    def encode_document(self, text: str) -> SparseEmbedding:
        tokens = self.tokenize(text)
        if not tokens:
            return SparseEmbedding()
        counts = Counter(tokens)
        doc_len = len(tokens)
        norm = self.k1 * (1.0 - self.b + self.b * doc_len / self.avg_len)
        scored: dict[int, float] = {}
        for term, tf in counts.items():
            value = tf * (self.k1 + 1.0) / (tf + norm)
            idx = _hash_term(term)
            # Va chạm hash hiếm nhưng vẫn xử lý: giữ trọng số lớn hơn.
            if value > scored.get(idx, 0.0):
                scored[idx] = value
        items = sorted(scored.items())
        return SparseEmbedding(indices=[i for i, _ in items], values=[round(v, 6) for _, v in items])

    def encode_query(self, text: str) -> SparseEmbedding:
        """Truy vấn chỉ cần đánh dấu term xuất hiện; IDF nhân phía Qdrant."""
        tokens = set(self.tokenize(text))
        if not tokens:
            return SparseEmbedding()
        indices = sorted({_hash_term(t) for t in tokens})
        return SparseEmbedding(indices=indices, values=[1.0] * len(indices))


# --------------------------------------------------------------------------- #
# Singleton tiện dụng
# --------------------------------------------------------------------------- #
_embedder: VietnameseM3Embedder | None = None
_bm25: BM25Encoder | None = None
_singleton_lock = threading.Lock()


def get_embedder() -> VietnameseM3Embedder:
    global _embedder
    if _embedder is None:
        with _singleton_lock:
            if _embedder is None:
                _embedder = VietnameseM3Embedder()
    return _embedder


def get_bm25_encoder() -> BM25Encoder:
    global _bm25
    if _bm25 is None:
        with _singleton_lock:
            if _bm25 is None:
                _bm25 = BM25Encoder()
    return _bm25


def embed_documents(texts: list[str]) -> list[HybridEmbedding]:
    """Sinh đủ 3 biểu diễn cho các chunk trước khi upsert vào Qdrant."""
    dense, lexical = get_embedder().encode(texts)
    bm25 = get_bm25_encoder()
    return [
        HybridEmbedding(dense=d, lexical=lx, bm25=bm25.encode_document(t))
        for d, lx, t in zip(dense, lexical, texts, strict=True)
    ]


def embed_query(text: str) -> HybridEmbedding:
    dense, lexical = get_embedder().encode([text])
    return HybridEmbedding(
        dense=dense[0],
        lexical=lexical[0],
        bm25=get_bm25_encoder().encode_query(text),
    )

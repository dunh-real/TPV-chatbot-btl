"""Cấu hình tập trung cho backend, nạp từ biến môi trường / file .env."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- app ---
    app_name: str = "TPV Chatbot Backend"
    debug: bool = False
    log_level: str = "INFO"

    # ------------------------------------------------- LLM (vLLM / OpenAI) ---
    # vLLM phục vụ OpenAI-compatible API: `vllm serve <model> --port 8000`
    llm_base_url: str = "http://localhost:8000/v1"
    llm_api_key: str = "EMPTY"
    llm_model: str = "Qwen/Qwen3.8-27B"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 8192
    llm_timeout: float = 120.0
    # Model nhỏ/nhanh cho các tác vụ phụ (viết lại truy vấn). Rỗng => dùng llm_model.
    llm_utility_model: str = ""
    # Dòng Qwen3.5/3.6 sinh một khối suy luận trước câu trả lời. Bật thì server vLLM
    # PHẢI chạy kèm `--reasoning-parser qwen3`, nếu không grammar của response_format
    # sẽ ép model xuất JSON ngay từ token đầu và giết luôn phần suy luận.
    llm_enable_thinking: bool = True
    # Hạn mức riêng cho khối suy luận, CỘNG THÊM vào max_tokens của nơi gọi. Suy luận
    # tiêu token trước khi model bắt đầu trả lời, nên nếu trừ vào cùng một hạn mức thì
    # các lời gọi đặt 300-400 token sẽ bị cắt giữa chừng và trả về rỗng.
    llm_thinking_budget: int = 8192

    # ------------------------------------------------------------ Qdrant ---
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "tpv_documents"
    # Hội thoại nằm riêng: chunk tài liệu cần 3 biểu diễn, lượt chat chỉ cần dense.
    qdrant_conversation_collection: str = "tpv_conversations"
    qdrant_timeout: float = 30.0
    # Kích thước vector dense của AITeamVN/Vietnamese_Embedding (BGE-M3 backbone).
    dense_vector_size: int = 1024

    # --------------------------------------------------------- Embedding ---
    embedding_model: str = "AITeamVN/Vietnamese_Embedding"
    # File sparse_linear.pt của BGE-M3 dùng để sinh trọng số lexical (M3 sparse).
    sparse_linear_repo: str = "BAAI/bge-m3"
    sparse_linear_file: str = "sparse_linear.pt"
    embedding_device: str = "cuda"
    embedding_batch_size: int = 16
    embedding_max_length: int = 1024
    embedding_fp16: bool = True
    # Nạp sẵn model lúc khởi động: request đầu tiên không phải chờ ~5s.
    warmup_models: bool = True

    # ---------------------------------------------------------- Reranker ---
    reranker_model: str = "AITeamVN/Vietnamese_Reranker"
    reranker_device: str = "cuda"
    reranker_batch_size: int = 8
    reranker_max_length: int = 2048

    # --------------------------------------------------------------- BM25 ---
    bm25_k1: float = 1.5
    bm25_b: float = 0.75
    bm25_avg_len: float = 256.0
    # Sinh thêm bigram âm tiết: tiếng Việt đơn âm nên unigram thường thiếu ngữ cảnh.
    bm25_use_bigrams: bool = True

    # --------------------------------------------------------------- OCR ----
    # VLM phục vụ riêng trên vLLM, ví dụ:
    #   vllm serve Qwen/Qwen3-VL-8B-Instruct --port 8001
    ocr_enabled: bool = False
    ocr_base_url: str = "http://localhost:8001/v1"
    ocr_api_key: str = "EMPTY"
    ocr_model: str = "Qwen/Qwen3-VL-8B-Instruct"
    ocr_max_concurrency: int = 8
    ocr_render_scale: float = 3.0
    ocr_timeout: float = 180.0
    ocr_max_tokens: int = 4096
    # Ngưỡng phân loại trang scan (cần >= 2/3 tín hiệu đồng thuận)
    ocr_char_threshold_abs: int = 100
    ocr_char_threshold_ratio: float = 0.3
    ocr_min_fonts: int = 1

    # ----------------------------------------------------------- Chunking ---
    # Đo bằng token của tokenizer embedding.
    chunk_min_tokens: int = 200     # nhỏ hơn mức này -> gộp vào chunk kề
    chunk_ideal_tokens: int = 700   # kích thước mong muốn khi đóng gói
    chunk_max_tokens: int = 1200    # vượt mức này -> phải cắt tiếp
    chunk_hard_cap: int = 1500      # trần tuyệt đối, cắt cưỡng bức
    chunk_overlap: int = 100

    # ---------------------------------------------------------- Retrieval ---
    retrieval_branch_limit: int = 50   # top-k mỗi nhánh trước khi RRF
    rrf_k: float = 60.0
    rrf_top_k: int = 20                # số ứng viên đưa vào reranker
    rerank_top_n: int = 4              # số chunk cuối cùng đưa vào context
    rerank_score_threshold: float = 0.1
    # Trọng số RRF cho từng nhánh: dense / lexical (M3 sparse) / bm25
    weight_dense: float = 1.0
    weight_lexical: float = 0.8
    weight_bm25: float = 0.7

    # ------------------------------------------------------ Query rewrite ---
    query_rewrite_enabled: bool = True
    query_rewrite_max_variants: int = 3
    query_rewrite_timeout: float = 20.0
    # Số lượt hội thoại gần nhất dùng để giải nghĩa đại từ ("cái đó", "nó"...).
    query_rewrite_history_turns: int = 4

    # ------------------------------------------------------------ Context ---
    context_max_chars: int = 16000
    context_language: Literal["vi", "en"] = "vi"

    # -------------------------------------------------------------- Redis ---
    redis_url: str = "redis://localhost:6379/0"
    cache_enabled: bool = True
    cache_ttl_seconds: int = 3600
    history_max_turns: int = 20

    # ------------------------------------------- CSDL nghiệp vụ (SQL Server) ---
    # SQL Server:
    #   mssql+aioodbc://user:pass@host:1433/tpv?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes
    # Demo/dev không cần server:
    #   sqlite+aiosqlite:///./data/demo.db
    database_url: str = "sqlite+aiosqlite:///./data/demo.db"
    database_echo: bool = False

    # ------------------------------------------------------------ Storage ---
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_secure: bool = False
    minio_bucket: str = "tpv-documents"

    upload_dir: str = "./data/uploads"
    output_dir: str = "./data/output"

    # NoDecode: không để pydantic-settings tự json.loads giá trị từ .env, vì
    # CORS_ORIGINS viết dạng "a,b" chứ không phải JSON. Tách chuỗi ở validator dưới.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def utility_model(self) -> str:
        return self.llm_utility_model or self.llm_model


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

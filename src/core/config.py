"""
Cấu hình hệ thống sử dụng Pydantic Settings
Đọc các biến môi trường từ file .env
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, validator
from typing import Optional
from pathlib import Path

class Settings(BaseSettings):
    """
    Cấu hình chính của ứng dụng
    Tự động đọc từ file .env
    """
    
    model_config = SettingsConfigDict(
        env_file = ".env",
        env_file_encoding = "utf-8",
        case_sensitive = False,
        extra = "ignore"
    )
    
    # ==================== QDRANT CONFIGURATION ====================
    qdrant_url: str = Field(default="http://localhost:6333", description="Qdrant server URL")
    qdrant_api_key: Optional[str] = Field(default=None, description="API key cho Qdrant Cloud (mà mình chạy local nên nô nít)")
    qdrant_collection_parent: str = Field(default="parent_chunks", description="Tên collection cho parent chunks")
    qdrant_collection_child: str = Field(default="child_chunks", description="Tên collection cho child chunks")
    
    # ==================== REDIS CONFIGURATION ====================
    redis_host: str = Field(default="localhost", description="Redis host")
    redis_port: int = Field(default=6379, description="Redis port")
    redis_db: int = Field(default=0, description="Redis database number")
    redis_password: Optional[str] = Field(default=None, description="Redis password")
    redis_conversation_ttl: int = Field(default=3600, description="TTL cho conversation trong Redis (giây)")
    
    # ==================== MODEL CONFIGURATION ====================
    # OCR Model
    ocr_model_path: str = Field(default="./models/LightOnOCR-1B", description="Đường dẫn đến model OCR")
    ocr_device: str = Field(default="cuda", description="Device để chạy OCR (cuda/cpu)")
    
    # Embedding Model
    embedding_model_name: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        description="Tên model embedding từ HuggingFace"
    )
    embedding_device: str = Field(default="cuda", description="Device để chạy embedding")
    embedding_dimension: int = Field(default=384, description="Dimension của embedding vector")
    
    # LLM Configuration
    llm_base_url: str = Field(default="http://localhost:11434", description="URL của Ollama server")
    llm_model_name: str = Field(default="llama3.1", description="Tên model LLM")
    llm_temperature: float = Field(default=0.1, description="Temperature cho LLM generation")
    llm_max_tokens: int = Field(default=2048, description="Số tokens tối đa cho response")
    
    # ==================== CHUNKING CONFIGURATION ====================
    parent_chunk_header_level: int = Field(default=2, description="Level của markdown header để chia parent chunks")
    child_chunk_size: int = Field(default=512, description="Kích thước tối đa của child chunk (ký tự)")
    child_chunk_overlap: int = Field(default=50, description="Overlap giữa các child chunks")
    
    # ==================== RETRIEVAL CONFIGURATION ====================
    top_k_children: int = Field(default=10, description="Số children chunks lấy ban đầu")
    top_k_rerank: int = Field(default=5, description="Số chunks sau khi rerank")
    
    # ==================== DATA PATHS ====================
    data_raw_path: str = Field(default="./data/raw", description="Thư mục chứa file PDF gốc")
    data_markdown_path: str = Field(default="./data/markdown", description="Thư mục chứa file markdown")
    data_vector_store_path: str = Field(default="./data/vector_store", description="Thư mục chứa vector store")
    
    # ==================== API CONFIGURATION ====================
    api_version: str = Field(default="v1", description="API version")
    api_host: str = Field(default="0.0.0.0", description="API host")
    api_port: int = Field(default=8000, description="API port")
    api_reload: bool = Field(default=False, description="Auto reload khi dev")
    
    # ==================== LOGGING ====================
    log_level: str = Field(default="INFO", description="Log level")
    log_file_path: str = Field(default="./logs/app.log", description="Đường dẫn file log")
    
    @validator("ocr_device", "embedding_device")
    def validate_device(cls, v):
        """Validate device là cuda hoặc cpu"""
        if v not in ["cuda", "cpu"]:
            raise ValueError("Device phải là 'cuda' hoặc 'cpu'")
        return v
    
    @validator("log_level")
    def validate_log_level(cls, v):
        """Validate log level"""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"Log level phải là một trong: {valid_levels}")
        return v.upper()
    
    def get_redis_url(self) -> str:
        """Tạo Redis URL từ config"""
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"
    
    def ensure_directories(self):
        """Tạo các thư mục cần thiết nếu chưa tồn tại"""
        directories = [
            self.data_raw_path,
            self.data_markdown_path,
            self.data_vector_store_path,
            Path(self.log_file_path).parent,
        ]
        
        for directory in directories:
            Path(directory).mkdir(parents=True, exist_ok=True)
    
    # class Config:
        # """Pydantic config"""
        # case_sensitive = False

# Singleton instance
settings = Settings()

# Tạo các thư mục khi import module
settings.ensure_directories()


# ==================== CONSTANTS ====================
class Constants:
    """Các hằng số không đổi trong hệ thống"""
    
    # Metadata keys
    METADATA_PARENT_ID = "parent_id"
    METADATA_DOCUMENT_ID = "document_id"
    METADATA_CHUNK_TYPE = "chunk_type"
    METADATA_SOURCE_FILE = "source_file"
    METADATA_PAGE_NUMBER = "page_number"
    METADATA_HEADER_LEVEL = "header_level"
    METADATA_CREATED_AT = "created_at"
    
    # Chunk types
    CHUNK_TYPE_PARENT = "parent"
    CHUNK_TYPE_CHILD = "child"
    
    # Redis keys
    REDIS_CONVERSATION_PREFIX = "conversation:"
    REDIS_USER_CONTEXT_PREFIX = "user_context:"
    
    # API response keys
    RESPONSE_QUESTION = "question"
    RESPONSE_ANSWER = "answer"
    RESPONSE_SOURCES = "sources"
    RESPONSE_METADATA = "metadata"
    
    # File extensions
    ALLOWED_FILE_EXTENSIONS = [".pdf"]
    MAX_FILE_SIZE_MB = 50


constants = Constants()
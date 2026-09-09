"""
Pydantic schemas cho Request/Response
Định nghĩa cấu trúc dữ liệu đầu vào/đầu ra của API
"""

from pydantic import BaseModel, Field, validator
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum


# ===================== ENUMS =======================

class ChunkType(str, Enum):
    """Loại chunk"""
    PARENT = "parent"
    CHILD = "child"

class MessageRole(str, Enum):
    """Role trong conversation"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


# ====================== CHUNK MODELS ========================

class ChunkMetadata(BaseModel):
    """Metadata của một chunk"""
    parent_id: Optional[str] = Field(None, description = "ID của parent chunk (chỉ có ở child chunk)")
    document_id: str = Field(..., description = "ID của document gốc")
    chunk_type: ChunkType = Field(..., description = "Loại chunk (parent/child)")
    source_file: str = Field(..., description = "Tên file PDF gốc")
    page_number: Optional[int] = Field(None, description = "Số trang trong PDF")
    header_level: Optional[int] = Field(None, description = "Level của header (cho parent chunk)")
    create_at: datetime = Field(default_factory = datetime.now, description = "Thời gian tạo chunk")
    
    class Config:
        use_enum_values = True


class Chunk(BaseModel):
    """Đại diện cho một chunk (parent hoặc child)"""
    id: str = Field(..., description = "Unique ID của chunk")
    content: str = Field(..., description = "Nội dung text của chunk")
    metadata: ChunkMetadata = Field(..., description = "Metadata của chunk")
    embedding: Optional[List[float]] = Field(None, description = "Vector embedding của chunk")
    
    @validator("content")
    def validate_content(cls, v):
        """Validate content không rỗng"""
        if not v or not v.strip():
            raise ValueError("Content không được rỗng")
        return v.strip()
    

class ParentChunk(Chunk):
    """Parent chunk - chunk lớn chia theo markdown header"""
    header_text: Optional[str] = Field(None, description = "Text của header")

class ChildChunk(Chunk):
    """Child chunk - chunk nhỏ chia theo semantic"""
    parent_id: str = Field(..., description = "ID của parent chunk chứa child chunk này")



# ========================= UPLOAD REQUEST/RESPONSE ====================================

class UploadFileResponse(BaseModel):
    """Response khi upload file thành công"""
    success: bool = Field(..., description = "Upload có thành công không")
    message: str = Field(..., description = "Thông báo")
    document_id: str = Field(..., description = "ID của document đã upload")
    filename: str = Field(..., description = "Tên file đã upload")
    total_parent_chunks: int = Field(..., description = "Số lượng parent chunks đã tạo")
    total_child_chunks: int = Field(..., description = "Số lượng child chunks đã tạo")
    processing_time_seconds: float = Field(..., description = "Thời gian xử lý (giây)")


# ========================== CHAT REQUEST/RESPONSE ================================

class ChatMessage(BaseModel):
    """Một tin nhắn trong conversation"""
    role: MessageRole = Field(..., description = "Vai trò (user/assistant/system)")
    content: str = Field(..., description = "Nội dung tin nhắn")
    timestamp: datetime = Field(default_factory = datetime.now, description = "Thời gian gửi")
    
    class Config:
        use_enum_values = True


class ChatRequest(BaseModel):
    """Request cho endpoint /ask"""
    question: str = Field(..., description = "Câu hỏi / tin nhắn của user", min_length = 1)
    tenant_id: str = Field(..., description = "ID của tenant (công ty/tổ chức)")
    role_id: int = Field(..., description = "Role ID để phân quyền truy cập tài liệu")
    user_id: str = Field(..., description = "ID của user (AbpUsers.Id)")
    employee_id: int = Field(default = 0, description = "ID của employee (Dms_Employee.Id)")
    is_manager: bool = Field(default = False, description = "Có phải manager không (từ Dms_WorkPosition.IsManager)")
    department_ids: List[int] = Field(default_factory = list, description = "Danh sách department IDs được phép xem (phòng mình + phòng con)")
    
    @classmethod
    @validator("question")
    def validate_question(cls, v):
        """Validate câu hỏi"""
        if len(v.strip()) < 3:
            raise ValueError("Câu hỏi phải có ít nhất 3 ký tự")
        return v.strip()
    
class Source(BaseModel):
    """Thông tin về một source (parent chunk)"""
    chunk_id: str = Field(..., description = "ID của parent chunk")
    content: str = Field(..., description="Nội dung của parent chunk")
    source_file: str = Field(..., description="Tên file PDF gốc")
    page_number: Optional[int] = Field(None, description="Số trang trong PDF")
    relevance_score: float = Field(..., description="Điểm độ liên quan", ge=0, le=1)
    header_text: Optional[str] = Field(None, description="Text của header (nếu có)")


class ChatResponse(BaseModel):
    """Response từ endpoint /ask - Đây là JSON string cuối cùng"""
    question: str = Field(..., description="Câu hỏi của user")
    answer: str = Field(..., description="Câu trả lời từ LLM")
    sources: List[Source] = Field(default_factory=list, description="Danh sách sources trích dẫn")
    conversation_id: str = Field(..., description="ID của conversation")
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Metadata bổ sung (thời gian xử lý, model used, etc.)"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "question": "Chính sách nghỉ phép của công ty là gì?",
                "answer": "Theo tài liệu nội bộ, nhân viên chính thức được hưởng 12 ngày phép năm...",
                "sources": [
                    {
                        "chunk_id": "parent_chunk_123",
                        "content": "## Chính sách nghỉ phép\n\nNhân viên chính thức: 12 ngày/năm...",
                        "source_file": "employee_handbook_2024.pdf",
                        "page_number": 15,
                        "relevance_score": 0.95,
                        "header_text": "Chính sách nghỉ phép"
                    }
                ],
                "conversation_id": "conv_abc123",
                "metadata": {
                    "processing_time_seconds": 2.5,
                    "model_used": "llama3.1",
                    "total_tokens": 450
                }
            }
        }


# ==================== CONVERSATION MODELS ====================

class Conversation(BaseModel):
    """Đại diện cho một cuộc hội thoại"""
    conversation_id: str = Field(..., description="ID của conversation")
    user_id: str = Field(..., description="ID của user")
    messages: List[ChatMessage] = Field(default_factory=list, description="Danh sách messages")
    created_at: datetime = Field(default_factory=datetime.now, description="Thời gian tạo conversation")
    updated_at: datetime = Field(default_factory=datetime.now, description="Thời gian update cuối")
    
    def add_message(self, role: MessageRole, content: str):
        """Thêm message vào conversation"""
        message = ChatMessage(role=role, content=content)
        self.messages.append(message)
        self.updated_at = datetime.now()
    
    def get_recent_messages(self, n: int = 5) -> List[ChatMessage]:
        """Lấy n messages gần nhất"""
        return self.messages[-n:] if len(self.messages) > n else self.messages


# ==================== HEALTH CHECK ====================

class HealthCheckResponse(BaseModel):
    """Response cho health check endpoint"""
    status: str = Field(..., description="Trạng thái của service")
    timestamp: datetime = Field(default_factory=datetime.now, description="Thời gian check")
    services: Dict[str, bool] = Field(
        ...,
        description="Trạng thái các services (qdrant, redis, llm)"
    )
    version: str = Field(..., description="Version của API")


# ==================== ERROR RESPONSE ====================

class ErrorResponse(BaseModel):
    """Response khi có lỗi"""
    error: str = Field(..., description="Loại lỗi")
    message: str = Field(..., description="Thông báo lỗi chi tiết")
    details: Optional[Dict[str, Any]] = Field(None, description="Chi tiết lỗi")
    timestamp: datetime = Field(default_factory=datetime.now, description="Thời gian xảy ra lỗi")
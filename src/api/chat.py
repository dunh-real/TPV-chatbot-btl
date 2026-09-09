"""
Chat Endpoint - Stateless REST API
Client gửi: question, tenant_id, role_id, user_id
"""

import logging
import traceback
from fastapi import APIRouter, HTTPException
from src.models.schemas import ChatRequest, ChatResponse, ErrorResponse

logger = logging.getLogger("uvicorn.error")
router = APIRouter()

def _get_chat_session():
    """Lấy ChatSession đã được pre-load từ main.py"""
    import main
    return main._chat_session

@router.post(
    "/ask",
    response_model = ChatResponse,
    responses = {400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def ask_question(request: ChatRequest):
    """
    Gửi câu hỏi và nhận câu trả lời từ hệ thống RAG.
    """
    logger.info(f"[ASK] Received: question='{request.question}', tenant={request.tenant_id}, role={request.role_id}, user={request.user_id}, employee={request.employee_id}, is_manager={request.is_manager}, dept_ids={request.department_ids}")
    try:
        chat_session = _get_chat_session()
        logger.info("[ASK] Calling chat_session()...")
        result, processing_time = chat_session.chat_session(
            query_input = request.question,
            tenant_id = request.tenant_id,
            access_role = request.role_id,
            employee_id = request.user_id,
            employee_db_id = request.employee_id,
            is_manager = request.is_manager,
            department_ids = request.department_ids,
        )
        
        logger.info(f"[ASK] Done in {processing_time:.2f}s, answer length = {len(result.get('answer', ''))}")
        
        return ChatResponse(
            question = request.question,
            answer = result.get("answer", ""),
            sources = [],
            conversation_id = f"{request.tenant_id}:{request.user_id}",
            metadata = {
                "processing_time_seconds": round(processing_time, 2),
                "citation": result.get("citation", ""),
                "tenant_id": request.tenant_id,
                "user_id": request.user_id,
                "role_id": request.role_id,
            },
        )
    
    except Exception as e:
        logger.error(f"[ASK] ERROR: {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code = 500, detail = f"Lỗi xử lý câu hỏi: {type(e).__name__}: {e}")
import sys
import os
import time
import logging

logger = logging.getLogger("uvicorn.error")

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from types import SimpleNamespace

from src.services.llm_service import OllamaChatLLM
from src.services.rerank_service import RerankerService
from src.services.prompt_service import PromptBuilder
from src.services.qdrant_service import VectorStoreService
from src.services.mssql_retrieval_service import MSSQLRetrievalService
from src.services.memory_service import RedisChatMemory
from src.services.report_service import is_report_request, generate_department_report

"""
Hệ thống trò chuyện:
- Input: Truy vấn được nhập từ người dùng
- Output: Hệ thống chatbot đưa ra câu trả lời dựa trên truy vấn từ nguồn tri thức hệ thống

Luồng hoạt động:
1. Người dùng nhập vào truy vấn (Query Input). API trả về các tham số sau: query_input, tenant_id, access_role, employee_id
2. Thêm ngữ cảnh cho truy vấn dựa trên lịch sử hội thoại
Query Input + Conversation History -> LLM rewrite -> Context Query
3. Embedding truy vấn:
Context Query -> Embedding Model -> Dense Vector + Sparse Vector
4. Truy vấn DB, tìm kiếm ngữ cảnh tương đồng:
Retrieval to Qdrant DB -> Context Docs (20)
5. Xếp hạng ngữ cảnh và lấy ra top ngữ cảnh tối ưu:
Context Docs (20) -> LLM reranking -> Context Docs (5)
6. Tăng cường context và đưa vào LLM để sinh ra phản hồi cuối cùng:
Context Query + Context Docs (5) -> LLM -> Final Response

Format đầu ra JSON:
{
    "tenant_id": tenant_id,
    "employee_id": employee_id,
    "query": query,
    "answer": final_answer,
    "citation": citation
}
"""

# load client service
db_client = VectorStoreService()
rerank_lient = RerankerService()
prompt_client = PromptBuilder()
memory_client = RedisChatMemory()
llm_client = OllamaChatLLM()
sql_retriever = MSSQLRetrievalService()

class ChatSession():
    def __init__(self):
        pass
    
    def chat_session(self, query_input, tenant_id, access_role, employee_id, employee_db_id = 0, is_manager = False, department_ids = None):
        first_time = time.time()
        
        query = query_input.strip()
        if department_ids is None:
            department_ids = []
        
        # 1. query input + conversation history -> llm rewrite -> context query
        logger.info("[CHAT] Step 1: Getting chat history from Redis...")
        try:
            chat_history = memory_client.get_history(tenant_id, employee_id, limit = 40)
        except Exception as e:
            logger.warning(f"[CHAT] Step 1: Redis error: {e}. Continuing without history.")
            chat_history = []
        logger.info(f"[CHAT] Step 1: Got {len(chat_history)} history messages. Contextualizing query via Ollama...")
        # context_query = memory_client.contextualize_query(query, chat_history)
        # logger.info(f"[CHAT] Step 1: Done. context_query = '{context_query[:100]}'")
        try:
            memory_client.add_message(tenant_id, employee_id, "user", query)
        except Exception as e:
            logger.warning(f"[CHAT] Step 1: Redis save error: {e}. Skipping.")
        
        # Decide if this is a department report request (personnel/assets)
        if is_report_request(query):
            logger.info("[CHAT] Detected department report request. Generating report via MSSQL data...")
            try:
                # generate report (both personnel and assets by default)
                summary_text, report_path = generate_department_report(tenant_id, employee_id, report_types=['personnel','assets'])

                # prepare answer: include short summary and path to generated file
                final_answer = summary_text + "\n\nBáo cáo chi tiết đã được tạo và lưu tại: " + report_path
                citation = 'mssql_database_result'

                # save assistant message to memory
                try:
                    memory_client.add_message(tenant_id, employee_id, 'assistant', final_answer)
                except Exception as e:
                    logger.warning(f"[CHAT] Redis save error (report): {e}. Skipping.")

                result = {
                    "tenant_id": tenant_id,
                    "employee_id": employee_id,
                    "employee_db_id": employee_db_id,
                    "is_manager": is_manager,
                    "department_ids": department_ids,
                    "query": query,
                    "answer": final_answer,
                    "citation": citation,
                    "report_file": report_path
                }

                end_time = time.time() - first_time
                return result, end_time

            except Exception as e:
                logger.error(f"[CHAT] Error generating department report: {e}")
                # fall back to normal pipeline

        # 2. hybrid search
        logger.info("[CHAT] Step 2: Hybrid search in Qdrant...")
        search_results = db_client.search_hybrid(query, tenant_id, access_role, k = 20)
        logger.info(f"[CHAT] Step 2: Done. Got {len(search_results)} results.")
        
        # 3. rerank
        logger.info("[CHAT] Step 3: Reranking...")
        top_docs = rerank_lient.rerank(query, search_results, top_k = 5)
        logger.info(f"[CHAT] Step 3: Done. Top {len(top_docs)} docs.")

        # 4. retrieve trusted data from MSSQL as a second source
        logger.info("[CHAT] Step 4: Querying SQL database for structured data...")
        sql_context = sql_retriever.retrieve_context(query)
        sql_context_text = []
        if sql_context:
            for item in sql_context:
                sql_context_text.append(item.get("content", ""))
        logger.info(f"[CHAT] Step 4: MSSQL context returned {len(sql_context_text)} rows.")

        if sql_context_text:
            for raw_context in sql_context_text:
                top_docs.append(
                    SimpleNamespace(
                        payload = {
                            "content": raw_context,
                            "src_file": "mssql_database_result",
                        }
                    )
                )
        
        # 5. llm generate
        logger.info("[CHAT] Step 5: Building prompt and calling Ollama LLM...")
        messages = prompt_client.build_chat_messages(
            query = query,
            search_results = top_docs,
            chat_history = chat_history,
            reasoning = False,
            database_context = sql_context_text,
        )
        
        response_obj, citation = llm_client.invoke(messages)
        logger.info("[CHAT] Step 4: Done. Got LLM response.")
        
        final_answer = ""
        if hasattr(response_obj, 'content'):
            final_answer = response_obj.content
        else:
            final_answer = str(response_obj)
        
        # save message to Redis
        try:
            memory_client.add_message(tenant_id, employee_id, "assistant", final_answer)
        except Exception as e:
            logger.warning(f"[CHAT] Redis save error: {e}. Skipping.")
        
        # output for backend server
        result = {
            "tenant_id": tenant_id,
            "employee_id": employee_id,
            "employee_db_id": employee_db_id,
            "is_manager": is_manager,
            "department_ids": department_ids,
            "query": query,
            "answer": final_answer,
            "citation": citation
        }
        
        end_time = time.time() - first_time
        
        return result, end_time


def main():
    chat_client = ChatSession()
    
    # API return: query_input, tenant_id, access_role, employee_id
    query_input = None
    tenant_id = None
    access_role = None
    employee_id = None
    
    chat_client.chat_session(query_input, tenant_id, access_role, employee_id)

if __name__ == "__main__":
    main()
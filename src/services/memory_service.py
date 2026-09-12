try:
    import redis
except Exception:  # pragma: no cover - optional dependency for local runtime
    redis = None

import json

try:
    import ollama
except Exception:  # pragma: no cover - optional dependency for local runtime
    ollama = None

NAME_LLM_MODEL = "qwen2.5:latest"

class RedisChatMemory:
    def __init__(self, host = 'localhost', port = 6379, db = 0, password = None, max_message = 40):
        self.redis_client = None if redis is None else redis.Redis(
            host = host,
            port = port,
            db = db,
            password = password,
            decode_responses = True,
            socket_timeout = 5,
            socket_connect_timeout = 5,
        )
        # self.ttl = 25920 # 72 hour
        self.max_message = max_message
    
    def _generate_key(self, tenant_id, employee_id):
        return f"chat_history:{tenant_id}:{employee_id}"
    
    def add_message(self, tenant_id, employee_id, role, content):
        if self.redis_client is None:
            return

        key = self._generate_key(tenant_id, employee_id)
        message = json.dumps({"role": role, "content": content}, ensure_ascii = False)
        
        self.redis_client.rpush(key, message)
        
        if self.redis_client.llen(key) > self.max_message:
            self.redis_client.ltrim(key, -self.max_message, -1)
    
    def get_history(self, tenant_id, employee_id, limit = 2):
        if self.redis_client is None:
            return []

        key = self._generate_key(tenant_id, employee_id)
        raw_history = self.redis_client.lrange(key, -limit, -1)
        
        return [json.loads(msg) for msg in raw_history]
    
    def clear_history(self, tenant_id, employee_id):
        if self.redis_client is None:
            return

        key = self._generate_key(tenant_id, employee_id)
        self.redis_client.delete(key)
    
    def contextualize_query(self, user_query, chat_history):
        if not chat_history:
            return user_query
        
        # json -> string
        history_str = ""
        for msg in chat_history:
            role = "user" if msg['role'] == 'user' else "assistant"
            history_str += f"{role}: {msg['content']}\n"
        
        context_prompt = f"""
        ### VAI TRÒ
        Bạn là một công cụ tiền xử lý (Pre-processor) cho hệ thống tìm kiếm Vector.
        Nhiệm vụ: Chuyển câu hỏi từ người dùng (user_query) thành một câu truy vấn ĐỘC LẬP (Standalone Query) dựa trên lịch sử trò chuyện được cung cấp.
        
        ### QUY TẮC NGHIÊM NGẶT
        1. KHÔNG TRẢ LỜI CÂU HỎI. Bạn không phải là trợ lý ảo ở bước này.
        2. KHÔNG GIẢI THÍCH. Không thêm "Câu hỏi được viết lại là...", không thêm dấu ngoặc kép.
        3. NẾU CÂU HỎI LÀ DẠNG LỜI CHÀO, CẢM ƠN, HAY TRÒ CHUYỆN XÃ GIAO: Giữ nguyên văn câu hỏi đầu vào từ người dùng.
        4. NẾU CÂU HỎI ĐÃ ĐỦ Ý: Giữ nguyên văn câu hỏi đầu vào từ người dùng.
        5. CHỈ VIẾT LẠI KHI: Có đại từ (nó, họ, đó, ông ấy...) hoặc thiếu chủ thể mà lịch sử đã nhắc tới.
        
        ### VÍ DỤ (FEW-SHOT)
        - Lịch sử: [user: Quy trình xin nghỉ phép là gì?, assistant: Bạn cần điền form A.]
        Câu hỏi mới: "Nó nộp ở đâu?"
        Kết quả: Quy trình nộp form xin nghỉ phép ở đâu?
        
        - Lịch sử: [user: Ai là giám đốc công ty?, assistant: Ông Nguyễn Văn A.]
        Câu hỏi mới: "Ông ấy bao nhiêu tuổi?"
        Kết quả: Giám đốc Nguyễn Văn A bao nhiêu tuổi?
        
        - Lịch sử: [user: Thủ tướng hiện tại là ai?, assistant: Bà La Thu Thu.]
        Câu hỏi mới: "Cảm ơn bạn"
        Kết quả: Cảm ơn bạn
        
        ### THỰC THI
        [LỊCH SỬ]: {history_str}
        [CÂU HỎI MỚI]: {user_query}
        
        KẾT QUẢ ĐỘC LẬP:
        """
        
        if ollama is None:
            return user_query

        response = ollama.chat(
            model = NAME_LLM_MODEL,
            messages = [{'role': 'user', 'content': context_prompt}],
            options = {'temperature': 0}
        )
        
        return response['message']['content'].strip()
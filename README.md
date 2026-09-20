# TPV Chatbot BTL

Hệ thống: AI Chatbot nội bộ cho doanh nghiệp (RAG + LLM + MSSQL + Qdrant)

Mục tiêu: cung cấp một AI server (chatbot) có khả năng:
- Trả lời truy vấn người dùng dựa trên tài liệu (RAG - retrieval augmented generation),
- Tương tác với backend (MSSQL) để trả lời các truy vấn có dữ trúc,
- Tạo văn bản theo template (.docx) từ câu chat của người dùng,
- Sinh báo cáo phòng ban (nhân sự & trang thiết bị), kèm biểu đồ, lưu file .docx và trả về đường dẫn,
- OCR file PDF (trích xuất văn bản),
- Tạo file PowerPoint từ nội dung (template + nội dung).

---------------------------------

Nội dung README này gồm:
1. Giới thiệu ngắn
2. Các tính năng chính
3. Hướng dẫn cài đặt & phát triển
4. Giao tiếp giữa AI server và Backend (contract cho từng tính năng)
5. Dependencies chính
6. Vị trí các file quan trọng trong mã nguồn


1) Giới thiệu
----------------
TPV Chatbot BTL là một AI assistant server triển khai RAG + LLM để phục vụ nhu cầu tìm kiếm kiến thức nội bộ, trả lời theo ngữ cảnh doanh nghiệp, và tự động hoá tạo tài liệu báo cáo. Hệ thống kết hợp:
- Vector DB (Qdrant) cho retrieval
- Redis làm bộ nhớ ngắn hạn (history)
- MSSQL làm nguồn dữ liệu có cấu trúc (nhân sự, trang thiết bị...)
- Ollama-hosted LLM (ví dụ: qwen3:latest) để sinh ngữ liệu và phân tích
- Các tiện ích: tạo docx (.docx), tạo pptx, OCR PDF, vẽ biểu đồ (matplotlib/seaborn)


2) Các tính năng chính
------------------------
- Chat RAG:
  - Nhận truy vấn người dùng, tìm kiếm đa nguồn (Qdrant + MSSQL), xếp hạng kết quả và gọi LLM để tạo câu trả lời dạng JSON.
  - Lưu lịch sử trò chuyện vào Redis.
  - Tập trung vào anti-hallucination: LLM được khuyến khích chỉ trả lời dựa trên context.

- Tạo văn bản theo template (.docx):
  - Input: đường dẫn template (ví dụ `src/templates/Template_Cong_Van.docx`) và câu chat người dùng.
  - Process: sử dụng LLM (qwen3:latest qua Ollama) để phân tích các trường cần điền, ánh xạ thông tin từ câu chat, sinh văn bản phù hợp cho các trường, sau đó ghi vào template và lưu vào `src/output_templates`.
  - API mẫu cho chức năng này có: [src/api/doc_generator_api.py](C:/Users/dungl/Desktop/TPV-chatbot-btl/src/api/doc_generator_api.py)

- Sinh báo cáo phòng ban (nhân sự & trang thiết bị):
  - Input: tenant_id (ID công ty), employee_id (ID người yêu cầu).
  - Process: truy vấn MSSQL (schema mô tả bên dưới) để xác định phòng ban của user, thu thập dữ liệu nhân sự & assets thuộc phòng đó, tổng hợp số liệu, vẽ biểu đồ (matplotlib/seaborn), chèn vào template báo cáo `src/templates/Bao_cao_kiem_ke_Phong_Ky_Thuat.docx` và lưu file vào `src/output_templates`.
  - Luồng đã tích hợp trực tiếp vào pipeline chat: nếu truy vấn người dùng chứa từ khoá báo cáo/kiểm kê, hệ thống sẽ sinh báo cáo và trả về đường dẫn file trong response.

- OCR PDF:
  - Trích xuất văn bản từ file PDF để index vào Qdrant hoặc trả về trực tiếp cho backend.
  - (Chi tiết implement nằm trong module OCR tương ứng; đảm bảo thư viện OCR (tesseract / pdfminer/ocr) có sẵn trong môi trường.)

- Tạo PowerPoint (.pptx):
  - Từ nội dung/ template + input người dùng, hệ thống có thể sinh slide và lưu file pptx.


3) Hướng dẫn cài đặt & phát triển
----------------------------------
Bước nhanh để developer khác có thể chạy & phát triển:

- 1) Clone repository
  git clone <repo-url>
  cd TPV-chatbot-btl

- 2) Tạo và kích hoạt virtualenv (Python >= 3.10 đề nghị)
  python -m venv .venv
  # Windows
  .\.venv\Scripts\activate
  # macOS / Linux
  source .venv/bin/activate

- 3) Cài dependencies (ví dụ):
  pip install -r requirements.txt

  Nếu không có file `requirements.txt`, cài tay các gói chính:
  pip install fastapi uvicorn flask python-docx pyodbc redis qdrant-client matplotlib seaborn pillow transformers torch ollama-client

- 4) Thiết lập biến môi trường quan trọng:
  - MSSQL_*:
    - MSSQL_DRIVER (ví dụ: "ODBC Driver 17 for SQL Server")
    - MSSQL_SERVER (ví dụ: "host\\INSTANCE" hoặc hostname)
    - MSSQL_DATABASE (tên DB)
    - MSSQL_USERNAME (nếu dùng SQL auth)
    - MSSQL_PASSWORD
  - OLLAMA: đảm bảo Ollama daemon đang chạy trên host mà server có thể truy cập; model `qwen3:latest` (hoặc model phù hợp) phải được host.
  - Redis: nếu dùng Redis cho memory service, thiết lập host/port nếu khác mặc định.

- 5) Chạy các dịch vụ cần thiết:
  - Start Ollama và load model (theo hướng dẫn Ollama)
  - Start Qdrant (nếu cần) và khởi tạo index
  - Start MSSQL database (có dữ liệu)
  - Start Redis

- 6) Khởi động AI server (FastAPI/ASGI):
  # Nếu project có `main.py` (ứng dụng FastAPI) — dùng uvicorn
  uvicorn main:app --host 0.0.0.0 --port 8000 --reload

  # API cho tạo doc (Flask, dev-only, ví dụ):
  python src/api/doc_generator_api.py

- 7) Gọi API: sử dụng curl / Postman theo hợp đồng (xem phần 4)


4) Giao tiếp giữa AI server và Backend (Contract)
-------------------------------------------------
Dưới đây tóm tắt phương thức giao tiếp (AI server REST API ↔ Backend server) cho từng tính năng. Dữ liệu giữa backend và AI server dùng JSON string khi trao file path kết quả.

A. Chat (RAG)
- Endpoint (AI server): POST /ask (được định nghĩa trong router tại [src/api/chat.py](C:/Users/dungl/Desktop/TPV-chatbot-btl/src/api/chat.py))
- Request payload (JSON) - ChatRequest gồm các trường:
  {
    "question": "...",
    "tenant_id": "<tenant id>",
    "role_id": "<role id>",
    "user_id": "<user id>",  # ID dùng để lưu history
    "employee_id": "<employee_db_id>",  # ID bảng Hrm_EmployeeProfile (nếu có)
    "is_manager": false,
    "department_ids": [ ... ]
  }
- Response (JSON) - ChatResponse:
  {
    "question": "<echoed>",
    "answer": "<string>" ,
    "sources": [ ... ],
    "conversation_id": "tenant:user",
    "metadata": { "processing_time_seconds": 0.12, "citation": "...", ... }
  }
- Lưu ý: Trong trường hợp request là báo cáo phòng ban (keywords detection hoặc intent), AI server có thể tạo file báo cáo .docx và trả về trong metadata/answer trường `report_file`: đường dẫn file trên máy AI server (backend sẽ chịu trách nhiệm tải file đó nếu cần).

B. Tạo văn bản theo template (.docx)
- Endpoint (AI server): POST /generate_doc (Flask example) - [src/api/doc_generator_api.py](C:/Users/dungl/Desktop/TPV-chatbot-btl/src/api/doc_generator_api.py)
- Request JSON:
  { "template_path": "src/templates/Template_Cong_Van.docx", "user_chat": "Nội dung người dùng" }
- Response JSON (theo hợp đồng trong hệ thống):
  { "result": "{\"file_path\": \"src/output_templates/<file>.docx\"}" }
  - Lưu ý: `result` là một JSON string (chuỗi JSON) chứa ít nhất key `file_path` trỏ tới file được tạo ở phía AI server.

C. Báo cáo phòng ban (nhân sự + trang thiết bị)
- Input (từ backend): thông tin tối thiểu cần có trong request chat (tenant_id, employee_id) khi gửi truy vấn dạng "Cho tôi báo cáo kiểm kê phòng".
- Process: AI server sẽ:
  1) xác định WorkDepartmentId của employee (bằng truy vấn MSSQL),
  2) lấy dữ liệu từ các bảng: Dms_WorkDepartment, Hrm_EmployeeProfile, Asm_Assets, Asm_AssetCategories,
  3) tổng hợp, vẽ biểu đồ (matplotlib/seaborn), chèn vào template `src/templates/Bao_cao_kiem_ke_Phong_Ky_Thuat.docx`,
  4) lưu file vào `src/output_templates`.
- Response (từ chat endpoint /ask): trả về `answer` (tóm tắt text) và trong metadata hoặc phần mở rộng trả về `report_file` = đường dẫn file docx sinh ra.

D. OCR PDF
- Expected Request: backend gửi file (URL hoặc file path) cùng metadata (tenant_id, employee_id, document_id).
- AI server: nhận file, thực hiện OCR, trả về JSON: { "text": "<extracted_text>", "pages": N, "file_id": "..." }
- Nếu dùng để index vào Qdrant: AI server có thể trả về segments/chunks kèm metadata để backend lưu hoặc AI server trực tiếp upload vào vector DB.

E. Tạo PowerPoint (.pptx)
- Expected Request: { "template_path": "...", "slides": [ {"title":"...","content":"..."}, ... ], "tenant_id":..., "employee_id":... }
- Response: JSON string với file_path: đường dẫn file pptx trên AI server.


5) Dependencies chính
-----------------------
Danh sách các packages chính cần cài để chạy hệ thống (khuyến nghị tạo virtualenv):

- Core LLM / infra
  - ollama (client tương tác với Ollama daemon)
  - transformers, torch (nếu dùng mô hình local/reranker)
  - langchain_core (dùng cho message types)

- API & web
  - fastapi
  - uvicorn
  - flask (chỉ một vài endpoint phụ trợ trong repo)

- Data & DB
  - pyodbc (kết nối MSSQL)
  - qdrant-client (nếu dùng Qdrant)
  - redis (client)

- Document & Office
  - python-docx
  - python-pptx (nếu dùng tạo PowerPoint)
  - pillow (PIL) - cho ảnh/biểu đồ

- Visualization
  - matplotlib
  - seaborn

- OCR
  - pytesseract (nếu dùng tesseract), pdfminer.six, fitz (PyMuPDF) hoặc các thư viện OCR khác

- Utilities
  - requests
  - numpy, pandas (tùy nhu cầu xử lý dữ liệu)


6) Các file & vị trí quan trọng
-------------------------------
- Core chat pipeline: [src/core/chat.py](C:/Users/dungl/Desktop/TPV-chatbot-btl/src/core/chat.py)
- LLM service (Ollama wrapper): [src/services/llm_service.py](C:/Users/dungl/Desktop/TPV-chatbot-btl/src/services/llm_service.py)
- Prompt builder & prompt templates: [src/services/prompt_service.py](C:/Users/dungl/Desktop/TPV-chatbot-btl/src/services/prompt_service.py)
- Docx template generation service: [src/services/docs_create_service.py](C:/Users/dungl/Desktop/TPV-chatbot-btl/src/services/docs_create_service.py)
- Report service (tạo báo cáo phòng ban + biểu đồ): [src/services/report_service.py](C:/Users/dungl/Desktop/TPV-chatbot-btl/src/services/report_service.py)
- MSSQL retriever: [src/services/mssql_retrieval_service.py](C:/Users/dungl/Desktop/TPV-chatbot-btl/src/services/mssql_retrieval_service.py)
- Memory (Redis): [src/services/memory_service.py](C:/Users/dungl/Desktop/TPV-chatbot-btl/src/services/memory_service.py)
- APIs:
  - Chat API router: [src/api/chat.py](C:/Users/dungl/Desktop/TPV-chatbot-btl/src/api/chat.py)
  - Doc generation API (Flask example): [src/api/doc_generator_api.py](C:/Users/dungl/Desktop/TPV-chatbot-btl/src/api/doc_generator_api.py)
- Templates: folder `src/templates/` — chứa template .docx và template PowerPoint (nếu có).
- Output: `src/output_templates/` — nơi lưu các file docx và biểu đồ tạm.


7) Lời khuyên cho developer
---------------------------
- Việc tích hợp LLM gắn với prompt engineering — hãy thử nghiệm các phiên bản prompt trong [src/services/prompt_service.py](C:/Users/dungl/Desktop/TPV-chatbot-btl/src/services/prompt_service.py) để tối ưu hoá đầu ra JSON mong muốn.
- Tránh in dữ liệu nhạy cảm ra logs.
- Khi tương tác MSSQL, cân nhắc dùng parameterized queries hoặc ORM để tránh rủi ro injection (hiện code mẫu sử dụng chuỗi SQL đơn giản vì giá trị được backend cung cấp tin cậy).
- Để nâng cao tính chính xác: triển khai unit test cho các phần map template -> fields -> docx (ví dụ: test template mẫu với mock LLM output).
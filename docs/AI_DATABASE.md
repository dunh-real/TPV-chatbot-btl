# Ánh xạ database AI có sẵn

Backend đọc schema thực tế bằng SQLAlchemy reflection trong `app/db/ai_models.py`.
Không tạo bảng, thêm cột hoặc sửa dữ liệu mẫu. Các file mới có chú thích tiếng Việt.

## Cấu hình

- `AI_DATABASE_URL`: kết nối đọc/ghi bảng `AI_*`, bắt buộc cho mẫu/văn bản/đầu ra;
  thiếu cấu hình báo lỗi, không quay về nghiệp vụ SQLite.
- `ERP_DATABASE_URL`: kết nối đọc tài khoản/quyền và truy vấn ERP, vẫn chặn ghi.
- Hai URL cần trỏ cùng DB chứa tài khoản ABP tương ứng. Không đổi `DATABASE_URL`
  sang SQL Server: kết nối đó chỉ quản lý tám bảng pháp lý SQLite.
- `AI_DEFAULT_PARTITION_ID`: tùy chọn cho admin, phải thuộc đúng tenant và còn hoạt động.
  Người dùng thường chỉ nạp vào phân vùng cá nhân của chính mình.

Danh tính phải được xác minh qua ERP. Không suy user ID từ tên đăng nhập trong thao tác ghi.
Tenant `NULL` chỉ khớp dữ liệu tenant `NULL`, không có nghĩa đọc tất cả tenant.

## Luồng dữ liệu

| Chức năng | Nơi lưu chính |
|---|---|
| Metadata tài liệu, trạng thái lập chỉ mục | `AI_KnowledgeDocuments` |
| Mẫu và phiên bản đang Published | `AI_ReportTemplate`, `AI_TemplateVersions` |
| Hội thoại, tin nhắn | `AI_ChatConversations`, `AI_ChatMessages` |
| Soát tài liệu, soạn báo cáo, tổng hợp, slide | Bảng đầu ra tương ứng + `AI_Tasks` |
| Phiên bản đầu ra, nguồn tham chiếu có doc_id | `AI_OutputVersions`, `AI_SourceReferences` |
| Nhật ký workflow | `AI_ExecutionLogs` |
| Sơ đồ tư duy | `AI_MindMaps` |
| File gốc và file đầu ra | MinIO; SQL lưu tham chiếu `minio://bucket/key` |
| Nội dung chia đoạn và embedding | Qdrant; nối SQL qua `AiServerDocId` |

`ai_repository.py` chứa truy vấn dùng chung. Các service `ai_documents`,
`ai_templates`, `ai_outputs`, `ai_mindmaps` nối chúng vào luồng cũ.

Nạp tài liệu: SQL Pending → MinIO/embedding/Qdrant → SQL Indexed/Extracted.
Lỗi nạp đánh dấu Failed; không trả các đoạn đang nạp/lỗi vào RAG.
Upload API giữ cách lưu MinIO trước khi xử lý. SQL/Qdrant/MinIO không có transaction
phân tán: khi lỗi có thể còn file/chunk cần dọn hoặc chạy lại, không báo thành công giả.
ID mới phân biệt tenant và người nạp để tránh ghi đè chỉ mục chéo người dùng.

Workflow: tạo task Processing → chạy → lưu file MinIO → ghi sản phẩm, phiên bản,
nhật ký và kết thúc task trong một transaction. Response có `ai_task_id`.
Thiếu đầu vào được đánh dấu Cancelled, lỗi là Failed. Không bổ sung API UC37 ở đợt này.
File xuất cục bộ vẫn là bản làm việc; API tải kết quả đã đăng ký lấy file từ MinIO.

Hội thoại SQL là nguồn chính khi bật cấu hình; không đọc cache/Qdrant cũ để tránh
lẫn lịch sử theo ID hội thoại. Các danh sách tài liệu đã lưu vẫn dùng JSON như trước.

## Phạm vi quyền và dữ liệu cũ

- Giữ chốt quyền chức năng trong `agents/quyen.py`.
- Tài liệu: người dùng đọc bản ghi do mình tạo; admin đọc trong cùng tenant.
  Chưa mở thêm quyền chia sẻ nhóm/đơn vị từ `AI_PartitionPermissions`.
- Hội thoại luôn theo đúng người dùng. Mẫu có UsageScopeJson chỉ mở cho admin
  cho đến khi triển khai bộ đánh giá phạm vi đầy đủ; không tự đoán quyền.
- Chỉ dùng mẫu và phiên bản Published đúng CurrentVersionNo. Dữ liệu kiểm tra ban đầu
  có 45 mẫu nhưng các phiên bản đều Retired: cần bên quản lý mẫu công bố phiên bản hợp lệ.
- Không tự chuyển SQLite/JSON cũ, không tự nạp lại 393 tài liệu SQL. Một bản ghi
  Indexed trên SQL chưa chứng minh collection Qdrant của môi trường này có nội dung.
- Dữ liệu hiện có trùng một số `AiServerDocId`: bản ghi đó dùng `doc_id=sql:<Id>`
  để xem metadata/bản gốc; loại khỏi RAG đến khi có liên kết chỉ mục duy nhất.
  Không tự chọn một bản ghi hoặc sửa ID đã có.
- Chế độ SQL AI yêu cầu `inputs.so_ky_hieu` do người dùng cung cấp; không dùng sổ
  SQLite cũ để tự cấp số cho DB mới. Nhánh soạn từ tài liệu chọn mẫu qua `inputs.ma_template`.
- Không thay cấu hình model trong `.env` bằng `AI_Models` ở đợt này.

## Chạy thử trên yuu

Thư mục: `/home/abc/yuu_workspace/TPV-chatbot-btl`, khác bản `/chatbot/` chạy cổng 8082.
Profile riêng `backend/.env.ai-test` không đưa vào Git và không sửa `.env` đang có.
Yuu kết nối trực tiếp SQL Server bị timeout; bài thử dùng reverse tunnel qua Mac.

Mở tunnel trên Mac và giữ cửa sổ đó:

```bash
ssh -N -o ExitOnForwardFailure=yes \
  -R 127.0.0.1:17679:dbnews.tpvtech.vn:7679 abc@100.105.11.124
```

Trên yuu, trong `backend`, chạy kiểm tra schema và ghi thử rollback:

```bash
.venv/bin/python -c 'from dotenv import load_dotenv; load_dotenv(".env.ai-test", override=True); import runpy,sys; sys.argv=["check_ai_database.py","--write-rollback","--user-id","407"]; runpy.run_path("scripts/check_ai_database.py",run_name="__main__")'
```

Chạy backend thử riêng, cổng 18083:

```bash
.venv/bin/python -c 'from dotenv import load_dotenv; load_dotenv(".env.ai-test", override=True); import uvicorn; uvicorn.run("app.main:app",host="127.0.0.1",port=18083)'
```

Forward cổng trên Mac: `ssh -N -L 18083:127.0.0.1:18083 abc@100.105.11.124`.
Mở `http://127.0.0.1:18083/docs`, dùng header `X-Tenant-Id: 94`, `X-User-Id: 407`
cho tài khoản admin đã kiểm tra. Nạp file thử rồi kiểm tra detail/original và lịch sử chat.

Bài `tests/test_ai_mapping_live.py` chạy SQL/MinIO/Qdrant thật, giả lập embedding và
phần sinh sản phẩm để kiểm tra luồng lưu trữ; không đánh giá chất lượng model.
SQL luôn rollback, collection và object riêng của bài thử được dọn cuối bài.
Rollback không hoàn tác số identity đã cấp. Không chạy bài ghi thử đồng thời trên cùng connection.

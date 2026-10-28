# Báo cáo thay đổi ánh xạ database AI — 28/10/2026

> **Cập nhật:** Đây là snapshot trước bước tách SQLite pháp lý/MSSQL nghiệp vụ. Xem [STORAGE_SPLIT.md](STORAGE_SPLIT.md) cho trạng thái mới; diff bên dưới giữ nguyên làm bằng chứng lịch sử.

> Lập lúc 28/10/2026 11:42 (giờ Việt Nam).
> Báo cáo này chỉ mô tả và chụp lại thay đổi; không thực hiện thu gọn, rollback hoặc sửa tiếp code.

## 1. Phạm vi báo cáo và kết luận kiểm kê

Báo cáo cho **đợt map database AI vừa thực hiện**. Mốc đối chiếu là commit trên Mac:

```text
0ec8eb552016dd600d009c02549916fc2ccb42dc
feat: add document search, saved lists and export with ERP permissions
```

Không phải báo cáo toàn bộ lịch sử MinIO/tìm kiếm/UC37 của những lượt trước. Các file đã được commit ở mốc trên là nền so sánh.

Phạm vi thực tế đã lớn hơn phương án tối thiểu chỉ map kho tài liệu và mẫu: đã đưa thêm hội thoại, sơ đồ, task, sản phẩm, phiên bản và nhật ký. Tôi đã thừa nhận điểm này; **phương án thu gọn mới chỉ được đề xuất, chưa thực hiện**.

| Hạng mục | Số lượng |
|---|---:|
| File đã có trong Git được sửa | 26 |
| File mới trong đợt map DB | 11 |
| File bị xóa | 0 |
| Dòng thêm trong 26 file cũ | 239 |
| Dòng bỏ/thay thế trong 26 file cũ | 25 |
| Dòng của 11 file mới, gồm code/test/script/tài liệu | 1053 |
| Tổng dòng thêm, chưa tính chính báo cáo này | 1292 |

Các số dòng là diff vật lý, gồm dòng trống/chú thích. 25 dòng bị bỏ/thay không có nghĩa xóa 25 chức năng. Bảy module runtime AI mới có tổng cộng 595 dòng ở thời điểm lập báo cáo; phần còn lại của file mới là script, test và hướng dẫn.

Bản thay đổi chưa được commit. Không có thay đổi staged tại lúc kiểm kê. Báo cáo này là một file mới bổ sung ở lượt hiện tại, không tính vào 11 file mới ở đợt trước.

## 2. Những việc đã làm và kết quả quan sát

1. Đọc `db_sample.txt`/cấu trúc người dùng cung cấp, rồi đọc metadata SQL Server thật. Có 31 bảng AI; chọn reflection 15 bảng phục vụ phần đã triển khai.
2. Xác nhận schema thật khác mẫu ở một số cột: hội thoại dùng LastMessageTime/MessageCount; mẫu dùng TemplateType, FieldsJson, FileUrl; một số bảng đầu ra không giống toàn bộ thiết kế trong file mẫu.
3. Tại lần đọc dữ liệu ban đầu: tổng 393 tài liệu, 45 mẫu, 31 phân vùng. Đây là số đếm tại thời điểm đó, không phải số bản ghi mới đã nạp hoặc số tài liệu RAG dùng được.
4. Quan sát các phiên bản mẫu hiện có mang trạng thái Retired. Code mới chỉ dùng mẫu và phiên bản Published; không tự sửa trạng thái mẫu để bài thử chạy qua.
5. Yuu không kết nối trực tiếp dbnews.tpvtech.vn:7679 được, trả login timeout. Mở reverse tunnel tạm qua Mac ở cổng yuu 17679 để thử; profile thử dùng TrustServerCertificate=yes vì chứng chỉ tự ký. Không sửa chính sách TLS trong code hoặc `.env` đang chạy.
6. Đã thêm kết nối AI ghi riêng, giữ kết nối ERP chỉ đọc. Reflection không tạo/đổi cấu trúc bảng.
7. Đã tích hợp cả kho/mẫu và các phần mở rộng hội thoại, sơ đồ, đầu ra/task/log như danh sách file bên dưới.
8. Bài khởi động/API thực tế phát hiện ID trùng. Truy vấn thời điểm đó trên tenant 94, chưa xóa, Indexed, có AiServerDocId: 51 hàng, 47 ID phân biệt; 4 nhóm bị trùng đôi, không có VersionGroupId phân biệt trong các nhóm kiểm tra. Đã thêm alias sql:<Id> để xem metadata/bản gốc và loại liên kết không duy nhất khỏi RAG; không sửa ID trong DB.
9. Đã thử rollback SQL, lưu trữ thật, hồi quy và khởi động backend tạm. Lượt xác nhận parity file + chạy lại storage sau cùng bị từ chối quyền SSH, nên chưa hoàn tất lượt cuối đó.

## 3. Các thay đổi hành vi cần bạn xem trước

| Phần | Trước | Sau khi bật AI_DATABASE_URL |
|---|---|---|
| Metadata kho | Chủ yếu từ payload Qdrant | Từ AI_KnowledgeDocuments |
| Mẫu báo cáo | template_bao_cao | Mẫu + phiên bản SQL Published |
| Không có mẫu phù hợp | Một số nhánh tự dùng mẫu/dàn ý chung | Nhánh soạn yêu cầu chọn mẫu SQL |
| Số ký hiệu | Có thể cấp từ sổ SQLite | Yêu cầu inputs.so_ky_hieu ở xuất báo cáo |
| ID tài liệu mới | Digest file/nội dung | Digest có thêm tenant/user |
| Lọc RAG | Bộ lọc truy vấn hiện có | Thêm danh sách ID được phép và liên kết duy nhất từ SQL |
| Chat | Qdrant bền, Redis cache | SQL là nguồn chính, bỏ đọc lịch sử cache/store cũ ở nhánh mới |
| Sơ đồ | JSON cục bộ | AI_MindMaps |
| Bốn workflow | Sinh kết quả theo luồng cũ | Có thêm task, sản phẩm, phiên bản 1 và log SQL |
| File đầu ra | Chủ yếu data/output | File đăng ký với task được tải từ MinIO; còn fallback local cho file không có task |
| Quyền tài liệu | Chốt chức năng trong quyen.py | Giữ chốt đó, thêm giới hạn tenant/chủ sở hữu ở repository mới |

Điều kiện bật được kiểm tra trong code qua ai_session.enabled(). Không phải mọi dòng thay đổi đều chỉ chạy khi bật: helper tải file thành async, các chỗ await, schema ai_task_id, adapter sơ đồ và handler PermissionError là thay đổi kết cấu được nạp cả trong chế độ cũ.

## 4. Danh mục file và số dòng

Đường dẫn dưới đây tính từ thư mục dự án. Số dòng hàm là vị trí hiện tại trên Mac, có thể đổi sau khi bạn sửa tiếp.

| Loại | File | Thêm | Bỏ/thay |
|---|---|---:|---:|
| Sửa | [backend/.env.example](../backend/.env.example) | 4 | 0 |
| Sửa | [backend/.gitignore](../backend/.gitignore) | 1 | 0 |
| Sửa | [backend/app/agents/graph.py](../backend/app/agents/graph.py) | 9 | 0 |
| Sửa | [backend/app/agents/nodes/drafting.py](../backend/app/agents/nodes/drafting.py) | 14 | 0 |
| Sửa | [backend/app/agents/nodes/drafting_doc.py](../backend/app/agents/nodes/drafting_doc.py) | 10 | 0 |
| Sửa | [backend/app/agents/nodes/report.py](../backend/app/agents/nodes/report.py) | 10 | 2 |
| Sửa | [backend/app/agents/state.py](../backend/app/agents/state.py) | 2 | 0 |
| Sửa | [backend/app/api/agent.py](../backend/app/api/agent.py) | 1 | 1 |
| Sửa | [backend/app/api/documents.py](../backend/app/api/documents.py) | 20 | 3 |
| Sửa | [backend/app/api/files.py](../backend/app/api/files.py) | 25 | 1 |
| Sửa | [backend/app/api/mindmap.py](../backend/app/api/mindmap.py) | 12 | 12 |
| Sửa | [backend/app/api/presentations.py](../backend/app/api/presentations.py) | 1 | 1 |
| Sửa | [backend/app/api/reports.py](../backend/app/api/reports.py) | 1 | 1 |
| Sửa | [backend/app/core/config.py](../backend/app/core/config.py) | 4 | 0 |
| Sửa | [backend/app/db/repository.py](../backend/app/db/repository.py) | 32 | 0 |
| Sửa | [backend/app/db/session.py](../backend/app/db/session.py) | 6 | 0 |
| Sửa | [backend/app/main.py](../backend/app/main.py) | 23 | 2 |
| Sửa | [backend/app/rag/ingestion.py](../backend/app/rag/ingestion.py) | 8 | 2 |
| Sửa | [backend/app/rag/retrieval.py](../backend/app/rag/retrieval.py) | 2 | 0 |
| Sửa | [backend/app/rag/vectorstore.py](../backend/app/rag/vectorstore.py) | 18 | 0 |
| Sửa | [backend/app/schemas/documents.py](../backend/app/schemas/documents.py) | 1 | 0 |
| Sửa | [backend/app/schemas/presentations.py](../backend/app/schemas/presentations.py) | 1 | 0 |
| Sửa | [backend/app/schemas/reports.py](../backend/app/schemas/reports.py) | 2 | 0 |
| Sửa | [backend/app/services/conversation.py](../backend/app/services/conversation.py) | 14 | 0 |
| Sửa | [backend/app/services/object_storage.py](../backend/app/services/object_storage.py) | 12 | 0 |
| Sửa | [backend/app/tools/templates.py](../backend/app/tools/templates.py) | 6 | 0 |
| Thêm mới | [backend/app/db/ai_models.py](../backend/app/db/ai_models.py) | 15 | 0 |
| Thêm mới | [backend/app/db/ai_session.py](../backend/app/db/ai_session.py) | 51 | 0 |
| Thêm mới | [backend/app/db/ai_repository.py](../backend/app/db/ai_repository.py) | 191 | 0 |
| Thêm mới | [backend/app/services/ai_documents.py](../backend/app/services/ai_documents.py) | 86 | 0 |
| Thêm mới | [backend/app/services/ai_templates.py](../backend/app/services/ai_templates.py) | 71 | 0 |
| Thêm mới | [backend/app/services/ai_outputs.py](../backend/app/services/ai_outputs.py) | 123 | 0 |
| Thêm mới | [backend/app/services/ai_mindmaps.py](../backend/app/services/ai_mindmaps.py) | 58 | 0 |
| Thêm mới | [backend/scripts/check_ai_database.py](../backend/scripts/check_ai_database.py) | 117 | 0 |
| Thêm mới | [backend/tests/test_ai_mapping.py](../backend/tests/test_ai_mapping.py) | 121 | 0 |
| Thêm mới | [backend/tests/test_ai_mapping_live.py](../backend/tests/test_ai_mapping_live.py) | 121 | 0 |
| Thêm mới | [docs/AI_DATABASE.md](../docs/AI_DATABASE.md) | 99 | 0 |

## 5. Chi tiết 26 file đã sửa

### 5.1. `backend/.env.example`

- Thêm `AI_DATABASE_URL=` để bật kết nối nghiệp vụ AI riêng.
- Thêm hướng dẫn `AI_DEFAULT_PARTITION_ID`; dòng ví dụ được comment, không tự chọn phân vùng.
- Giữ `DATABASE_URL` cũ. Đây là thay đổi file mẫu cấu hình, không sửa `.env` đang dùng.

### 5.2. `backend/.gitignore`

- Thêm `.env.ai-test` vào danh sách bỏ qua Git để không đưa profile có thông tin kết nối vào commit.

### 5.3. `backend/app/agents/graph.py`

- Thêm import `tracked_workflow`.
- Bọc bốn hàm bằng decorator: `run_document_workflow` (Review), `run_draft_workflow` (Draft), `run_aggregate_workflow` (Aggregate), `run_presentation_workflow` (Slide). Khi bật AI SQL, việc gọi các hàm này có thêm tác dụng ghi task, sản phẩm và nhật ký.
- Kết quả soạn báo cáo bổ sung `template_id`, `template_version` lấy từ template trong state.
- Kết quả tổng hợp bổ sung hai trường phiên bản mẫu tương tự.
- Không thêm API danh sách/chạy lại/thông báo của UC37; tuy nhiên đã thêm cơ chế ghi task, vượt phần tối thiểu chỉ map kho tài liệu và mẫu.

### 5.4. `backend/app/agents/nodes/drafting.py`

- `select_template_node()`: bật AI SQL mà không có mẫu phù hợp thì trả `missing_input`; không tự dùng mẫu chung BC_ERP như trước.
- Thêm ID và phiên bản mẫu SQL vào state template.
- `export_node()`: bật AI SQL mà chưa nhập `so_ky_hieu` thì trả yêu cầu bổ sung số văn bản. Đây là thay đổi hành vi, không chỉ thay kết nối.
- `register_node()`: khi bật AI SQL, bỏ qua ghi sổ SQLite vì decorator workflow sẽ ghi sản phẩm vào SQL AI. Chế độ cũ giữ nhánh ghi sổ cũ.

### 5.5. `backend/app/agents/nodes/drafting_doc.py`

- `outline_node()`: khi bật AI SQL, chọn mẫu qua `get_template(inputs.ma_template hoặc request)`.
- Không tìm thấy mẫu: trả `missing_input`. Mẫu không có sections: trả lỗi cấu hình.
- Tìm thấy mẫu: dùng cấu trúc mẫu SQL và trả ngay; không chạy nhánh LLM tự lập dàn ý cũ trong chế độ này.
- Đây là thay đổi đáng kể ở luồng soạn báo cáo từ tài liệu, cần bạn xem riêng.

### 5.6. `backend/app/agents/nodes/report.py`

- `export_node()`: bật AI SQL mà không có `inputs.so_ky_hieu` thì trả lỗi, không tự cấp số bằng sổ SQLite.
- Cho phép chọn mẫu bằng `inputs.ma_template`; nếu không có vẫn tra `BC_TONGHOP`.
- Kết quả xuất bổ sung ID/phiên bản mẫu SQL để liên kết sản phẩm.
- `register_node()`: bỏ qua ghi sổ SQLite khi bật AI SQL; việc ghi sản phẩm chuyển vào decorator workflow.

### 5.7. `backend/app/agents/state.py`

- Thêm `template_id` và `template_version` vào `AggregateState` để LangGraph giữ hai trường này qua các node.

### 5.8. `backend/app/api/agent.py`

- Chỉ đổi handler download từ `return output_file_response(...)` thành `return await output_file_response(...)` vì helper tải file đã chuyển thành async.

### 5.9. `backend/app/api/documents.py`

- `_ingest_source()`: chỉ dò ID file kiểu cũ khi chưa bật AI SQL; tránh tự gắn dữ liệu cũ sang không gian ID mới.
- Nhánh upload trùng/không force: vẫn cập nhật metadata liên kết và trạng thái Indexed/Extracted trong SQL, giữ chunk_count và LastIndexedTime.
- Handler download đổi sang await helper async.
- `list_documents()`: thêm log ngoại lệ phía server, thay lỗi phản hồi chứa nguyên văn ngoại lệ bằng thông báo chung.
- `remove_document()`: khi bật AI SQL, kiểm tra bản ghi tồn tại trong phạm vi truy cập, đánh dấu IsDeleted/DeletionTime/DeleterUserId rồi mới gọi xóa Qdrant như luồng cũ.
- Không thêm endpoint upload mới. Các route search/detail/original sử dụng metadata SQL gián tiếp qua vectorstore/service đã sửa.

### 5.10. `backend/app/api/files.py`

- Đổi `output_file_response()` thành async.
- Khi bật AI SQL, tìm task theo tên file trong `TextSearch`, giới hạn quyền đọc task trước khi lấy `OutputFileUrl`.
- Có task tương ứng nhưng không được đọc hoặc thiếu file: trả 404. Có tham chiếu MinIO hợp lệ: stream file từ MinIO bằng response quản lý đóng kết nối.
- Không có task nào tương ứng: vẫn rơi về tải file cục bộ theo cơ chế cũ, gồm kiểm tra tenant của tên file. Vì vậy chưa phải toàn bộ download đã chuyển sang MinIO.
- Helper dùng `_OriginalResponse` của API documents; đây là phụ thuộc giữa hai module API được thêm trong đợt này.

### 5.11. `backend/app/api/mindmap.py`

- Đổi import từ store JSON trực tiếp sang adapter `ai_mindmaps`, vẫn dùng tên biến mindmap_store.
- Đổi `_record_or_404()` sang async; thêm await ở các điểm load/save/list/delete trong API nguồn, tạo cây, đọc cây, mở rộng mục và xóa.
- Không đổi thuật toán sinh cây; đổi nguồn lưu cây khi AI SQL được bật.

### 5.12. `backend/app/api/presentations.py`

- Chỉ đổi handler download để await helper tải file async.

### 5.13. `backend/app/api/reports.py`

- Chỉ đổi handler download để await helper tải file async. API danh mục mẫu đổi nguồn dữ liệu thông qua TemplateRepository, không thêm đoạn SQL trực tiếp tại đây.

### 5.14. `backend/app/core/config.py`

- Thêm `ai_database_url: str = ""` và `ai_default_partition_id: int | None = None`.
- `AI_DATABASE_URL` rỗng: giữ các nhánh lưu trữ cũ. Có giá trị: các nhánh mới chuyển sang SQL AI.
- Không bỏ hoặc thay thế kết nối `database_url` và `erp_database_url` hiện có.

### 5.15. `backend/app/db/repository.py`

- `TemplateRepository.list_all()`: khi bật AI SQL, gọi service mẫu AI thay truy vấn bảng `template_bao_cao`.
- `TemplateRepository.get()`: tìm mã trong danh mục SQL rồi tải file mẫu MinIO vào cache khi cần.
- `VanBanRepository.get()` và `get_by_doc_id()`: khi bật AI SQL, gọi `_ai_document()` để tìm theo DocumentNumber/DocCode/AiServerDocId.
- Thêm `_ai_document()` chuyển bản ghi `AI_KnowledgeDocuments` thành đối tượng `VanBan` tạm để các nơi gọi cũ còn đọc được. Một số trường không có ánh xạ được trả rỗng; không phải ánh xạ đầy đủ mọi cột VanBan.
- `upsert()`: chặn ghi sổ SQLite khi bật AI SQL, yêu cầu đi qua service tài liệu/sản phẩm.
- `list_recent()` và `link_doc_id()` vẫn là code cũ; chưa chuyển hai phương thức này sang bảng AI. Không nên hiểu đây là việc thay toàn bộ repository cũ.

### 5.16. `backend/app/db/session.py`

- `healthcheck()`: nếu bật AI SQL, kiểm tra reflection và `SELECT 1` trên kết nối AI; nếu không, giữ kiểm tra DB cũ.
- Không sửa `create_all()` hoặc hàm thêm cột của DB cũ. Các hàm này vẫn tồn tại cho luồng cũ/pháp lý; session AI mới không gọi chúng.

### 5.17. `backend/app/main.py`

- Khởi động: bật AI SQL thì đọc mapping các bảng AI thay cho ensure collection hội thoại Qdrant; collection tài liệu Qdrant vẫn được khởi tạo.
- Tắt ứng dụng: dispose thêm engine AI.
- Health: đếm tin nhắn SQL trong phạm vi người gọi khi bật AI SQL; lỗi đếm vẫn được bắt, số mặc định là 0.
- Thêm handler `PermissionError` trả HTTP 403 với thông báo chung.
- Vẫn tạo đối tượng store hội thoại cũ và còn một số log dùng từ collection; đây không phải việc loại bỏ hoàn toàn code hội thoại Qdrant.

### 5.18. `backend/app/rag/ingestion.py`

- `make_doc_id()` và `make_doc_id_file()` chuyển digest qua `scoped_id()`.
- Bật AI SQL: ID được băm thêm tenant và user; cùng file khác người/tenant có thể có ID mới khác nhau. Tắt AI SQL: giữ ID cũ.
- Bọc `IngestionPipeline.ingest_text()` bằng `tracked_ingestion` để ghi trạng thái vào SQL trước/sau quá trình xử lý.
- Không bỏ các bước converter, chunking, embedding hoặc ghi Qdrant.

### 5.19. `backend/app/rag/retrieval.py`

- Thêm `restrict(query_filter)` ở đầu `HybridRetriever.search()`.
- Bật AI SQL: lấy danh sách doc_id được phép từ SQL và gộp vào filter trước khi chạy các nhánh truy hồi.
- Danh sách được phép rỗng phải khớp rỗng, không trở thành truy vấn toàn kho.

### 5.20. `backend/app/rag/vectorstore.py`

- `document_metadata()`: bật AI SQL thì đọc metadata từ service SQL, không đọc payload Qdrant cho phần này.
- `scroll_document()`: kiểm tra tài liệu qua metadata SQL trước khi trả nội dung.
- `scroll_filtered()`: áp thêm filter truy cập SQL.
- `list_documents()`: lấy các bản ghi Indexed trong SQL theo tenant/chủ sở hữu. ID chỉ mục không duy nhất hoặc không dùng được thì hiển thị bằng alias `sql:<Id>`.
- Các hàm search_dense/search_sparse và cơ chế lưu embedding vẫn dùng Qdrant; không chuyển vector sang SQL.

### 5.21. `backend/app/schemas/documents.py`

- Thêm trường tùy chọn `ai_task_id: int | None = None` vào `ReviewResponse`.

### 5.22. `backend/app/schemas/presentations.py`

- Thêm trường tùy chọn `ai_task_id` vào `PresentationResponse`.

### 5.23. `backend/app/schemas/reports.py`

- Thêm trường tùy chọn `ai_task_id` vào `DraftResponse` và `AggregateResponse`.

### 5.24. `backend/app/services/conversation.py`

- Thêm nhánh AI SQL vào `ConversationMemory.get_history()`, `append_turns()`, `clear_history()`.
- Bật AI SQL: đọc/ghi/xóa mềm thông qua repository AI và trả ngay; không đọc Redis/Qdrant cũ ở những nhánh này.
- Tắt AI SQL: giữ luồng cache và Qdrant cũ. Không tự di trú lịch sử cũ sang SQL.
- Đây là phần đã mở rộng ngoài kho tài liệu/mẫu, làm tăng phạm vi thay đổi.

### 5.25. `backend/app/services/object_storage.py`

- Thêm `parse_reference()` đọc `minio://bucket/key`, `minio:bucket/key`, `s3://bucket/key`.
- Từ chối scheme khác, tham chiếu thiếu bucket/key, query hoặc fragment.
- Không thay nội dung các phương thức upload/download/open MinIO hiện có.

### 5.26. `backend/app/tools/templates.py`

- `_as_dict()`: bổ sung ID và phiên bản mẫu SQL nếu có.
- `get_template()`: mẫu tìm thấy trong chế độ SQL được materialize từ MinIO trước khi trả cho workflow.
- Không thay công cụ chọn mẫu bằng truy vấn SQL do LLM tự sinh.

## 6. Chi tiết 11 file mới

### 6.1. `backend/app/db/ai_models.py`

- Thêm danh sách 15 bảng và hàm `reflect(connection)` đọc metadata bằng SQLAlchemy Table/MetaData.
- Đây là reflection, không phải 15 lớp ORM khai báo tay. Không gọi create_all, ALTER hay migration.
- Các bảng: AI_KnowledgeDocuments, AI_DataPartitions, AI_ReportTemplate, AI_TemplateVersions, AI_ChatConversations, AI_ChatMessages, AI_Tasks, AI_DocumentReviews, AI_DocumentDrafts, AI_ReportSummaries, AI_Slides, AI_OutputVersions, AI_SourceReferences, AI_MindMaps, AI_ExecutionLogs.

**Vị trí hàm:** `reflect()` dòng 12.

### 6.2. `backend/app/db/ai_session.py`

- Thêm enabled(), get_engine(), tables(), repository(), dispose().
- Cache mapping sau lần reflection đầu; dùng asyncio.Lock để tránh khởi tạo trùng.
- Kết nối AI độc lập, hide_parameters=True, pool_pre_ping=True.
- Context repository dùng engine.begin(): thành công commit, ngoại lệ rollback. Kết nối ERP chỉ đọc không bị gỡ chặn ghi.

**Vị trí hàm:** `enabled()` dòng 15; `get_engine()` dòng 19; `tables()` dòng 29; `repository()` dòng 40; `dispose()` dòng 47.

### 6.3. `backend/app/db/ai_repository.py`

- Thêm AIRepository dùng chung cho các bảng AI. Khởi tạo yêu cầu tài khoản ERP đã xác minh và tenant khớp principal.
- scope(): lọc TenantId và IsDeleted. owned=True giới hạn UserId/CreatorUserId với người thường; admin được bỏ giới hạn chủ sở hữu nhưng vẫn trong tenant. Hội thoại có thêm điều kiện UserId rõ ràng cho cả admin.
- add()/patch()/rows(): thêm, cập nhật, đọc; tự ghi các trường audit phù hợp với bảng.
- lock(): dùng sp_getapplock trên SQL Server trong transaction, timeout 15 giây; khóa tài liệu dùng phạm vi doc_id chung, khóa hội thoại theo tenant/user.
- document()/document_ids()/unique_index(): tra tài liệu, cấp danh sách ID cho RAG; chỉ cho phép ID chỉ mục duy nhất trong các bản ghi chưa xóa. Nhận alias sql:<Id> để đọc metadata theo khóa SQL.
- partition(): dùng phân vùng cá nhân của user hoặc cấu hình ID hợp lệ. Người thường không được tự chọn phân vùng người khác. Chưa đánh giá quyền nhóm/đơn vị.
- begin_document(): chống ID trùng/khác chủ, kiểm tra hash khi gắn lại file; tạo hoặc cập nhật trạng thái Pending/Extracting, thông tin file MinIO và metadata cơ bản.
- templates()/template_version(): chỉ dùng mẫu active, chưa xóa, Published và đúng phiên bản Published hiện hành. Mẫu có UsageScopeJson chỉ cho admin trong phiên bản này.
- conversation()/history()/append_turns()/clear_history(): quản lý hội thoại, tin nhắn, bộ đếm và xóa mềm.
- Không đọc đủ tất cả metadata nghiệp vụ, không triển khai toàn bộ chính sách phân vùng trong schema đính kèm.

**Vị trí hàm:** `now()` dòng 10; `dumps()` dòng 14; `table()` dòng 27; `lock()` dòng 30; `scope()` dòng 42; `add()` dòng 49; `patch()` dòng 56; `rows()` dòng 62; `document()` dòng 66; `document_ids()` dòng 79; `unique_index()` dòng 85; `partition()` dòng 91; `begin_document()` dòng 107; `templates()` dòng 136; `template_version()` dòng 145; `conversation()` dòng 151; `history()` dòng 164; `append_turns()` dòng 174; `clear_history()` dòng 187.

### 6.4. `backend/app/services/ai_documents.py`

- scoped_id(): băm tenant:user:doc_id thành ID 32 ký tự trong chế độ AI SQL.
- metadata(): chuyển cột SQL thành cấu trúc metadata mà API hiện tại đang dùng; đọc FileUrl thành bucket/object_key.
- document_metadata(): đọc theo lô tối đa 500 ID để tránh một query cho từng tài liệu; xử lý cả alias sql:<Id>.
- restrict(): bổ sung filter doc_id được phép vào Qdrant.
- tracked_ingestion(): tạo trạng thái SQL trước khi chạy pipeline; thành công ghi Indexed/Extracted, ChunkCount, LastIndexedTime; lỗi ghi Failed rồi ném lại ngoại lệ. Chặn việc dùng tiền tố sql: để nạp.
- Không có transaction phân tán giữa SQL, Qdrant và MinIO; lỗi có thể để lại file hoặc chunk cần xử lý lại.

**Vị trí hàm:** `scoped_id()` dòng 14; `metadata()` dòng 22; `document_metadata()` dòng 33; `restrict()` dòng 51; `tracked_ingestion()` dòng 61; `run()` dòng 63.

### 6.5. `backend/app/services/ai_templates.py`

- template_fields(): chuyển FieldsJson/StructureJson dạng object hoặc list sang cấu trúc fields/meta/sections của backend cũ. List {Name, Sample} được chuyển thành meta nguồn input.
- list_templates(): đọc mẫu + phiên bản SQL; tạo TemplateBaoCao tạm làm đối tượng tương thích, gắn ai_id, ai_version, ai_hash. Không ghi đối tượng này vào bảng SQLite.
- materialize(): tải file mẫu từ MinIO vào UPLOAD_DIR/ai_templates, đặt tên theo hash tham chiếu/phiên bản; kiểm tra SHA-256 khi DB có hash; tải qua thư mục tạm và đổi tên file.
- Không tự tải URL HTTP bên ngoài hoặc mở đường dẫn tùy ý từ DB. Cache file đã có vẫn được dùng lại.

**Vị trí hàm:** `template_fields()` dòng 15; `list_templates()` dòng 28; `materialize()` dòng 46; `download()` dòng 59.

### 6.6. `backend/app/services/ai_outputs.py`

- Thêm decorator tracked_workflow() bọc bốn workflow: tạo AI_Tasks trước khi chạy; ghi kết quả, tiến độ, tham chiếu sản phẩm và nhật ký sau khi chạy.
- Trạng thái: Processing → Completed; thiếu đầu vào → Cancelled; lỗi → Failed. Chưa có hàng đợi/worker bền hoặc API retry/thông báo UC37.
- Đưa file nguồn của luồng soát/soạn từ file và file đầu ra lên MinIO khi có. Bản local vẫn là file làm việc.
- save_product(): Review → AI_DocumentReviews; Draft → AI_DocumentDrafts; Aggregate → AI_ReportSummaries; Slide → AI_Slides.
- Tạo AI_OutputVersions phiên bản 1, AI_SourceReferences cho source_url hoặc refs/all_refs có doc_id tra được, và AI_ExecutionLogs cho workflow.
- Draft cần template_id/template_version hợp lệ; không tự tạo mẫu giả trong SQL.
- public_result() chỉ bỏ output_path/file_path ở cấp ngoài cùng trước khi lưu JSON; không phải bộ lọc đệ quy mọi dữ liệu nhạy cảm.
- Chưa có cơ chế cập nhật sản phẩm thành phiên bản 2, 3; chưa lưu token/model/config đầy đủ; không nên coi đây là UC37 hoàn chỉnh.

**Vị trí hàm:** `file_url()` dòng 14; `public_result()` dòng 18; `save_product()` dòng 23; `tracked_workflow()` dòng 63; `decorate()` dòng 64.

### 6.7. `backend/app/services/ai_mindmaps.py`

- Thêm adapter async load/save/list_all/delete. Tắt AI SQL thì gọi store JSON cũ; bật thì dùng AI_MindMaps.
- Save kiểm tra quyền đọc tài liệu nguồn, lưu TreeJson, NodeCount, Title và thông tin người dùng.
- Xóa là xóa mềm trên SQL. Không tự chuyển các file JSON sơ đồ đã có sang SQL.
- Admin vẫn theo quy tắc owned của repository cho sơ đồ; không đồng nghĩa mọi cách chia sẻ sơ đồ trong schema đã hoàn tất.

**Vị trí hàm:** `record()` dòng 10; `load()` dòng 15; `save()` dòng 24; `list_all()` dòng 40; `delete()` dòng 49.

### 6.8. `backend/scripts/check_ai_database.py`

- Thêm CLI kiểm tra đọc schema thực tế và SELECT trên 15 bảng.
- Chế độ --write-rollback --user-id: lấy tài khoản ERP thật, tạo bản ghi thử trong transaction và luôn rollback.
- Kiểm tra tài liệu/chỉ mục, ID trùng và alias SQL, mẫu/phiên bản, hội thoại, bốn loại sản phẩm, nguồn, task, tenant/chủ sở hữu và xóa mềm.
- --output ghi báo cáo JSON; không chạy DDL. Rollback không trả lại các giá trị identity đã được SQL Server cấp.

**Vị trí hàm:** `check()` dòng 22; `main()` dòng 99.

### 6.9. `backend/tests/test_ai_mapping.py`

- Thêm các bài kiểm tra tham chiếu MinIO hợp lệ/không hợp lệ, danh tính và tenant, ID khác theo user/tenant, JSON mẫu.
- Kiểm tra filter rỗng không mở toàn kho; hội thoại SQL không đọc cache/store cũ; soạn từ tài liệu dùng mẫu SQL; lỗi nạp ghi Failed.
- Có parametrization: 9 hàm test tương ứng 13 ca chạy ở trạng thái file hiện tại. Phần lớn dùng mock; không thay thế kiểm thử live.

**Vị trí hàm:** `ai_mode()` dòng 15; `test_minio_references()` dòng 20; `test_reject_untrusted_references()` dòng 25; `test_requires_verified_tenant()` dòng 30; `test_document_ids_separate_users_and_tenants()` dòng 40; `test_template_json_formats()` dòng 49; `test_empty_scope_keeps_filter()` dòng 57; `test_sql_history_does_not_use_old_cache()` dòng 69; `test_draft_from_document_uses_published_sql_template()` dòng 86; `test_ingestion_failure_records_failed_state()` dòng 98; `scope()` dòng 62; `scope()` dòng 75; `scope()` dòng 105; `ingest()` dòng 117.

### 6.10. `backend/tests/test_ai_mapping_live.py`

- Thêm bài pytest đánh dấu live, dùng SQL/MinIO/Qdrant thật và tài khoản admin kiểm tra.
- Embedding được giả lập; phần sinh slide thử chỉ tạo fixture file, không gọi LLM sinh nội dung thực.
- Kiểm tra API upload/detail/original/upload trùng, SQL chat, sơ đồ, lưu task/sản phẩm và tải MinIO sau khi xóa bản local.
- Transaction SQL rollback, collection và object riêng của bài thử được dọn trong finally.
- Lượt retry cuối với phiên bản sau sửa ID trùng chưa được chạy vì SSH bị từ chối; không suy diễn là bài này đã xác nhận mọi dòng cuối.

**Vị trí hàm:** `test_real_storage_flow()` dòng 17; `upload()` dòng 50.

### 6.11. `docs/AI_DATABASE.md`

- Thêm hướng dẫn cấu hình AI_DATABASE_URL, phân chia SQL/MinIO/Qdrant, điều kiện quyền, dữ liệu cũ và giới hạn.
- Hướng dẫn profile .env.ai-test, tunnel SQL qua Mac, chạy CLI rollback và backend thử ở cổng 18083.
- Đây là tài liệu vận hành ngắn đã tạo ở đợt trước; báo cáo hiện tại bổ sung kiểm kê chi tiết và diff.

## 7. File không xóa, file không sửa và file ngoài diff

### Không có file bị xóa

Không có trạng thái D trong git diff. Tôi chỉ thay/bỏ một số dòng và nhánh xử lý; diff nguyên văn ở phụ lục cho biết cụ thể từng dòng.

### Những file quan trọng không sửa ở đợt này

- `backend/app/db/models.py`: giữ các model cũ, không viết lại toàn bộ sang SQL AI.
- `backend/app/agents/quyen.py`: giữ chốt quyền chức năng hiện tại. Tuy nhiên đã bổ sung logic tenant/chủ sở hữu trong repository mới; vì vậy không thể nói quyền hoàn toàn không thay đổi hành vi.
- `backend/app/db/erp_auth.py`, `backend/app/db/erp_session.py`: không sửa trong đợt này; không gỡ cơ chế ERP chỉ đọc.
- `backend/.env` và `.env` ở thư mục cha: không chỉnh ở đợt map DB; việc người dùng đổi ERP_DATABASE_URL trước đó là thao tác trước đợt này.
- Frontend: không sửa để bổ sung màn hình quản lý task/phân vùng.

### File đang untracked nhưng không phải do đợt map DB tạo

- `db_sample.txt`: tài liệu cấu trúc DB người dùng cung cấp; không nhận là code tôi tạo/sửa.
- `.DS_Store`: đã có ở đầu phiên; không đưa vào báo cáo diff hoặc commit.

### File local bị Git bỏ qua đã tạo

- `backend/.env.ai-test`: profile riêng trên Mac, quyền file 0600. Có URL AI/ERP cùng DB thử, tenant 94, tắt warmup, collection thử tpv_ai_db_mac. Báo cáo không sao chép giá trị tài khoản/mật khẩu.
- `artifacts/ai-database/validation-summary.json`: tổng hợp bằng chứng kiểm thử từ phiên trước. File này được tạo bằng tổng hợp kết quả công cụ, không phải output tự động từ một lần pytest duy nhất.
- Các archive/manifest/script làm việc trong `/private/tmp`, bao gồm gói đồng bộ, danh sách hash và smoke script. Không phải file code được yêu cầu commit. File cấu hình kết nối tạm trên Mac đã được xóa ở cuối phiên trước.

## 8. Thao tác trên server yuu và dịch vụ ngoài Git

### Đường dẫn đã dùng

- Server: abc@100.105.11.124.
- Bản được sửa/thử: `/home/abc/yuu_workspace/TPV-chatbot-btl`.
- Không tìm thấy thư mục `TPB-chatbot-btl` như chữ trong yêu cầu; đã dùng thư mục TPV thực tế và báo lại.
- Không triển khai đợt này vào `/home/abc/yuu_workspace/chatbot/TPV-chatbot-btl` hoặc `/home/abc/kien_workspace/TPV-chatbot-btl`.

### Đã đồng bộ nhiều hơn riêng các file diff trên Mac

Bản yuu ban đầu ở commit 14383a0 và thiếu mã nền/phụ thuộc hiện có trên Mac. Để chạy thử, ngoài các file thay đổi ở báo cáo, đã chép thêm mã nền backend/app, backend/config, pyproject.toml và uv.lock có trong danh sách đồng bộ. Các file này có thể không hiện trong diff Mac vì đã được commit từ trước trên Mac.

Các bài test liên quan, conftest.py và fixture `backend/tests/fixtures/presenton_modern_template.json` cũng được bổ sung sang yuu khi phát hiện thiếu. Đây là lý do số file được chép lên server lớn hơn 37 file thay đổi trên Mac.

**Giới hạn kiểm kê:** chưa hoàn tất so hash toàn bộ file Mac/yuu ở cuối phiên vì lệnh SSH bị từ chối. Danh sách 37 file trong báo cáo là kiểm kê chính xác trạng thái Mac so với Git; không phải khẳng định đã có bản diff toàn bộ filesystem server.

### Sao lưu đã tạo trên server

- `/tmp/tpv-ai-before-mapping.tgz`: sao lưu những file đã tồn tại trước khi giải nén gói thay đổi đầu.
- `/tmp/tpv-ai-before-full-source.tgz`: sao lưu các file đã tồn tại trước lần bổ sung mã nền rộng hơn.
- Bản sao lưu thứ hai được tạo sau lần đồng bộ đầu; không nên coi nó là snapshot sạch trước toàn bộ đợt. Chưa kiểm tra lại khả năng phục hồi các archive ở lượt hiện tại.

### Môi trường và tiến trình

- Cài bổ sung trong `.venv` của bản yuu riêng: openai 2.54.0, jiter 0.18.0 và seaborn 0.13.2. Không cài vào venv của bản /chatbot/ hoặc kien.
- Tạo `.env.ai-test` trên yuu, quyền 0600, dùng AI_DATABASE_URL và ERP_DATABASE_URL qua 127.0.0.1:17679, tenant 94, tắt warmup, collection tpv_ai_db_yuu.
- Tạo tunnel SSH tạm Mac → SQL Server cho yuu dùng; đã đóng riêng tunnel do phiên này tạo khi kết thúc. Muốn dùng lại profile yuu cần mở lại tunnel như tài liệu vận hành.
- Khởi động backend tạm cổng 18083 để smoke test; tiến trình thử được dừng sau bài kiểm tra. Không khởi động lại backend giao diện cổng 8082.
- Smoke startup đã tạo collection thử `tpv_ai_db_yuu` theo profile; không xóa collection profile này sau smoke. Không có nghĩa các tài liệu SQL đã được nạp vào nó.
- Các collection có tên ngẫu nhiên `tpv_ai_mapping_test_*` và object chỉ thuộc bài live storage đã được dọn ở những lượt bài thử thực sự chạy.
- Có log thử trong backend/data và báo cáo JSON trong artifacts/ai-database trên server; có thể có cache schema/file runtime. Chưa kiểm kê lại toàn bộ file runtime trên server sau khi SSH cuối bị từ chối.

## 9. Database và dữ liệu bị tác động thế nào

- Đã đọc metadata/cột, số đếm và một số giá trị trạng thái/dạng JSON để map đúng schema.
- Không chạy DDL trong các kiểm thử AI: không CREATE TABLE, ALTER TABLE, DROP TABLE.
- Có INSERT/UPDATE dữ liệu thử bên trong transaction SQL rồi rollback, gồm tài liệu, mẫu, chat, sản phẩm, nguồn, task và xóa mềm thử.
- Rollback được kiểm tra lại bằng marker. Không nhận là chưa hề có câu ghi; có ghi thử rồi hoàn tác dữ liệu hàng.
- Identity có thể đã tăng do SQL Server không hoàn tác số ID đã cấp.
- Không tự đổi mẫu Retired thành Published, không tự sửa ID trùng, không chuyển hàng loạt SQLite/JSON, không reindex hàng loạt 393 tài liệu.
- MinIO/Qdrant của bài storage thực sự có file/point thử, sau đó dọn dữ liệu thử của bài. Không có transaction chung tự rollback cả ba dịch vụ.

## 10. Kiểm thử: phần nào đã xác nhận, phần nào chưa

| Kiểm tra | Kết quả ghi nhận | Giới hạn |
|---|---|---|
| Hồi quy trên yuu | 294 passed | Chạy với AI_DATABASE_URL rỗng để giữ chế độ cũ; không phải 294 ca kiểm thử chế độ SQL mới |
| Script SQL thật sau sửa ID trùng | Passed, 15 bảng | Reflection/đọc bảng + ghi thử rollback các thao tác được script liệt kê; không kiểm mọi cột/chức năng của 15 bảng |
| Kiểm thử storage thật trên yuu | 1 passed ở các lượt đã chạy trước sửa ID trùng cuối | SQL/MinIO/Qdrant thật, embedding và phần sinh sản phẩm giả lập; không phải kiểm chất lượng LLM |
| Smoke backend thật với profile mới | Passed | /whoami, /api/reports/templates, /api/documents trả 200; tiến trình 18083 được dừng |
| Kiểm thử mục tiêu Mac sau sửa ID trùng | 23 passed | test_ai_mapping.py + test_document_search.py; không thay lượt storage live cuối |
| Diff whitespace/compile | Đã kiểm tra | Không chứng minh nghiệp vụ hoàn chỉnh |
| So hash Mac/yuu + storage retry cuối | Chưa chạy | Công cụ trả rejected by user cho yêu cầu SSH cuối |

Các lỗi đã gặp trong quá trình chuẩn bị thử:

- Yuu truy cập DB trực tiếp bị login timeout → dùng tunnel tạm qua Mac.
- Yuu thiếu erp_discovery → bổ sung mã nền; thiếu openai/seaborn → cài phụ thuộc dự án.
- Bộ test yuu thiếu test_db_permissions và fixture slide → chép các file test/fixture tương ứng.
- Một test trên Mac lỗi vì backend/data là file, không phải thư mục → chạy lại test với upload/output tạm; không sửa file data đó.
- Smoke danh sách tài liệu trả 503 do ID trùng → thêm xử lý alias và lọc RAG, sau đó smoke trả 200.

Không coi các lỗi môi trường đã được xử lý là lỗi sản phẩm vẫn còn; cũng không suy diễn từ test lưu trữ giả lập rằng mọi workflow LLM đã chạy thành công trên dữ liệu thật.

## 11. Phần chưa hoàn tất và các điểm cần duyệt

- Chưa thực hiện phương án thu gọn bạn vừa trao đổi; toàn bộ phần mở rộng nêu trên vẫn còn trong working tree.
- Chưa có đầy đủ UC37: thiếu API quản lý tác vụ, retry, thông báo, hàng đợi bền và khôi phục task khi tiến trình chết.
- Quyền chia sẻ nhóm/đơn vị và AI_PartitionPermissions chưa được tích hợp đầy đủ; hiện là chốt quyen.py kết hợp tenant/chủ sở hữu và các giới hạn tạm đã mô tả.
- Mẫu có UsageScopeJson bị hạn chế cho admin thay vì diễn giải đầy đủ nội dung policy.
- Nhánh mới yêu cầu mẫu SQL Published; một số nhánh còn cho phép mẫu tổng hợp rỗng đi qua đường dựng file cũ, nên không nên hiểu mọi loại báo cáo đều đã bị bắt buộc dùng file mẫu SQL theo cùng một cách.
- Báo cáo yêu cầu nhập số ký hiệu; chưa có hệ thống cấp số chính thức thay sổ SQLite.
- Phiên bản sản phẩm mới chỉ ghi VersionNo=1; chưa có quy trình chỉnh sửa tạo phiên bản tiếp theo.
- SourceReferences chỉ ghi nguồn mà code nhận được/tra được; chưa bảo đảm mọi nguồn trong mọi loại workflow đã được chuẩn hóa đầy đủ.
- Chưa map AI_Models/AI_AgentConfigs vào cấu hình chạy model, chưa ghi đủ model/token/config cho nhật ký.
- Các bản ghi SQL Indexed chưa chắc có chunk tương ứng trong collection thử; cần bước đối soát/nạp riêng nếu muốn sử dụng toàn bộ kho.
- Profile riêng chưa thay thế `.env` đang vận hành; không nên nói hệ thống đang chạy đã được chuyển hẳn sang DB mới.
- Lượt cuối xác nhận đồng bộ và storage chưa được phép chạy. Hiện tại không có thêm kiểm thử server ở lượt lập báo cáo này.

Nếu thu gọn về kho tài liệu và mẫu, các phần hội thoại, sơ đồ, task/đầu ra/log và các móc workflow/schema/download đi kèm là nhóm cần xem xét gỡ. Đây chỉ là phân nhóm để duyệt, chưa phải chỉ thị xóa file hay một rollback đã thực hiện.

## 12. Phụ lục: diff nguyên văn để đối chiếu từng dòng

Phụ lục được tạo trực tiếp từ Git và nội dung file mới tại thời điểm lập báo cáo. Không đưa `.env`, `.env.ai-test`, `.DS_Store`, db_sample.txt hoặc dữ liệu runtime vào đây. Dấu `-` là dòng cũ bị bỏ/thay; dấu `+` là dòng thêm. Với file mới, toàn bộ dòng đều có `+`.

<details>

<summary>Sửa: backend/.env.example</summary>

SHA-256 nội dung hiện tại: `1536b7983a3353ac17c3c2c1f33fe16c9af2a36c4960da2576bcf73ce507baef`.

```diff
diff --git a/backend/.env.example b/backend/.env.example
index 7e486fe..8c7e06a 100644
--- a/backend/.env.example
+++ b/backend/.env.example
@@ -128,6 +128,10 @@ HISTORY_MAX_TURNS=20
 # ------------------------------------------------- CSDL app tự quản lý ---
 # Sổ văn bản + mẫu báo cáo: thứ hệ thống TỰ SINH ra nên phải ghi được.
 DATABASE_URL=sqlite+aiosqlite:///./data/demo.db
+# Bảng AI SQL Server đã có sẵn; bỏ trống để giữ cách lưu cũ, không chạy DDL.
+AI_DATABASE_URL=
+# Bỏ trống: chọn phân vùng cá nhân của tài khoản; admin có thể chọn ID cùng tenant.
+# AI_DEFAULT_PARTITION_ID=123
 DATABASE_ECHO=false
 
 # ------------------------------------------------- CSDL ERP (CHỈ ĐỌC) ---
```

</details>

<details>

<summary>Sửa: backend/.gitignore</summary>

SHA-256 nội dung hiện tại: `680b8df8270ddd13fce66b8ef33c4169cd510502b9d25ba0a43102255c699905`.

```diff
diff --git a/backend/.gitignore b/backend/.gitignore
index 80e100b..c98763f 100644
--- a/backend/.gitignore
+++ b/backend/.gitignore
@@ -5,6 +5,7 @@ __pycache__/
 .ruff_cache/
 
 .env
+.env.ai-test
 data/
 qdrant_storage/
 *.log
```

</details>

<details>

<summary>Sửa: backend/app/agents/graph.py</summary>

SHA-256 nội dung hiện tại: `0c64e93400882a81729fa23af104c0c1d1452fe06fa9b241159fe4f47d71e794`.

```diff
diff --git a/backend/app/agents/graph.py b/backend/app/agents/graph.py
index b526655..9d6afea 100644
--- a/backend/app/agents/graph.py
+++ b/backend/app/agents/graph.py
@@ -77,6 +77,7 @@ from app.agents.state import (
     StepResult,
 )
 from app.services.conversation import get_memory
+from app.services.ai_outputs import tracked_workflow
 from app.services import errors, session_files
 from app.tools.base import ToolError
 
@@ -200,6 +201,7 @@ def get_document_graph():
     return build_document_graph()
 
 
+@tracked_workflow("Review")
 async def run_document_workflow(
     file_path: str,
     file_name: str = "",
@@ -330,6 +332,7 @@ def get_draft_doc_graph():
     return build_draft_doc_graph()
 
 
+@tracked_workflow("Draft")
 async def run_draft_workflow(
     request: str,
     ma_don_vi: str | None = None,
@@ -364,6 +367,8 @@ async def run_draft_workflow(
         "ma_don_vi": result.get("ma_don_vi", ""),
         "template": result.get("template", {}).get("ma_template", ""),
         "template_name": result.get("template", {}).get("ten_bao_cao", ""),
+        "template_id": result.get("template", {}).get("template_id"),
+        "template_version": result.get("template", {}).get("template_version"),
         "data": result.get("data", {}),
         "sections": result.get("sections", []),
         "validation": result.get("validation", {}),
@@ -416,6 +421,7 @@ def get_aggregate_graph():
     return build_aggregate_graph()
 
 
+@tracked_workflow("Aggregate")
 async def run_aggregate_workflow(
     request: str,
     inputs: dict[str, Any] | None = None,
@@ -433,6 +439,8 @@ async def run_aggregate_workflow(
     return {
         "request": request,
         "params": result.get("params", {}),
+        "template_id": result.get("template_id"),
+        "template_version": result.get("template_version"),
         "data": result.get("data", {}),
         "sections": sections,
         "reconciliation": result.get("reconciliation", []),
@@ -477,6 +485,7 @@ def get_presentation_graph():
     return build_presentation_graph()
 
 
+@tracked_workflow("Slide")
 async def run_presentation_workflow(
     request: str,
     inputs: dict[str, Any] | None = None,
```

</details>

<details>

<summary>Sửa: backend/app/agents/nodes/drafting.py</summary>

SHA-256 nội dung hiện tại: `472d87787f649059743fb498bcd0dadd25d515ebf7f984aa341f13710562c822`.

```diff
diff --git a/backend/app/agents/nodes/drafting.py b/backend/app/agents/nodes/drafting.py
index aa988db..1f13981 100644
--- a/backend/app/agents/nodes/drafting.py
+++ b/backend/app/agents/nodes/drafting.py
@@ -102,6 +102,9 @@ async def select_template_node(state: dict[str, Any]) -> dict[str, Any]:
         repo = TemplateRepository(session)
         catalog = await repo.catalog()
         if not catalog:
+            from app.db.ai_session import enabled
+            if enabled():
+                return {"missing_input": ["Mẫu SQL và phiên bản đang công bố"]}
             return {"template": {"ma_template": "BC_ERP", "ten_bao_cao": state["params"]["loai_bao_cao"],
                                  "file_path": "", "fields": {"sections": [], "meta": {}}}}
 
@@ -125,6 +128,9 @@ async def select_template_node(state: dict[str, Any]) -> dict[str, Any]:
         valid = {t["ma_template"] for t in catalog}
         ma_template = choice.get("ma_template") if choice.get("ma_template") in valid else None
         if ma_template is None:
+            from app.db.ai_session import enabled
+            if enabled():
+                return {"missing_input": ["Chọn mã mẫu SQL phù hợp trong danh mục"]}
             # Không có mẫu phù hợp thì dùng bố cục Word chung cho bảng ERP.
             return {"template": {"ma_template": "BC_ERP", "ten_bao_cao": state["params"]["loai_bao_cao"],
                                  "file_path": "", "fields": {"sections": [], "meta": {}}}}
@@ -133,6 +139,8 @@ async def select_template_node(state: dict[str, Any]) -> dict[str, Any]:
         return {
             "template": {
                 "ma_template": template.ma_template,
+                "template_id": getattr(template, "ai_id", None),
+                "template_version": getattr(template, "ai_version", None),
                 "ten_bao_cao": template.ten_bao_cao,
                 "file_path": template.file_path,
                 "fields": template.fields,
@@ -292,6 +300,9 @@ async def export_node(state: dict[str, Any]) -> dict[str, Any]:
     except ValueError as exc:
         return {"error": str(exc)}
     so_nhap = so_nguoi_dung(inputs)
+    from app.db.ai_session import enabled
+    if enabled() and not so_nhap:
+        return {"missing_input": ["so_ky_hieu: số văn bản được cấp từ sổ chính thức"]}
     try:
         async with session_scope() as session:
             if (trung := await kiem_so_van_ban(session, so_nhap)):
@@ -339,6 +350,9 @@ async def export_node(state: dict[str, Any]) -> dict[str, Any]:
 # --------------------------------------------------------------------------- #
 async def register_node(state: dict[str, Any]) -> dict[str, Any]:
     """Ghi báo cáo vừa sinh vào sổ văn bản để tra cứu lại sau."""
+    from app.db.ai_session import enabled
+    if enabled():
+        return {}  # Service workflow ghi bảng AI thay sổ SQLite.
     output_path = state.get("output_path")
     if not output_path:
         return {}
```

</details>

<details>

<summary>Sửa: backend/app/agents/nodes/drafting_doc.py</summary>

SHA-256 nội dung hiện tại: `ca42c23693e13dee820e727950a883c7e315ac3f8cf7a5425396956f5c2c6c41`.

```diff
diff --git a/backend/app/agents/nodes/drafting_doc.py b/backend/app/agents/nodes/drafting_doc.py
index 1382cdc..06d9aa3 100644
--- a/backend/app/agents/nodes/drafting_doc.py
+++ b/backend/app/agents/nodes/drafting_doc.py
@@ -123,6 +123,16 @@ async def outline_node(state: dict[str, Any]) -> dict[str, Any]:
     if state.get("error") or state.get("missing_input"):
         return {}
 
+    from app.db.ai_session import enabled
+    if enabled():
+        from app.tools.templates import get_template
+        template = await get_template(state.get("inputs", {}).get("ma_template") or state["request"])
+        if not template.get("found"):
+            return {"missing_input": ["ma_template: mã mẫu SQL có phiên bản đang công bố"]}
+        if not template["fields"].get("sections"):
+            return {"error": "Phiên bản mẫu SQL chưa khai báo các mục nội dung"}
+        return {"template": template, "template_choice": {"confidence": 1.0, "reason": "Mẫu SQL đã công bố"}}
+
     data = state["data"]
     tieu_de_mac_dinh = f"Báo cáo tổng hợp nội dung {data['ten_tai_lieu']}"
     muc: list[dict[str, Any]] = []
```

</details>

<details>

<summary>Sửa: backend/app/agents/nodes/report.py</summary>

SHA-256 nội dung hiện tại: `e7560bdab64876b7ecc75f8b8db796eb998357fbc94bac0ad0bc24be2dbf70fc`.

```diff
diff --git a/backend/app/agents/nodes/report.py b/backend/app/agents/nodes/report.py
index 4cbed38..2ab549a 100644
--- a/backend/app/agents/nodes/report.py
+++ b/backend/app/agents/nodes/report.py
@@ -805,11 +805,14 @@ async def export_node(state: dict[str, Any]) -> dict[str, Any]:
     except ValueError as exc:
         return {"output_path": "", "error": str(exc)}
     so_nhap = so_nguoi_dung(inputs)
+    from app.db.ai_session import enabled
+    if enabled() and not so_nhap:
+        return {"error": "Cần inputs.so_ky_hieu được cấp từ sổ chính thức khi dùng DB AI"}
 
     async with session_scope() as session:
         if (trung := await kiem_so_van_ban(session, so_nhap)):
             return {"output_path": "", "error": trung}
-        template = await TemplateRepository(session).get("BC_TONGHOP")
+        template = await TemplateRepository(session).get(inputs.get("ma_template") or "BC_TONGHOP")
         template_file = template.file_path if template else ""
         so_ky_hieu = so_nhap or await next_so_ky_hieu(
             session, params["nam"], "BC-HCQT"
@@ -843,10 +846,15 @@ async def export_node(state: dict[str, Any]) -> dict[str, Any]:
     path = await anyio.to_thread.run_sync(
         lambda: build_docx(payload, output_path, template_file or None)
     )
-    return {"output_path": str(path), "so_ky_hieu": so_ky_hieu}
+    return {"output_path": str(path), "so_ky_hieu": so_ky_hieu,
+            "template_id": getattr(template, "ai_id", None),
+            "template_version": getattr(template, "ai_version", None)}
 
 
 async def register_node(state: dict[str, Any]) -> dict[str, Any]:
+    from app.db.ai_session import enabled
+    if enabled():
+        return {}  # Service workflow ghi bảng AI thay sổ SQLite.
     output_path = state.get("output_path")
     if not output_path:
         return {}
```

</details>

<details>

<summary>Sửa: backend/app/agents/state.py</summary>

SHA-256 nội dung hiện tại: `0ec0e7bd3bd2d7e4cf9cc40a710111a52c2d66b9faa2ca7c2079e5dffcf176a8`.

```diff
diff --git a/backend/app/agents/state.py b/backend/app/agents/state.py
index 18dfd1e..0ebb7ae 100644
--- a/backend/app/agents/state.py
+++ b/backend/app/agents/state.py
@@ -135,6 +135,8 @@ class AggregateState(TypedDict, total=False):
 
     # --- đầu vào ---
     request: str
+    template_id: int | None
+    template_version: int | None
     history: list[dict[str, str]]
     inputs: dict[str, Any]
```

</details>

<details>

<summary>Sửa: backend/app/api/agent.py</summary>

SHA-256 nội dung hiện tại: `8f85ced4a8811eab250b93e77152bfee0bcc2a2671612f0932f5e9792b99f407`.

```diff
diff --git a/backend/app/api/agent.py b/backend/app/api/agent.py
index 79023c5..0fc015d 100644
--- a/backend/app/api/agent.py
+++ b/backend/app/api/agent.py
@@ -247,4 +247,4 @@ async def tools() -> list[ToolInfo]:
 
 @router.get("/download/{filename}", summary="Tải file agent vừa tạo")
 async def download(filename: str) -> FileResponse:
-    return output_file_response(filename)
+    return await output_file_response(filename)
```

</details>

<details>

<summary>Sửa: backend/app/api/documents.py</summary>

SHA-256 nội dung hiện tại: `3d7d4e17385e9fa68def6fc3293974346bcf726485b24cf06c9e3878f6e48958`.

```diff
diff --git a/backend/app/api/documents.py b/backend/app/api/documents.py
index ece7aa1..2401686 100644
--- a/backend/app/api/documents.py
+++ b/backend/app/api/documents.py
@@ -221,7 +221,8 @@ async def _ingest_source(path, filename, metadata, doc_type, force):
     await store.ensure_collection()
     doc_id = make_doc_id_file(filename, path)
     existing = await store.document_summary(doc_id)
-    if existing is None:
+    from app.db.ai_session import enabled, repository
+    if existing is None and not enabled():
         existing = await store.document_summary(make_legacy_doc_id_file(filename, path))
     if existing is not None:
         try:
@@ -229,6 +230,12 @@ async def _ingest_source(path, filename, metadata, doc_type, force):
         except ValueError as exc:
             raise HTTPException(status_code=409, detail=str(exc)) from exc
         if not force:
+            if enabled():
+                async with repository() as repo:
+                    row_id = await repo.begin_document(existing["doc_id"], existing["doc_title"], metadata)
+                    from app.db.ai_repository import now
+                    await repo.patch("KnowledgeDocuments", row_id, IndexStatus="Indexed",
+                        ProcessingStatus="Extracted", ChunkCount=existing["chunk_count"], LastIndexedTime=now())
             return IngestResponse(**existing, already_exists=True,
                                   elapsed_ms=(time.perf_counter() - started) * 1000)
         doc_id = existing["doc_id"]
@@ -418,7 +425,7 @@ async def giao_viec(request: GiaoViecRequest) -> GiaoViecResponse:
 @router.get("/download/{filename}", summary="Tải văn bản giao việc đã sinh")
 async def download(filename: str) -> FileResponse:
     # Chặn cả vượt thư mục, đọc chéo thuê bao, lẫn bản cũ trong cache trình duyệt.
-    return output_file_response(filename)
+    return await output_file_response(filename)
 
 
 @router.get("", response_model=DocumentPage, summary="Danh sách tài liệu đã nạp (phân trang)",
@@ -432,8 +439,9 @@ async def list_documents(
     try:
         docs = await get_vector_store().list_documents()
     except Exception as exc:  # noqa: BLE001
+        logger.exception("Không đọc được danh sách tài liệu")
         raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
-                            detail=f"Không đọc được kho tài liệu: {exc}") from exc
+                            detail="Không đọc được kho tài liệu") from exc
     legal = await _legal_info([d["doc_id"] for d in docs])
     items = [DocumentItem(**d, **legal.get(d["doc_id"], {})) for d in docs]
     if q.strip():
@@ -467,6 +475,15 @@ async def _legal_info(doc_ids: list[str]) -> dict[str, dict]:
 async def remove_document(doc_id: str) -> list[str]:
     """Xoá tài liệu khỏi kho: chunk trong Qdrant + cây phiên bản VBQPPL (nếu bật). Trả về doc_id các
     văn bản gốc đã dựng lại chunk vì văn bản sửa đổi của chúng vừa bị xoá."""
+    from app.db.ai_session import enabled, repository
+    if enabled():
+        async with repository() as repo:
+            row = await repo.document(doc_id, include_unready=True)
+            if row is None:
+                raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu")
+            from app.db.ai_repository import now
+            await repo.patch("KnowledgeDocuments", row["Id"], IsDeleted=True,
+                DeletionTime=now(), DeleterUserId=repo.user_id)
     await get_vector_store().delete_document(doc_id)
     if not get_settings().legal_versioning_enabled:
         return []
```

</details>

<details>

<summary>Sửa: backend/app/api/files.py</summary>

SHA-256 nội dung hiện tại: `b8c9395b89a83dc7c0cebb471ee34f4ddeaebd495f2dd35e13c2288fadaea570`.

```diff
diff --git a/backend/app/api/files.py b/backend/app/api/files.py
index b9d281c..f971a48 100644
--- a/backend/app/api/files.py
+++ b/backend/app/api/files.py
@@ -15,6 +15,8 @@ một 304 rỗng.
 
 from __future__ import annotations
 
+from pathlib import Path
+
 from fastapi import HTTPException, status
 from fastapi.responses import FileResponse
 
@@ -29,8 +31,30 @@ MEDIA_TYPES = {
 NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}
 
 
-def output_file_response(filename: str) -> FileResponse:
+async def output_file_response(filename: str) -> FileResponse:
     """File trong thư mục output: đã chặn vượt thư mục, đọc chéo tenant, và cache cũ."""
+    from app.db.ai_session import enabled, repository
+    if enabled():
+        from sqlalchemy import select
+        from urllib.parse import urlsplit, quote
+        import anyio
+        from app.services.object_storage import ObjectStorage, parse_reference
+        from app.api.documents import _OriginalResponse
+        async with repository() as repo:
+            table = repo.table("Tasks")
+            rows = await repo.rows("Tasks", table.c.TextSearch == filename, owned=True)
+            exists = (await repo.conn.execute(select(table.c.Id).where(table.c.TextSearch == filename))).first()
+            if exists:
+                if not rows or not rows[0]["OutputFileUrl"]:
+                    raise HTTPException(status_code=404, detail="Không tìm thấy sản phẩm")
+                url = urlsplit(rows[0]["OutputFileUrl"])
+                if url.scheme not in {"s3", "minio"}:
+                    raise HTTPException(status_code=404, detail="Sản phẩm chưa có file MinIO")
+                bucket, key = parse_reference(rows[0]["OutputFileUrl"])
+                stream = await anyio.to_thread.run_sync(lambda: ObjectStorage().open(bucket, key))
+                return _OriginalResponse(stream, media_type=MEDIA_TYPES.get(Path(filename).suffix, "application/octet-stream"),
+                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
+                             "Cache-Control": "private, no-store"})
     try:
         ref = storage.resolve_output(filename)
     except storage.StorageError as exc:
```

</details>

<details>

<summary>Sửa: backend/app/api/mindmap.py</summary>

SHA-256 nội dung hiện tại: `48c127b2d54450f7415e852b966bd62234032333840d121f5a21d6046d942212`.

```diff
diff --git a/backend/app/api/mindmap.py b/backend/app/api/mindmap.py
index 169784b..ba9f936 100644
--- a/backend/app/api/mindmap.py
+++ b/backend/app/api/mindmap.py
@@ -35,7 +35,7 @@ from app.schemas.mindmap import (
     SectionRequest,
     SectionResponse,
 )
-from app.services import mindmap_store
+from app.services import ai_mindmaps as mindmap_store
 from app.services.llm import LLMError
 from app.services.mindmap_store import MindmapStoreError
 
@@ -51,9 +51,9 @@ DOC_READ = Depends(quyen.can_quyen("Ai.MindMap", "xem sơ đồ tư duy"))
 MIN_NODES = 3
 
 
-def _record_or_404(doc_id: str) -> dict:
+async def _record_or_404(doc_id: str) -> dict:
     try:
-        record = mindmap_store.load(doc_id)
+        record = await mindmap_store.load(doc_id)
     except MindmapStoreError as exc:
         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
     if record is None:
@@ -85,7 +85,7 @@ async def sources() -> list[MindmapSource]:
         raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                             detail=f"Không đọc được kho tài liệu: {exc}") from exc
 
-    built = {item["doc_id"] for item in mindmap_store.list_all()}
+    built = {item["doc_id"] for item in (await mindmap_store.list_all())}
     return [
         MindmapSource(**doc, has_mindmap=doc["doc_id"] in built)
         for doc in documents
@@ -95,7 +95,7 @@ async def sources() -> list[MindmapSource]:
 @router.get("/documents", response_model=list[MindmapSummary], dependencies=[DOC_READ],
             summary="Các sơ đồ tư duy đã dựng")
 async def list_documents() -> list[MindmapSummary]:
-    return [MindmapSummary(**item) for item in mindmap_store.list_all()]
+    return [MindmapSummary(**item) for item in (await mindmap_store.list_all())]
 
 
 @router.post("/generate", response_model=MindmapResponse, dependencies=[DOC_READ],
@@ -107,7 +107,7 @@ async def generate(request: GenerateRequest) -> MindmapResponse:
                             detail="Thiếu doc_id của tài liệu cần dựng sơ đồ")
 
     try:
-        existing = mindmap_store.load(doc_id)
+        existing = await mindmap_store.load(doc_id)
     except MindmapStoreError as exc:
         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
 
@@ -150,20 +150,20 @@ async def generate(request: GenerateRequest) -> MindmapResponse:
                    f"hoặc model trả về không dùng được - thử dựng lại.",
         )
 
-    mindmap_store.save(doc_id, tree, meta={"doc_title": doc_title, "word_count": word_count})
-    return _response(_record_or_404(doc_id), cached=False)
+    await mindmap_store.save(doc_id, tree, meta={"doc_title": doc_title, "word_count": word_count})
+    return _response(await _record_or_404(doc_id), cached=False)
 
 
 @router.get("/{doc_id}", response_model=MindmapResponse, dependencies=[DOC_READ],
             summary="Đọc lại sơ đồ tư duy đã dựng")
 async def get_mindmap(doc_id: str) -> MindmapResponse:
-    return _response(_record_or_404(doc_id), cached=True)
+    return _response(await _record_or_404(doc_id), cached=True)
 
 
 @router.post("/{doc_id}/section", response_model=SectionResponse, dependencies=[DOC_READ],
              summary="Nội dung chi tiết của một mục")
 async def section(doc_id: str, request: SectionRequest) -> SectionResponse:
-    record = _record_or_404(doc_id)
+    record = await _record_or_404(doc_id)
     tree = record["tree"]
 
     node = find_node(tree, request.node_id)
@@ -207,7 +207,7 @@ async def section(doc_id: str, request: SectionRequest) -> SectionResponse:
     node["summary"] = content["summary"]
     node["key_points"] = content["key_points"]
     node["sources"] = sources
-    mindmap_store.save(doc_id, tree, meta={k: v for k, v in record.items()
+    await mindmap_store.save(doc_id, tree, meta={k: v for k, v in record.items()
                                            if k not in {"tree", "saved_at"}})
 
     return SectionResponse(
@@ -222,7 +222,7 @@ async def section(doc_id: str, request: SectionRequest) -> SectionResponse:
                summary="Xoá sơ đồ tư duy đã dựng")
 async def delete_mindmap(doc_id: str) -> None:
     try:
-        deleted = mindmap_store.delete(doc_id)
+        deleted = await mindmap_store.delete(doc_id)
     except MindmapStoreError as exc:
         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
     if not deleted:
```

</details>

<details>

<summary>Sửa: backend/app/api/presentations.py</summary>

SHA-256 nội dung hiện tại: `cb7e9f502b7ccd29db43e736271ead5a0ecf80a9543583daf380f49b805fef58`.

```diff
diff --git a/backend/app/api/presentations.py b/backend/app/api/presentations.py
index c5f5a44..a59ff10 100644
--- a/backend/app/api/presentations.py
+++ b/backend/app/api/presentations.py
@@ -46,4 +46,4 @@ async def download(filename: str) -> FileResponse:
     Tên file đoán được (`SLIDE_2026-08__t64.pptx`), nên nếu chỉ kiểm "có nằm trong
     thư mục output không" thì bất kỳ ai cũng tải được bộ slide của đơn vị khác.
     """
-    return output_file_response(filename)
+    return await output_file_response(filename)
```

</details>

<details>

<summary>Sửa: backend/app/api/reports.py</summary>

SHA-256 nội dung hiện tại: `ccad916a61b26a28186dd57010a2ab9f606c2961c56aca6392a8d5f9992f938b`.

```diff
diff --git a/backend/app/api/reports.py b/backend/app/api/reports.py
index 5cf68fb..9980267 100644
--- a/backend/app/api/reports.py
+++ b/backend/app/api/reports.py
@@ -77,4 +77,4 @@ async def aggregate(request: AggregateRequest) -> AggregateResponse:
 @router.get("/download/{filename}", summary="Tải file báo cáo đã sinh")
 async def download(filename: str) -> FileResponse:
     # Chặn cả vượt thư mục, đọc chéo thuê bao, lẫn bản cũ trong cache trình duyệt.
-    return output_file_response(filename)
+    return await output_file_response(filename)
```

</details>

<details>

<summary>Sửa: backend/app/core/config.py</summary>

SHA-256 nội dung hiện tại: `c70419148097a61acedd8aab2483ef173df858e78988622f2662eb832b9f05b9`.

```diff
diff --git a/backend/app/core/config.py b/backend/app/core/config.py
index 06701bd..2b41903 100644
--- a/backend/app/core/config.py
+++ b/backend/app/core/config.py
@@ -200,6 +200,10 @@ class Settings(BaseSettings):
     database_url: str = "sqlite+aiosqlite:///./data/demo.db"
     database_echo: bool = False
 
+    # Bảng AI có sẵn trên SQL Server; bỏ trống để giữ luồng lưu trữ cũ.
+    ai_database_url: str = ""
+    ai_default_partition_id: int | None = None
+
     # ------------------------------------------------- CSDL ERP (CHỈ ĐỌC) ---
     # Nguồn sự thật về phòng ban / thiết bị / nhân sự. Quyền được cấp là chỉ đọc,
     # và `app.db.erp_session` chặn mọi câu lệnh ghi ngay tại engine - đừng gỡ chốt
```

</details>

<details>

<summary>Sửa: backend/app/db/repository.py</summary>

SHA-256 nội dung hiện tại: `986500b0fdf2323482c8b45d5d2c3a7c86836e70648f59f3e0bfab90eb7ae79f`.

```diff
diff --git a/backend/app/db/repository.py b/backend/app/db/repository.py
index 1da6c80..c7b7f29 100644
--- a/backend/app/db/repository.py
+++ b/backend/app/db/repository.py
@@ -72,6 +72,10 @@ class TemplateRepository:
         self.session = session
 
     async def list_all(self, loai_bao_cao: str | None = None) -> list[TemplateBaoCao]:
+        from app.db.ai_session import enabled
+        if enabled():
+            from app.services.ai_templates import list_templates
+            return await list_templates(loai_bao_cao)
         stmt = select(TemplateBaoCao).order_by(TemplateBaoCao.ma_template)
         if loai_bao_cao:
             stmt = stmt.where(TemplateBaoCao.loai_bao_cao == loai_bao_cao)
@@ -79,6 +83,11 @@ class TemplateRepository:
         return list(result.scalars())
 
     async def get(self, ma_template: str) -> TemplateBaoCao | None:
+        from app.db.ai_session import enabled
+        if enabled():
+            from app.services.ai_templates import materialize
+            match = next((t for t in await self.list_all() if t.ma_template == ma_template), None)
+            return await materialize(match) if match else None
         return await self.session.get(TemplateBaoCao, ma_template)
 
     async def catalog(self) -> list[dict[str, str]]:
@@ -95,10 +104,16 @@ class VanBanRepository:
         self.session = session
 
     async def get(self, ma_van_ban: str) -> VanBan | None:
+        from app.db.ai_session import enabled
+        if enabled():
+            return await self._ai_document(ma_van_ban)
         return await self.session.get(VanBan, ma_van_ban)
 
     async def get_by_doc_id(self, doc_id: str) -> VanBan | None:
         """Tra ngược từ doc_id trong Qdrant về sổ văn bản."""
+        from app.db.ai_session import enabled
+        if enabled():
+            return await self._ai_document(doc_id)
         result = await self.session.execute(select(VanBan).where(VanBan.doc_id == doc_id).limit(1))
         return result.scalar_one_or_none()
 
@@ -110,6 +125,9 @@ class VanBanRepository:
         return list(result.scalars())
 
     async def upsert(self, van_ban: VanBan) -> VanBan:
+        from app.db.ai_session import enabled
+        if enabled():
+            raise RuntimeError("Chế độ AI SQL ghi qua service tài liệu/sản phẩm, không ghi sổ SQLite")
         merged = await self.session.merge(van_ban)
         await self.session.flush()
         return merged
@@ -120,3 +138,17 @@ class VanBanRepository:
         if van_ban is not None:
             van_ban.doc_id = doc_id
             await self.session.flush()
+
+    async def _ai_document(self, key):
+        from sqlalchemy import or_
+        from app.db.ai_session import repository
+        async with repository() as repo:
+            table = repo.table("KnowledgeDocuments")
+            rows = await repo.rows("KnowledgeDocuments", or_(table.c.DocumentNumber == key,
+                table.c.DocCode == key, table.c.AiServerDocId == key), owned=True)
+            if not rows:
+                return None
+            row = rows[0]
+            return VanBan(ma_van_ban=row["DocCode"] or row["DocumentNumber"], ten_van_ban=row["Title"],
+                loai_van_ban="", noi_gui=row["IssuingAgency"] or "", noi_nhan="", mo_ta="",
+                ngay_van_ban=row["IssuedDate"], file_path="", dang_file="", doc_id=row["AiServerDocId"])
```

</details>

<details>

<summary>Sửa: backend/app/db/session.py</summary>

SHA-256 nội dung hiện tại: `49df1e7d05fe09ed9c92e84d47ffc753d35f8849a0059f144b228a986aee42f1`.

```diff
diff --git a/backend/app/db/session.py b/backend/app/db/session.py
index c2a850b..97089f9 100644
--- a/backend/app/db/session.py
+++ b/backend/app/db/session.py
@@ -112,6 +112,12 @@ async def healthcheck() -> bool:
     from sqlalchemy import text
 
     try:
+        from app.db import ai_session
+        if ai_session.enabled():
+            await ai_session.tables()
+            async with ai_session.get_engine().connect() as connection:
+                await connection.execute(text("SELECT 1"))
+            return True
         async with get_session_factory()() as session:
             await session.execute(text("SELECT 1"))
         return True
```

</details>

<details>

<summary>Sửa: backend/app/main.py</summary>

SHA-256 nội dung hiện tại: `a4886f20e28d1b8e728fe6d9bdbb79f06bc147053e28a4d1d7485cb6d12ca8da`.

```diff
diff --git a/backend/app/main.py b/backend/app/main.py
index 8378cb4..480ed05 100644
--- a/backend/app/main.py
+++ b/backend/app/main.py
@@ -66,7 +66,11 @@ async def lifespan(app: FastAPI):
     except Exception as exc:  # noqa: BLE001 - vẫn cho app chạy để /health báo lỗi
         logger.error("Không khởi tạo được Qdrant: %s", exc)
     try:
-        await conversations.ensure_collection()
+        from app.db import ai_session
+        if ai_session.enabled():
+            await ai_session.tables()
+        else:
+            await conversations.ensure_collection()
         logger.info("Collection hội thoại sẵn sàng: %s", conversations.collection)
     except Exception as exc:  # noqa: BLE001
         # Không chặn app, nhưng phải kêu to: đây là bản gốc của lịch sử chat,
@@ -105,6 +109,8 @@ async def lifespan(app: FastAPI):
     await get_cache().close()
     await dispose_engine()
     await dispose_erp_engine()
+    from app.db.ai_session import dispose
+    await dispose()
 
 
 app = FastAPI(
@@ -221,7 +227,17 @@ async def health() -> HealthResponse:
     conversations = get_memory().store
     conversation_turns = 0
     try:
-        conversation_turns = await conversations.count()
+        from app.db import ai_session
+        if ai_session.enabled():
+            from sqlalchemy import func, select
+            async with ai_session.repository() as repo:
+                table = repo.table("ChatConversations")
+                ids = select(table.c.Id).where(repo.scope(table), table.c.UserId == repo.user_id)
+                messages = repo.table("ChatMessages")
+                conversation_turns = (await repo.conn.execute(select(func.count()).select_from(messages)
+                    .where(messages.c.ConversationId.in_(ids)))).scalar_one()
+        else:
+            conversation_turns = await conversations.count()
     except Exception as exc:  # noqa: BLE001
         logger.debug("Health check collection hội thoại lỗi: %s", exc)
 
@@ -339,6 +355,11 @@ async def _thieu_quyen(request: Request, exc: ThieuQuyen) -> JSONResponse:
     return JSONResponse(status_code=403, content={"detail": exc.message})
 
 
+@app.exception_handler(PermissionError)
+async def _sai_pham_vi(request: Request, exc: PermissionError) -> JSONResponse:
+    return JSONResponse(status_code=403, content={"detail": "Không có quyền truy cập phạm vi dữ liệu này"})
+
+
 @app.get("/whoami", tags=["system"], summary="Backend đang thấy request này là ai")
 async def whoami() -> dict[str, object]:
     """Danh tính mà backend đọc ra từ request, để đội làm giao diện tự đối chiếu.
```

</details>

<details>

<summary>Sửa: backend/app/rag/ingestion.py</summary>

SHA-256 nội dung hiện tại: `93d03db5507f3afa30394eda355fe960071e8e0916ca527ca090a5267d8392a7`.

```diff
diff --git a/backend/app/rag/ingestion.py b/backend/app/rag/ingestion.py
index 7980760..f96eb05 100644
--- a/backend/app/rag/ingestion.py
+++ b/backend/app/rag/ingestion.py
@@ -71,7 +71,8 @@ def make_doc_id(source: str, text: str) -> str:
     thì dùng `make_doc_id_file`, xem docstring ở đó để biết vì sao.
     """
     digest = hashlib.sha256(f"{source}|{text[:4096]}".encode("utf-8")).hexdigest()[:16]
-    return digest
+    from app.services.ai_documents import scoped_id
+    return scoped_id(digest)
 
 
 def make_doc_id_file(source: str, path: Path) -> str:
@@ -80,7 +81,8 @@ def make_doc_id_file(source: str, path: Path) -> str:
     Giữ tham số source để tương thích các caller hiện tại.
     """
     with path.open("rb") as content:
-        return hashlib.file_digest(content, "sha256").hexdigest()[:16]
+        from app.services.ai_documents import scoped_id
+        return scoped_id(hashlib.file_digest(content, "sha256").hexdigest()[:16])
 
 
 def make_legacy_doc_id_file(source: str, path: Path) -> str:
@@ -95,6 +97,9 @@ def make_legacy_doc_id_file(source: str, path: Path) -> str:
 # --------------------------------------------------------------------------- #
 # Pipeline
 # --------------------------------------------------------------------------- #
+from app.services.ai_documents import tracked_ingestion
+
+
 class IngestionPipeline:
     def __init__(
         self,
@@ -105,6 +110,7 @@ class IngestionPipeline:
         self.store = store or get_vector_store()
         self.chunker = Chunker(self.settings)
 
+    @tracked_ingestion
     async def ingest_text(
         self,
         text: str,
```

</details>

<details>

<summary>Sửa: backend/app/rag/retrieval.py</summary>

SHA-256 nội dung hiện tại: `a593c39c51733d935b67912e02a134a2965c9835a7dfbc2eb03b408de72264fd`.

```diff
diff --git a/backend/app/rag/retrieval.py b/backend/app/rag/retrieval.py
index 53efdb3..8a1ff98 100644
--- a/backend/app/rag/retrieval.py
+++ b/backend/app/rag/retrieval.py
@@ -193,6 +193,8 @@ class HybridRetriever:
         top_k: int | None = None,
     ) -> tuple[list[FusedHit], dict[str, int], dict[str, float]]:
         """Chạy 3 nhánh cho mọi biến thể truy vấn rồi gộp bằng RRF."""
+        from app.services.ai_documents import restrict
+        query_filter = await restrict(query_filter)
         cfg = self.settings
         branch_limit = branch_limit or cfg.retrieval_branch_limit
         top_k = top_k or cfg.rrf_top_k
```

</details>

<details>

<summary>Sửa: backend/app/rag/vectorstore.py</summary>

SHA-256 nội dung hiện tại: `f0a88cd62a4af945d94eddc460c5faa0aa2b34fd4c07ebc56fb0190e27a0ed17`.

```diff
diff --git a/backend/app/rag/vectorstore.py b/backend/app/rag/vectorstore.py
index bce09b8..f6ecb25 100644
--- a/backend/app/rag/vectorstore.py
+++ b/backend/app/rag/vectorstore.py
@@ -205,6 +205,10 @@ class QdrantVectorStore:
 
     async def document_metadata(self, doc_ids: Sequence[str]) -> dict[str, dict]:
         """Metadata và số chunk hiện hành; không tải text/vector hoặc quét toàn kho."""
+        from app.db.ai_session import enabled
+        if enabled():
+            from app.services.ai_documents import document_metadata
+            return await document_metadata(doc_ids)
         import asyncio
         gate = asyncio.Semaphore(8)
         fields = ["doc_title", "source", "doc_type", "ingested_at", "storage_bucket",
@@ -229,6 +233,9 @@ class QdrantVectorStore:
         Khác `search`: không xếp hạng và không được bỏ sót đoạn nào - dùng khi
         người dùng muốn đọc lại nguyên văn bản chứ không phải tìm đoạn liên quan.
         """
+        from app.db.ai_session import enabled
+        if enabled() and doc_id not in await self.document_metadata([doc_id]):
+            return []
         payloads: list[dict[str, Any]] = []
         offset = None
         while True:
@@ -252,6 +259,8 @@ class QdrantVectorStore:
 
     async def scroll_filtered(self, query_filter: models.Filter, limit: int = 256) -> list[dict[str, Any]]:
         """Payload (kèm text) của các chunk khớp bộ lọc, theo thứ tự trong file gốc."""
+        from app.services.ai_documents import restrict
+        query_filter = await restrict(query_filter)
         points, _ = await self._client.scroll(collection_name=self.collection, scroll_filter=query_filter,
                                               limit=limit, with_payload=True, with_vectors=False)
         return sorted((dict(p.payload or {}) for p in points), key=lambda p: p.get("chunk_index", 0))
@@ -263,6 +272,15 @@ class QdrantVectorStore:
         collection rồi gộp, và chỉ lấy đúng vài trường payload cần cho danh sách
         chứ không kéo cả `text` về: trường đó là toàn bộ nội dung kho.
         """
+        from app.db.ai_session import enabled, repository
+        if enabled():
+            from app.services.ai_documents import metadata
+            async with repository() as repo:
+                table = repo.table("KnowledgeDocuments")
+                allowed = set(await repo.document_ids())
+                rows = await repo.rows("KnowledgeDocuments", table.c.IndexStatus == "Indexed", owned=True)
+            return [{"doc_id": row["AiServerDocId"] if row["AiServerDocId"] in allowed else f"sql:{row['Id']}",
+                     **metadata(row)} for row in rows]
         docs: dict[str, dict[str, Any]] = {}
         offset = None
         while True:
```

</details>

<details>

<summary>Sửa: backend/app/schemas/documents.py</summary>

SHA-256 nội dung hiện tại: `dad4885de73931cfe4682bbd95cec9db8f2f2aebb288006883e0851ace08ab57`.

```diff
diff --git a/backend/app/schemas/documents.py b/backend/app/schemas/documents.py
index 3f4b900..a6cc56f 100644
--- a/backend/app/schemas/documents.py
+++ b/backend/app/schemas/documents.py
@@ -311,6 +311,7 @@ class GiaoViecResponse(BaseModel):
 
 
 class ReviewResponse(BaseModel):
+    ai_task_id: int | None = None
     document: DocumentInfoModel
     blocks: list[BlockModel] = Field(
         default_factory=list,
```

</details>

<details>

<summary>Sửa: backend/app/schemas/presentations.py</summary>

SHA-256 nội dung hiện tại: `b0eb0ecc114f180c57e97b207f99f1d0254108e9367ace5497d112336f814e11`.

```diff
diff --git a/backend/app/schemas/presentations.py b/backend/app/schemas/presentations.py
index d905d5e..10bb586 100644
--- a/backend/app/schemas/presentations.py
+++ b/backend/app/schemas/presentations.py
@@ -18,6 +18,7 @@ class PresentationRequest(BaseModel):
 
 
 class PresentationResponse(BaseModel):
+    ai_task_id: int | None = None
     request: str
     data: dict[str, Any] = Field(default_factory=dict, description="QueryResult trong data.queries")
     params: dict[str, Any] = Field(default_factory=dict)
```

</details>

<details>

<summary>Sửa: backend/app/schemas/reports.py</summary>

SHA-256 nội dung hiện tại: `dc8d2fb56d61bb356ea933c0b78c3d450334c852b0b7b0bf00898da786dea999`.

```diff
diff --git a/backend/app/schemas/reports.py b/backend/app/schemas/reports.py
index 6f0d6bd..d3dea20 100644
--- a/backend/app/schemas/reports.py
+++ b/backend/app/schemas/reports.py
@@ -72,6 +72,7 @@ class RegulationRef(BaseModel):
 
 
 class DraftResponse(BaseModel):
+    ai_task_id: int | None = None
     nguon: str = Field(default="csdl", description="Nguồn số liệu đã dùng")
     source_document: dict[str, Any] = Field(
         default_factory=dict,
@@ -147,6 +148,7 @@ class AggregateSection(BaseModel):
 
 
 class AggregateResponse(BaseModel):
+    ai_task_id: int | None = None
     request: str
     params: dict[str, Any] = Field(default_factory=dict)
     data: dict[str, Any] = Field(
```

</details>

<details>

<summary>Sửa: backend/app/services/conversation.py</summary>

SHA-256 nội dung hiện tại: `f21b79fadb9f8c5059fa3cf1757fb98637996dbc938fb0b54f142553178bbb58`.

```diff
diff --git a/backend/app/services/conversation.py b/backend/app/services/conversation.py
index 552cd2c..fbf66a6 100644
--- a/backend/app/services/conversation.py
+++ b/backend/app/services/conversation.py
@@ -248,6 +248,10 @@ class ConversationMemory:
 
     async def get_history(self, conversation_id: str,
                           limit: int | None = None) -> list[dict[str, str]]:
+        from app.db.ai_session import enabled, repository
+        if enabled():
+            async with repository() as repo:
+                return await repo.history(conversation_id, limit or get_settings().history_max_turns)
         if not conversation_id:
             return []
         cached = await self.cache.get_history(conversation_id, limit=limit)
@@ -264,6 +268,11 @@ class ConversationMemory:
 
     async def append_turns(self, conversation_id: str,
                            turns: list[tuple[str, str]]) -> None:
+        from app.db.ai_session import enabled, repository
+        if enabled():
+            async with repository() as repo:
+                await repo.append_turns(conversation_id, turns)
+            return
         if not conversation_id:
             return
         for role, content in turns:
@@ -271,6 +280,11 @@ class ConversationMemory:
         await self.store.append_turns(conversation_id, turns)
 
     async def clear_history(self, conversation_id: str) -> None:
+        from app.db.ai_session import enabled, repository
+        if enabled():
+            async with repository() as repo:
+                await repo.clear_history(conversation_id)
+            return
         await self.cache.clear_history(conversation_id)
         await self.store.clear_history(conversation_id)
```

</details>

<details>

<summary>Sửa: backend/app/services/object_storage.py</summary>

SHA-256 nội dung hiện tại: `92b75eac531af246b3084e234f038a89a5b00ffcfa37fd2250aa3a20c883272e`.

```diff
diff --git a/backend/app/services/object_storage.py b/backend/app/services/object_storage.py
index 4a4dcea..c24e216 100644
--- a/backend/app/services/object_storage.py
+++ b/backend/app/services/object_storage.py
@@ -10,6 +10,18 @@ from urllib3 import PoolManager, Timeout
 from app.core.config import get_settings
 
 
+def parse_reference(value):
+    """Nhận minio://bucket/key, minio:bucket/key và s3://bucket/key."""
+    url = urlsplit(value or "")
+    if url.scheme not in {"s3", "minio"}:
+        raise ValueError("Tham chiếu file phải thuộc MinIO")
+    bucket, key = (url.netloc, url.path.lstrip("/")) if url.netloc else (
+        url.path.lstrip("/").partition("/")[0], url.path.lstrip("/").partition("/")[2])
+    if not bucket or not key or url.query or url.fragment:
+        raise ValueError("Tham chiếu MinIO thiếu bucket/object hoặc có tham số lạ")
+    return bucket, key
+
+
 class ObjectStorage:
     def __init__(self):
         self.cfg = get_settings()
```

</details>

<details>

<summary>Sửa: backend/app/tools/templates.py</summary>

SHA-256 nội dung hiện tại: `a3541c2bf9c23f255b49e127d8b2b221b41297a6299aa7b8ae524c2f7f065544`.

```diff
diff --git a/backend/app/tools/templates.py b/backend/app/tools/templates.py
index a2158af..438dc1b 100644
--- a/backend/app/tools/templates.py
+++ b/backend/app/tools/templates.py
@@ -28,6 +28,8 @@ def _as_dict(template: TemplateBaoCao) -> dict[str, Any]:
         "loai_bao_cao": template.loai_bao_cao,
         "mo_ta": template.mo_ta,
         "file_path": template.file_path,
+        "template_id": getattr(template, "ai_id", None),
+        "template_version": getattr(template, "ai_version", None),
         "sections": [
             {"id": s.get("id", ""), "title": s.get("title", ""), "type": s.get("type", "llm")}
             for s in fields.get("sections", [])
@@ -81,6 +83,10 @@ async def get_template(template_type: str) -> dict[str, Any]:
             return {"found": False, "query": needle,
                     "reason": f"Không có mẫu nào khớp với {needle!r}",
                     "candidates": candidates}
+        from app.db.ai_session import enabled
+        if enabled():
+            from app.services.ai_templates import materialize
+            found = await materialize(found)
         return {"found": True, "query": needle, **_as_dict(found), "candidates": candidates}
```

</details>

<details>

<summary>Thêm: backend/app/db/ai_models.py</summary>

SHA-256 nội dung hiện tại: `9f149341fd839d0103b93e70761a4b8bdc1eb66b7f7444f90c2f7fdda5f402a1`.

```diff
--- /dev/null
+++ b/backend/app/db/ai_models.py
@@ -0,0 +1,15 @@
+"""Ánh xạ bảng AI có sẵn bằng reflection; không tạo bảng hoặc sửa schema."""
+from sqlalchemy import MetaData, Table
+
+TABLES = (
+    "AI_KnowledgeDocuments", "AI_DataPartitions", "AI_ReportTemplate",
+    "AI_TemplateVersions", "AI_ChatConversations", "AI_ChatMessages",
+    "AI_Tasks", "AI_DocumentReviews", "AI_DocumentDrafts", "AI_ReportSummaries",
+    "AI_Slides", "AI_OutputVersions", "AI_SourceReferences", "AI_MindMaps", "AI_ExecutionLogs",
+)
+
+
+def reflect(connection):
+    metadata = MetaData()
+    return {name: Table(name, metadata, autoload_with=connection, resolve_fks=False)
+            for name in TABLES}
```

</details>

<details>

<summary>Thêm: backend/app/db/ai_session.py</summary>

SHA-256 nội dung hiện tại: `124fe38cbc44b0ea7025e0a423d06ee48739451845e4d954ed8a88b2ee4965ef`.

```diff
--- /dev/null
+++ b/backend/app/db/ai_session.py
@@ -0,0 +1,51 @@
+"""Kết nối ghi nghiệp vụ AI riêng; ERP vẫn chỉ đọc, không chạy DDL."""
+import asyncio
+from contextlib import asynccontextmanager
+
+from sqlalchemy.ext.asyncio import create_async_engine
+
+from app.core.config import get_settings
+from app.db.ai_models import reflect
+
+_engine = None
+_tables = None
+_lock = asyncio.Lock()
+
+
+def enabled():
+    return bool(get_settings().ai_database_url)
+
+
+def get_engine():
+    global _engine
+    if _engine is None:
+        url = get_settings().ai_database_url
+        if not url:
+            raise RuntimeError("Chưa cấu hình AI_DATABASE_URL")
+        _engine = create_async_engine(url, pool_pre_ping=True, hide_parameters=True)
+    return _engine
+
+
+async def tables():
+    global _tables
+    if _tables is None:
+        async with _lock:
+            if _tables is None:
+                async with get_engine().connect() as conn:
+                    _tables = await conn.run_sync(reflect)
+    return _tables
+
+
+@asynccontextmanager
+async def repository():
+    from app.db.ai_repository import AIRepository
+    mapped = await tables()
+    async with get_engine().begin() as conn:
+        yield AIRepository(conn, mapped)
+
+
+async def dispose():
+    global _engine, _tables
+    if _engine is not None:
+        await _engine.dispose()
+    _engine = _tables = None
```

</details>

<details>

<summary>Thêm: backend/app/db/ai_repository.py</summary>

SHA-256 nội dung hiện tại: `8a37a04ab00400eaa2a2d9942370e671d91af1f9f85bb7bba2454ebfebc2b46f`.

```diff
--- /dev/null
+++ b/backend/app/db/ai_repository.py
@@ -0,0 +1,191 @@
+"""Đọc/ghi bảng AI thực tế; luôn giới hạn tenant, chủ sở hữu và xóa mềm."""
+import json
+from datetime import datetime, timezone
+
+from sqlalchemy import and_, func, insert, or_, select, update
+
+from app.core.context import current_principal
+
+
+def now():
+    return datetime.now(timezone.utc).replace(tzinfo=None)
+
+
+def dumps(value):
+    return json.dumps(value, ensure_ascii=False, default=str)
+
+
+class AIRepository:
+    def __init__(self, connection, tables):
+        self.conn, self.tables = connection, tables
+        self.principal = current_principal()
+        access = self.principal.access
+        if access is None or access.tenant_id != self.principal.tenant_id:
+            raise PermissionError("Cần tài khoản ERP hợp lệ trong đúng tenant")
+        self.user_id = access.user_id
+
+    def table(self, name):
+        return self.tables["AI_" + name]
+
+    async def lock(self, key):
+        if self.conn.dialect.name == "mssql":
+            import hashlib
+            from sqlalchemy import text
+            scope = key if key.startswith("document:") else f"{self.principal.tenant_id}:{self.user_id}:{key}"
+            resource = hashlib.sha256(scope.encode()).hexdigest()
+            result = await self.conn.execute(text("DECLARE @r int; EXEC @r=sp_getapplock "
+                "@Resource=:key, @LockMode='Exclusive', @LockOwner='Transaction', @LockTimeout=15000; SELECT @r"),
+                {"key": "tpv-ai-" + resource})
+            if result.scalar_one() < 0:
+                raise TimeoutError("Không lấy được khóa ghi AI")
+
+    def scope(self, table, owned=False):
+        terms = [table.c.TenantId == self.principal.tenant_id, table.c.IsDeleted == False]
+        if owned and not self.principal.is_admin:
+            owner = table.c.UserId if "UserId" in table.c else table.c.CreatorUserId
+            terms.append(owner == self.user_id)
+        return and_(*terms)
+
+    async def add(self, name, **values):
+        table = self.table(name)
+        audit = dict(TenantId=self.principal.tenant_id, CreatorUserId=self.user_id,
+                     CreationTime=now(), IsDeleted=False)
+        values = {**{k: v for k, v in audit.items() if k in table.c}, **values}
+        return (await self.conn.execute(insert(table).values(**values).returning(table.c.Id))).scalar_one()
+
+    async def patch(self, name, row_id, **values):
+        table = self.table(name)
+        if "LastModificationTime" in table.c:
+            values.update(LastModificationTime=now(), LastModifierUserId=self.user_id)
+        await self.conn.execute(update(table).where(table.c.Id == row_id, self.scope(table, True)).values(**values))
+
+    async def rows(self, name, *terms, owned=False):
+        table = self.table(name)
+        return list((await self.conn.execute(select(table).where(self.scope(table, owned), *terms))).mappings())
+
+    async def document(self, doc_id, include_unready=False):
+        table = self.table("KnowledgeDocuments")
+        sql_id = doc_id.removeprefix("sql:") if doc_id.startswith("sql:") else ""
+        terms = [table.c.Id == int(sql_id)] if sql_id.isdigit() else [table.c.AiServerDocId == doc_id]
+        if not include_unready:
+            terms.append(table.c.IndexStatus == "Indexed")
+            if not sql_id.isdigit():
+                terms.append(self.unique_index())
+        rows = await self.rows("KnowledgeDocuments", *terms, owned=True)
+        if len(rows) > 1:
+            raise ValueError("Trùng AiServerDocId trong cùng phạm vi dữ liệu")
+        return dict(rows[0]) if rows else None
+
+    async def document_ids(self):
+        table = self.table("KnowledgeDocuments")
+        rows = await self.conn.execute(select(table.c.AiServerDocId).where(
+            self.scope(table, True), table.c.IndexStatus == "Indexed", self.unique_index()))
+        return list(rows.scalars())
+
+    def unique_index(self):
+        table = self.table("KnowledgeDocuments")
+        unique = select(table.c.AiServerDocId).where(table.c.IsDeleted == False,
+            table.c.AiServerDocId.is_not(None), table.c.AiServerDocId != "").group_by(table.c.AiServerDocId)
+        return table.c.AiServerDocId.in_(unique.having(func.count() == 1).correlate(None))
+
+    async def partition(self, partition_id=None):
+        from app.core.config import get_settings
+        table = self.table("DataPartitions")
+        partition_id = partition_id or get_settings().ai_default_partition_id
+        terms = [self.scope(table), table.c.IsActive == True]
+        if partition_id:
+            terms.append(table.c.Id == partition_id)
+        else:
+            terms.extend([table.c.PartitionType == "Personal", table.c.OwnerUserId == self.user_id])
+        if not self.principal.is_admin:
+            terms.extend([table.c.PartitionType == "Personal", table.c.OwnerUserId == self.user_id])
+        row = (await self.conn.execute(select(table.c.Id).where(*terms).order_by(table.c.Id))).first()
+        if row is None:
+            raise PermissionError("Chưa có phân vùng cá nhân hợp lệ; admin cần chọn phân vùng cùng tenant")
+        return row[0]
+
+    async def begin_document(self, doc_id, title, metadata):
+        await self.lock("document:" + doc_id)
+        existing = await self.document(doc_id, include_unready=True)
+        table = self.table("KnowledgeDocuments")
+        collisions = (await self.conn.execute(select(func.count()).select_from(table).where(
+            table.c.AiServerDocId == doc_id, table.c.IsDeleted == False))).scalar_one()
+        if collisions and (existing is None or collisions > 1):
+            raise PermissionError("doc_id trùng hoặc thuộc phạm vi dữ liệu khác")
+        # Không nhận partition/folder tùy ý từ metadata của client.
+        if existing:
+            values = dict(IndexStatus="Pending", ProcessingStatus="Extracting")
+            if metadata.get("storage_bucket") and metadata.get("storage_object_key"):
+                checksum = metadata.get("checksum_sha256")
+                if existing["FileHashSha256"] and checksum != existing["FileHashSha256"]:
+                    raise ValueError("File khác SHA-256 phải nạp bằng doc_id mới")
+                values.update(FileUrl=f"minio://{metadata['storage_bucket']}/{metadata['storage_object_key']}",
+                    FileHashSha256=checksum, FileSizeBytes=metadata.get("file_size_bytes", 0))
+            await self.patch("KnowledgeDocuments", existing["Id"], **values)
+            return existing["Id"]
+        partition = await self.partition()
+        bucket, key = metadata.get("storage_bucket"), metadata.get("storage_object_key")
+        return await self.add("KnowledgeDocuments", AiServerDocId=doc_id,
+            DocumentNumber="AI-" + doc_id, PartitionId=partition, Title=title[:500],
+            FileName=metadata.get("original_filename") or title[:260],
+            FileUrl=f"minio://{bucket}/{key}" if bucket and key else "",
+            FileSizeBytes=metadata.get("file_size_bytes", 0), FileHashSha256=metadata.get("checksum_sha256"),
+            HasTextLayer=True, ProcessingStatus="Extracting", QualityStatus="Pending",
+            VersionNo=1, LifecycleStatus="Active", ApprovalStatus="Draft", IndexStatus="Pending", ChunkCount=0)
+
+    async def templates(self, kind=None):
+        table = self.table("ReportTemplate")
+        terms = [table.c.IsActive == True, table.c.Status == "Published"]
+        if kind:
+            terms.append(table.c.TemplateType == kind)
+        rows = await self.rows("ReportTemplate", *terms)
+        # Chính sách mẫu có phạm vi riêng cần được triển khai trước khi mở cho người khác.
+        return [dict(r) for r in rows if self.principal.is_admin or not r["UsageScopeJson"]]
+
+    async def template_version(self, template):
+        table = self.table("TemplateVersions")
+        rows = await self.rows("TemplateVersions", table.c.TemplateId == template["Id"],
+            table.c.VersionNo == template["CurrentVersionNo"], table.c.Status == "Published")
+        return dict(rows[0]) if rows else None
+
+    async def conversation(self, key, create=False):
+        if create:
+            await self.lock("conversation:" + key)
+        table = self.table("ChatConversations")
+        # Hội thoại luôn thuộc người dùng, kể cả admin không đọc hộ qua API chat.
+        terms = [table.c.AiConversationId == key, table.c.UserId == self.user_id]
+        rows = await self.rows("ChatConversations", *terms)
+        if rows:
+            return rows[0]["Id"]
+        if create:
+            return await self.add("ChatConversations", UserId=self.user_id, Title="Cuộc trò chuyện mới",
+                                  AiConversationId=key, AiFeatureType="Chatbot", MessageCount=0)
+
+    async def history(self, key, limit):
+        conversation_id = await self.conversation(key)
+        if conversation_id is None:
+            return []
+        table = self.table("ChatMessages")
+        rows = list((await self.conn.execute(select(table).where(table.c.ConversationId == conversation_id)
+            .order_by(table.c.Id.desc()).limit(limit))).mappings())
+        return [{"role": "user" if r["IsFromUser"] else "assistant", "content": r["Message"],
+                 "ts": r["CreationTime"].replace(tzinfo=timezone.utc).timestamp()} for r in reversed(rows)]
+
+    async def append_turns(self, key, turns):
+        turns = [(role, text) for role, text in turns if role in {"user", "assistant"} and text]
+        if not turns:
+            return 0
+        conversation_id = await self.conversation(key, create=True)
+        for role, content in turns:
+            await self.add("ChatMessages", ConversationId=conversation_id, Message=content,
+                           IsFromUser=role == "user", NoEvidence=False)
+        table = self.table("ChatConversations")
+        await self.conn.execute(update(table).where(table.c.Id == conversation_id, self.scope(table),
+            table.c.UserId == self.user_id).values(LastMessageTime=now(), MessageCount=table.c.MessageCount+len(turns)))
+        return len(turns)
+
+    async def clear_history(self, key):
+        conversation_id = await self.conversation(key)
+        if conversation_id is not None:
+            await self.patch("ChatConversations", conversation_id, IsDeleted=True,
+                             DeletionTime=now(), DeleterUserId=self.user_id)
```

</details>

<details>

<summary>Thêm: backend/app/services/ai_documents.py</summary>

SHA-256 nội dung hiện tại: `c454493c2ce952abf90c96cfba2b98c4966072861c37f9b496267e3ec8718f2b`.

```diff
--- /dev/null
+++ b/backend/app/services/ai_documents.py
@@ -0,0 +1,86 @@
+"""Liên kết sổ tài liệu SQL với file MinIO và chỉ mục Qdrant."""
+import hashlib
+from datetime import timezone
+from functools import wraps
+from urllib.parse import urlsplit
+
+from qdrant_client import models
+
+from app.core.context import current_principal
+from app.db.ai_session import enabled, repository
+from app.db.ai_repository import now
+
+
+def scoped_id(doc_id):
+    if not enabled():
+        return doc_id
+    principal = current_principal()
+    user = principal.access.user_id if principal.access else principal.user_id
+    return hashlib.sha256(f"{principal.tenant_id}:{user}:{doc_id}".encode()).hexdigest()[:32]
+
+
+def metadata(row):
+    url = urlsplit(row["FileUrl"] or "")
+    from app.services.object_storage import parse_reference
+    bucket, key = parse_reference(row["FileUrl"]) if url.scheme in {"s3", "minio"} else (None, None)
+    return dict(doc_title=row["Title"], source=row["FileName"], original_filename=row["FileName"],
+        chunk_count=row["ChunkCount"], file_size_bytes=row["FileSizeBytes"],
+        checksum_sha256=row["FileHashSha256"], ingested_at=row["CreationTime"].replace(tzinfo=timezone.utc).timestamp(),
+        storage_bucket=bucket, storage_object_key=key,
+        doc_type=row["FileName"].rsplit(".", 1)[-1].lower() if "." in row["FileName"] else "")
+
+
+async def document_metadata(doc_ids):
+    from sqlalchemy import and_, or_
+    async with repository() as repo:
+        result = {}
+        table = repo.table("KnowledgeDocuments")
+        ids = list(dict.fromkeys(doc_ids))
+        for start in range(0, len(ids), 500):
+            batch = ids[start:start+500]
+            sql_ids = [int(key[4:]) for key in batch if key.startswith("sql:") and key[4:].isdigit()]
+            rows = await repo.rows("KnowledgeDocuments", or_(table.c.Id.in_(sql_ids),
+                and_(table.c.AiServerDocId.in_(batch), repo.unique_index())),
+                                   table.c.IndexStatus == "Indexed", owned=True)
+            for row in rows:
+                key = f"sql:{row['Id']}" if row["Id"] in sql_ids else row["AiServerDocId"]
+                result[key] = metadata(row)
+        return result
+
+
+async def restrict(query_filter=None):
+    if not enabled():
+        return query_filter
+    async with repository() as repo:
+        allowed = await repo.document_ids()
+    # Danh sách rỗng phải khớp rỗng; không được biến thành bỏ lọc.
+    return models.Filter(must=[*( [query_filter] if query_filter else []),
+        models.FieldCondition(key="doc_id", match=models.MatchAny(any=allowed))])
+
+
+def tracked_ingestion(function):
+    @wraps(function)
+    async def run(self, text, doc_title, doc_id=None, source="", doc_type="", metadata=None, **kwargs):
+        if not enabled():
+            return await function(self, text, doc_title, doc_id, source, doc_type, metadata, **kwargs)
+        from app.rag.ingestion import make_doc_id
+        doc_id = doc_id or make_doc_id(source or doc_title, text)
+        async with repository() as repo:
+            if doc_id.startswith("sql:"):
+                raise ValueError("Tiền tố sql: dành cho định danh metadata, không dùng để nạp")
+            row_id = await repo.begin_document(doc_id, doc_title, metadata or {})
+        try:
+            result = await function(self, text, doc_title, doc_id, source, doc_type, metadata, **kwargs)
+            if not result.chunk_count:
+                raise ValueError("Tài liệu không tạo được đoạn nội dung")
+            async with repository() as repo:
+                await repo.patch("KnowledgeDocuments", row_id, IndexStatus="Indexed",
+                    ProcessingStatus="Extracted", ChunkCount=result.chunk_count, LastIndexedTime=now())
+            return result
+        except BaseException:
+            import anyio
+            with anyio.CancelScope(shield=True):
+                async with repository() as repo:
+                    await repo.patch("KnowledgeDocuments", row_id, IndexStatus="Failed", ProcessingStatus="Failed")
+            raise
+    return run
```

</details>

<details>

<summary>Thêm: backend/app/services/ai_templates.py</summary>

SHA-256 nội dung hiện tại: `459f864e7afa3b2111e2375bd0e603b807ded698f63b8baf68dbc81eb12a382b`.

```diff
--- /dev/null
+++ b/backend/app/services/ai_templates.py
@@ -0,0 +1,71 @@
+"""Đọc mẫu đã công bố từ SQL; tải file MinIO vào cache cục bộ có kiểm soát."""
+import hashlib
+import json
+from pathlib import Path
+from urllib.parse import urlsplit
+
+import anyio
+
+from app.core.config import get_settings
+from app.db.ai_session import repository
+from app.db.models import TemplateBaoCao
+from app.services.object_storage import ObjectStorage, parse_reference
+
+
+def template_fields(fields_json, structure_json):
+    fields, structure = json.loads(fields_json or "{}"), json.loads(structure_json or "{}")
+    if isinstance(fields, list):
+        fields = {"meta": {f["Name"]: {"source": "input", "example": f.get("Sample", "")}
+                           for f in fields if isinstance(f, dict) and f.get("Name")}}
+    if isinstance(structure, list):
+        structure = {"sections": [{"id": str(i+1), "title": title, "type": "llm"}
+                                   if isinstance(title, str) else title for i, title in enumerate(structure)]}
+    if not isinstance(fields, dict) or not isinstance(structure, dict):
+        raise ValueError("Cấu trúc JSON của mẫu không hợp lệ")
+    return {**structure, **fields}
+
+
+async def list_templates(kind=None):
+    async with repository() as repo:
+        rows = await repo.templates(kind)
+        result = []
+        for row in rows:
+            version = await repo.template_version(row)
+            if version is None:
+                continue
+            fields = template_fields(version["FieldsJson"], version["StructureJson"])
+            template = TemplateBaoCao(ma_template=row["Code"], ten_bao_cao=row["Name"],
+                loai_bao_cao=row["TemplateType"], mo_ta=row["Description"] or "",
+                file_path=version["FileUrl"], truong_du_lieu=json.dumps(fields, ensure_ascii=False))
+            template.ai_id, template.ai_version = row["Id"], version["VersionNo"]
+            template.ai_hash = version["FileHashSha256"]
+            result.append(template)
+        return result
+
+
+async def materialize(template):
+    url = urlsplit(template.file_path or "")
+    if url.scheme not in {"s3", "minio"}:
+        # Không tự tải URL bên ngoài hoặc mở đường dẫn lấy từ DB.
+        if template.file_path:
+            raise ValueError("File mẫu cần tham chiếu s3://bucket/object trên MinIO")
+        return template
+    cfg = get_settings()
+    bucket, key = parse_reference(template.file_path)
+    cache = Path(cfg.upload_dir) / "ai_templates"
+    cache.mkdir(parents=True, exist_ok=True)
+    name = hashlib.sha256(f"{bucket}/{key}:{template.ai_hash}".encode()).hexdigest() + Path(key).suffix
+    target = cache / name
+    def download():
+        import os
+        import tempfile
+        with tempfile.TemporaryDirectory(dir=cache) as directory:
+            path = Path(directory) / name
+            ObjectStorage().download(bucket, key, path, name)
+            if template.ai_hash and ObjectStorage.checksum(path) != template.ai_hash:
+                raise ValueError("SHA-256 file mẫu không khớp phiên bản SQL")
+            os.replace(path, target)
+    if not target.exists():
+        await anyio.to_thread.run_sync(download)
+    template.file_path = str(target)
+    return template
```

</details>

<details>

<summary>Thêm: backend/app/services/ai_outputs.py</summary>

SHA-256 nội dung hiện tại: `2181554728e1f7628f7e7d5653ba25d2813e90bfe91e7b8cff94bc3c2d591dcb`.

```diff
--- /dev/null
+++ b/backend/app/services/ai_outputs.py
@@ -0,0 +1,123 @@
+"""Lưu tác vụ và sản phẩm workflow vào SQL; file bền trên MinIO."""
+import inspect
+import time
+from functools import wraps
+from pathlib import Path
+
+import anyio
+
+from app.db.ai_repository import dumps, now
+from app.db.ai_session import enabled, repository
+from app.services.object_storage import ObjectStorage
+
+
+def file_url(meta):
+    return f"minio://{meta['storage_bucket']}/{meta['storage_object_key']}"
+
+
+def public_result(result):
+    # Đường dẫn cục bộ chỉ dùng trong tiến trình; DB chung lưu tham chiếu file.
+    return {k: v for k, v in result.items() if k not in {"output_path", "file_path"}}
+
+
+async def save_product(repo, kind, inputs, result, url):
+    title = str(inputs.get("request") or inputs.get("file_name") or "Sản phẩm AI")[:500]
+    content = dumps(public_result(result))
+    if kind == "Review":
+        name = "DocumentReviews"
+        values = dict(UserId=repo.user_id, FileName=Path(inputs.get("file_name") or inputs["file_path"]).name,
+            FileUrl=inputs.get("source_url"), ResultJson=content, IssuesJson=dumps(result.get("findings", [])),
+            IssueCount=len(result.get("findings", [])), AcceptedCount=0, IgnoredCount=0)
+    elif kind == "Draft":
+        name = "DocumentDrafts"
+        if not result.get("template_id"):
+            raise ValueError("Đầu ra cần mẫu SQL đã công bố để lưu AI_DocumentDrafts")
+        values = dict(UserId=repo.user_id, Title=title, TemplateId=result["template_id"],
+            TemplateVersionNo=result["template_version"], Request=inputs["request"][:4000],
+            SectionsJson=dumps(result.get("sections", [])), Status="Completed", VersionNo=1)
+    elif kind == "Aggregate":
+        name = "ReportSummaries"
+        values = dict(Title=title, Status="Completed", ResultSummaryJson=content,
+            ResultFileUrl=url, TemplateId=result.get("template_id"), VersionNo=1,
+            HasDiscrepancy=result.get("has_discrepancy", False), StrictNumbers=True)
+    else:
+        name = "Slides"
+        values = dict(Title=title, Status="Completed", OutlineJson=content, FileUrl=url, VersionNo=1)
+    row_id = await repo.add(name, **values)
+    await repo.add("OutputVersions", OwnerType=kind, OwnerId=row_id, VersionNo=1,
+                   ContentJson=content, FileUrl=url, ChangeNote="Tạo bởi backend AI")
+    if inputs.get("source_url"):
+        await repo.add("SourceReferences", OwnerType=kind, OwnerId=row_id,
+                       ExternalSourceRef=inputs["source_url"])
+    for ref in result.get("refs", []) or result.get("all_refs", []):
+        if not isinstance(ref, dict):
+            continue
+        doc = await repo.document(str(ref.get("doc_id") or ""))
+        if doc:
+            await repo.add("SourceReferences", OwnerType=kind, OwnerId=row_id, DocumentId=doc["Id"],
+                DocumentVersionNo=doc["VersionNo"], Snippet=ref.get("text") or ref.get("quote"),
+                PageNumber=ref.get("page"))
+    return name, row_id
+
+
+def tracked_workflow(kind):
+    def decorate(function):
+        @wraps(function)
+        async def run(*args, **kwargs):
+            if not enabled():
+                return await function(*args, **kwargs)
+            bound = inspect.signature(function).bind(*args, **kwargs)
+            inputs = dict(bound.arguments)
+            inputs.pop("history", None)
+            async with repository() as repo:
+                task_id = await repo.add("Tasks", UserId=repo.user_id, TaskType=kind,
+                    AgentCode=function.__name__, Status="Processing", ProgressPercentage=0,
+                    InputJson=dumps({k: v for k, v in inputs.items() if k != "file_path"}),
+                    NotifyOnFinish=False, StartTime=now())
+            try:
+                # File nguồn của soát tài liệu cũng được lưu bền.
+                source = inputs.get("file_path")
+                if not source and inputs.get("file_id"):
+                    from app.services.storage import resolve
+                    source = str(resolve(inputs["file_id"], kinds=("upload",)).path)
+                if source:
+                    meta = await anyio.to_thread.run_sync(lambda: ObjectStorage().upload(Path(source), Path(source).name))
+                    inputs["source_url"] = file_url(meta)
+                started = time.perf_counter()
+                result = await function(*args, **kwargs)
+                failed = result.get("error") or result.get("validation", {}).get("status") == "failed"
+                waiting = bool(result.get("missing_input"))
+                path = Path(result["output_path"]) if result.get("output_path") else None
+                url = None
+                if path and not failed and not waiting:
+                    from app.services.storage import resolve_output
+                    path = resolve_output(str(path)).path
+                    meta = await anyio.to_thread.run_sync(lambda: ObjectStorage().upload(path, path.name))
+                    url = file_url(meta)
+                async with repository() as repo:
+                    related = {}
+                    if not failed and not waiting and (kind == "Review" or url):
+                        name, row_id = await save_product(repo, kind, inputs, result, url)
+                        related = dict(RelatedEntityType="AI_" + name, RelatedEntityId=row_id)
+                    await repo.patch("Tasks", task_id, Status="Failed" if failed else "Cancelled" if waiting else "Completed",
+                        ProgressPercentage=100, EndTime=now(), ResultJson=dumps(public_result(result)),
+                        InputJson=dumps({k: v for k, v in inputs.items() if k != "file_path"}),
+                        OutputFileUrl=url, TextSearch=path.name if path else None,
+                        ErrorMessage=str(failed) if failed else None, **related)
+                    await repo.add("ExecutionLogs", UserId=repo.user_id, EventType=kind,
+                        TaskId=task_id, AgentCode=function.__name__, Status="Failed" if failed else "Completed",
+                        DurationMs=int((time.perf_counter()-started)*1000),
+                        HasCitations=bool(result.get("refs") or result.get("all_refs")), NoEvidence=False,
+                        OutputFileUrl=url, UserQuery=str(inputs.get("request") or "")[:4000])
+                return {**result, "ai_task_id": task_id}
+            except BaseException as exc:
+                with anyio.CancelScope(shield=True):
+                    async with repository() as repo:
+                        await repo.patch("Tasks", task_id, Status="Failed", EndTime=now(),
+                                         ErrorMessage=type(exc).__name__)
+                        await repo.add("ExecutionLogs", UserId=repo.user_id, EventType=kind,
+                            TaskId=task_id, Status="Failed", HasCitations=False, NoEvidence=False,
+                            ErrorMessage=type(exc).__name__)
+                raise
+        return run
+    return decorate
```

</details>

<details>

<summary>Thêm: backend/app/services/ai_mindmaps.py</summary>

SHA-256 nội dung hiện tại: `4d4c9a27f6d045ec7b3d4e24067430228bbec1e753a174f89b85ca305a6537ce`.

```diff
--- /dev/null
+++ b/backend/app/services/ai_mindmaps.py
@@ -0,0 +1,58 @@
+"""Lưu/đọc sơ đồ tư duy trên SQL khi bật kết nối AI; giữ JSON cho chế độ cũ."""
+import json
+
+from app.db.ai_repository import dumps, now
+from app.db.ai_session import enabled, repository
+from app.documents.mindmap import count_nodes
+from app.services import mindmap_store
+
+
+def record(row):
+    return dict(doc_id=row["DocId"], doc_title=row["Title"], tree=json.loads(row["TreeJson"]),
+                saved_at=(row["LastModificationTime"] or row["CreationTime"]).isoformat())
+
+
+async def load(doc_id):
+    if not enabled():
+        return mindmap_store.load(doc_id)
+    async with repository() as repo:
+        table = repo.table("MindMaps")
+        rows = await repo.rows("MindMaps", table.c.DocId == doc_id, owned=True)
+        return record(rows[0]) if rows else None
+
+
+async def save(doc_id, tree, meta=None):
+    if not enabled():
+        return mindmap_store.save(doc_id, tree, meta)
+    async with repository() as repo:
+        if not await repo.document(doc_id):
+            raise PermissionError("Không được đọc tài liệu nguồn của sơ đồ")
+        table = repo.table("MindMaps")
+        rows = await repo.rows("MindMaps", table.c.DocId == doc_id, owned=True)
+        values = dict(Title=(meta or {}).get("doc_title") or tree.get("title") or doc_id,
+                      TreeJson=dumps(tree), NodeCount=count_nodes(tree))
+        if rows:
+            await repo.patch("MindMaps", rows[0]["Id"], **values)
+        else:
+            await repo.add("MindMaps", UserId=repo.user_id, DocId=doc_id, **values)
+
+
+async def list_all():
+    if not enabled():
+        return mindmap_store.list_all()
+    async with repository() as repo:
+        rows = await repo.rows("MindMaps", owned=True)
+        return [{"doc_id": r["DocId"], "doc_title": r["Title"], "node_count": r["NodeCount"],
+                 "saved_at": (r["LastModificationTime"] or r["CreationTime"]).isoformat()} for r in rows]
+
+
+async def delete(doc_id):
+    if not enabled():
+        return mindmap_store.delete(doc_id)
+    async with repository() as repo:
+        table = repo.table("MindMaps")
+        rows = await repo.rows("MindMaps", table.c.DocId == doc_id, owned=True)
+        if rows:
+            await repo.patch("MindMaps", rows[0]["Id"], IsDeleted=True,
+                             DeletionTime=now(), DeleterUserId=repo.user_id)
+        return bool(rows)
```

</details>

<details>

<summary>Thêm: backend/scripts/check_ai_database.py</summary>

SHA-256 nội dung hiện tại: `1b8f57bf0fd218a0feb2e365ad18bdc2df5219d79a13bd5e89987ffc34e4b7f5`.

```diff
--- /dev/null
+++ b/backend/scripts/check_ai_database.py
@@ -0,0 +1,117 @@
+"""Kiểm tra schema AI và ghi thử trong transaction luôn rollback; không chạy DDL."""
+import argparse
+import asyncio
+import json
+import sys
+import uuid
+from dataclasses import replace
+from pathlib import Path
+
+sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
+
+from sqlalchemy import func, select
+from sqlalchemy.ext.asyncio import AsyncSession
+
+from app.core.context import Principal, use_principal
+from app.db.ai_repository import AIRepository
+from app.db.ai_session import dispose, get_engine, tables
+from app.db.erp_auth import ErpAuthRepository
+from app.services.ai_outputs import save_product
+
+
+async def check(args):
+    mapped = await tables()
+    report = {"tables": list(mapped), "ddl_executed": False, "write_mode": "rollback" if args.write_rollback else "none"}
+    async with get_engine().connect() as conn:
+        for table in mapped.values():
+            await conn.execute(select(table).limit(0))
+        await conn.rollback()
+        if args.write_rollback:
+            if args.user_id is None:
+                raise ValueError("Ghi thử cần --user-id của tài khoản ERP thực")
+            async with AsyncSession(bind=conn) as session:
+                access = await ErpAuthRepository(session).find_user(user_id=args.user_id)
+            await conn.rollback()
+            if access is None:
+                raise ValueError("Không tìm thấy tài khoản ERP")
+            principal = Principal(access.tenant_id, str(access.user_id), "default", access)
+            marker = "test-" + uuid.uuid4().hex
+            transaction = await conn.begin()
+            try:
+                with use_principal(principal):
+                    repo = AIRepository(conn, mapped)
+                    document_id = await repo.begin_document(marker, "Kiểm thử ánh xạ", {})
+                    await repo.patch("KnowledgeDocuments", document_id, IndexStatus="Indexed", ChunkCount=1)
+                    assert (await repo.document(marker))["Id"] == document_id
+                    assert marker in await repo.document_ids()
+                    table = repo.table("KnowledgeDocuments")
+                    source = dict((await repo.rows("KnowledgeDocuments", table.c.Id == document_id))[0])
+                    source.pop("Id")
+                    source["DocumentNumber"] += "-dup"
+                    duplicate_id = await repo.add("KnowledgeDocuments", **source)
+                    assert marker not in await repo.document_ids()
+                    assert await repo.document(marker) is None
+                    assert (await repo.document(f"sql:{document_id}"))["Id"] == document_id
+                    await repo.patch("KnowledgeDocuments", duplicate_id, IsDeleted=True)
+                    await repo.append_turns(marker, [("user", "Câu hỏi thử"), ("assistant", "Câu trả lời thử")])
+                    assert len(await repo.history(marker, 20)) == 2
+                    template_id = await repo.add("ReportTemplate", Code=marker, Name="Mẫu kiểm thử", IsActive=True,
+                        IsReference=False, Status="Published", TemplateType="Report", CurrentVersionNo=1)
+                    await repo.add("TemplateVersions", TemplateId=template_id, VersionNo=1, FileName="test.docx",
+                        FileUrl="s3://test/test.docx", FileSizeBytes=0, Status="Published", FieldsJson="{}", StructureJson="{}")
+                    found = next(t for t in await repo.templates() if t["Id"] == template_id)
+                    assert (await repo.template_version(found))["VersionNo"] == 1
+                    for kind in ("Review", "Draft", "Aggregate", "Slide"):
+                        name, product_id = await save_product(repo, kind,
+                            {"request": "Kiểm thử", "file_path": "test.txt"},
+                            {"template_id": template_id, "template_version": 1, "all_refs": [{"doc_id": marker}]},
+                            "s3://test/output.docx")
+                        assert product_id
+                    task_id = await repo.add("Tasks", UserId=repo.user_id, TaskType="Draft", Status="Processing",
+                        ProgressPercentage=0, NotifyOnFinish=False)
+                    await repo.patch("Tasks", task_id, Status="Completed", ProgressPercentage=100)
+                    # Danh tính khác không đọc được hội thoại/tài liệu vừa tạo.
+                    other = replace(access, user_id=access.user_id + 10000000, is_static_admin=False)
+                    with use_principal(replace(principal, user_id=str(other.user_id), access=other)):
+                        private = AIRepository(conn, mapped)
+                        assert await private.document(marker) is None
+                        assert await private.history(marker, 20) == []
+                    with use_principal(replace(principal, tenant_id=(access.tenant_id or 0)+1)):
+                        try:
+                            AIRepository(conn, mapped)
+                        except PermissionError:
+                            pass
+                        else:
+                            raise AssertionError("Không chặn tenant sai")
+                    await repo.clear_history(marker)
+                    assert await repo.history(marker, 20) == []
+            finally:
+                await transaction.rollback()
+            docs = mapped["AI_KnowledgeDocuments"]
+            assert not (await conn.execute(select(docs.c.Id).where(docs.c.AiServerDocId == marker))).first()
+            report["checks"] = ["document_index", "published_template_version", "chat_history", "four_output_types",
+                "source_references", "task_status", "owner_isolation", "tenant_mismatch", "duplicate_index_ids",
+                "sql_metadata_alias", "soft_delete", "rollback_verified"]
+    report["status"] = "passed"
+    return report
+
+
+async def main():
+    parser = argparse.ArgumentParser(description=__doc__)
+    parser.add_argument("--write-rollback", action="store_true")
+    parser.add_argument("--user-id", type=int)
+    parser.add_argument("--output", type=Path)
+    args = parser.parse_args()
+    try:
+        report = await check(args)
+        rendered = json.dumps(report, ensure_ascii=False, indent=2)
+        if args.output:
+            args.output.parent.mkdir(parents=True, exist_ok=True)
+            args.output.write_text(rendered + "\n")
+        print(rendered)
+    finally:
+        await dispose()
+
+
+if __name__ == "__main__":
+    asyncio.run(main())
```

</details>

<details>

<summary>Thêm: backend/tests/test_ai_mapping.py</summary>

SHA-256 nội dung hiện tại: `d8db0d7e22e1682206c41bf5cf3c2ebe1b22de92135a68f03ef5f92f9e75bab9`.

```diff
--- /dev/null
+++ b/backend/tests/test_ai_mapping.py
@@ -0,0 +1,121 @@
+"""Kiểm tra ranh giới dữ liệu và tương thích khi bật ánh xạ SQL AI."""
+from contextlib import asynccontextmanager
+from unittest.mock import AsyncMock
+
+import pytest
+
+from app.core.config import get_settings
+from app.core.context import Principal, use_principal
+from app.db.ai_repository import AIRepository
+from app.db.erp_auth import AccountAccess
+from app.services.object_storage import parse_reference
+
+
+@pytest.fixture
+def ai_mode(monkeypatch):
+    monkeypatch.setattr(get_settings(), "ai_database_url", "sqlite+aiosqlite://")
+
+
+@pytest.mark.parametrize("value", ["minio://bucket/a/b.pdf", "minio:bucket/a/b.pdf", "s3://bucket/a/b.pdf"])
+def test_minio_references(value):
+    assert parse_reference(value) == ("bucket", "a/b.pdf")
+
+
+@pytest.mark.parametrize("value", ["https://example.com/a", "minio://bucket", "minio://bucket/a?token=x"])
+def test_reject_untrusted_references(value):
+    with pytest.raises(ValueError):
+        parse_reference(value)
+
+
+def test_requires_verified_tenant():
+    with use_principal(Principal(94, "407")):
+        with pytest.raises(PermissionError):
+            AIRepository(None, {})
+    access = AccountAccess(407, 94, "test", "test", "")
+    with use_principal(Principal(95, "407", access=access)):
+        with pytest.raises(PermissionError):
+            AIRepository(None, {})
+
+
+def test_document_ids_separate_users_and_tenants(ai_mode):
+    from app.services.ai_documents import scoped_id
+    ids = []
+    for tenant, user in [(94, "1"), (94, "2"), (95, "1")]:
+        with use_principal(Principal(tenant, user)):
+            ids.append(scoped_id("same-bytes"))
+    assert len(set(ids)) == 3
+
+
+def test_template_json_formats():
+    from app.services.ai_templates import template_fields
+    fields = template_fields('[{"Name":"nguoi_ky","Sample":"A"}]', '["Tình hình","Kiến nghị"]')
+    assert fields["meta"]["nguoi_ky"]["source"] == "input"
+    assert len(fields["sections"]) == 2
+    assert template_fields('{"sections":[]}', '{}') == {"sections": []}
+
+
+async def test_empty_scope_keeps_filter(ai_mode, monkeypatch):
+    from app.services import ai_documents
+    repo = AsyncMock()
+    repo.document_ids.return_value = []
+    @asynccontextmanager
+    async def scope():
+        yield repo
+    monkeypatch.setattr(ai_documents, "repository", scope)
+    query = await ai_documents.restrict()
+    assert query.must[0].match.any == []
+
+
+async def test_sql_history_does_not_use_old_cache(ai_mode, monkeypatch):
+    from app.db import ai_session
+    from app.services.conversation import ConversationMemory
+    repo, cache, store = AsyncMock(), AsyncMock(), AsyncMock()
+    repo.history.return_value = []
+    @asynccontextmanager
+    async def scope():
+        yield repo
+    monkeypatch.setattr(ai_session, "repository", scope)
+    memory = ConversationMemory(cache, store)
+    assert await memory.get_history("old-id") == []
+    await memory.append_turns("old-id", [("user", "test")])
+    repo.append_turns.assert_awaited_once()
+    cache.get_history.assert_not_awaited()
+    store.append_turns.assert_not_awaited()
+
+
+async def test_draft_from_document_uses_published_sql_template(ai_mode, monkeypatch):
+    from app.agents.nodes.drafting_doc import outline_node
+    from app.tools import templates
+    template = {"found": True, "template_id": 12, "template_version": 3,
+                "fields": {"sections": [{"id": "1", "title": "Nội dung"}]}}
+    get = AsyncMock(return_value=template)
+    monkeypatch.setattr(templates, "get_template", get)
+    result = await outline_node({"request": "Soạn báo cáo", "inputs": {"ma_template": "BC_SQL"}})
+    assert result["template"]["template_id"] == 12
+    get.assert_awaited_once_with("BC_SQL")
+
+
+async def test_ingestion_failure_records_failed_state(ai_mode, monkeypatch):
+    from app.services import ai_documents
+    repo = AsyncMock()
+    repo.document.return_value = {"Id": 1}
+    repo.begin_document.return_value = 1
+    repo.conn.execute.return_value.first.return_value = None
+    @asynccontextmanager
+    async def scope():
+        yield repo
+    monkeypatch.setattr(ai_documents, "repository", scope)
+    # Thay truy vấn chống trùng bằng câu lệnh SQL thật trên bảng tối thiểu.
+    from sqlalchemy import Table, Column, Integer, String, Boolean, MetaData
+    repo.table = lambda _: Table("docs", MetaData(), Column("Id", Integer),
+        Column("AiServerDocId", String), Column("IsDeleted", Boolean))
+    from unittest.mock import MagicMock
+    response = MagicMock()
+    response.first.return_value = None
+    repo.conn.execute.return_value = response
+    @ai_documents.tracked_ingestion
+    async def ingest(self, *args, **kwargs):
+        raise RuntimeError("Qdrant down")
+    with pytest.raises(RuntimeError, match="Qdrant down"):
+        await ingest(None, "text", "title", "id")
+    assert repo.patch.call_args.kwargs["IndexStatus"] == "Failed"
```

</details>

<details>

<summary>Thêm: backend/tests/test_ai_mapping_live.py</summary>

SHA-256 nội dung hiện tại: `85cefdd093c12a6b430f9e47203e760f6d0c5a0714afabd695c69bc05c0d80f4`.

```diff
--- /dev/null
+++ b/backend/tests/test_ai_mapping_live.py
@@ -0,0 +1,121 @@
+"""Thử luồng API với SQL/MinIO/Qdrant thật; giả lập embedding, luôn dọn dữ liệu thử."""
+import os
+import uuid
+from contextlib import asynccontextmanager
+from dataclasses import replace
+from pathlib import Path
+
+import httpx
+import pytest
+from fastapi import FastAPI
+from sqlalchemy import select
+from sqlalchemy.ext.asyncio import AsyncSession
+
+pytestmark = pytest.mark.live
+
+
+async def test_real_storage_flow(monkeypatch, tmp_path):
+    from app.core.config import get_settings
+    from app.core.context import Principal, use_principal
+    from app.db import ai_session
+    from app.db.ai_repository import AIRepository
+    from app.db.erp_auth import ErpAuthRepository
+    from app.api import documents, files
+    from app.rag import ingestion
+    from app.rag.chunking import Chunk
+    from app.rag.embedding import HybridEmbedding, SparseEmbedding
+    from app.rag.vectorstore import QdrantVectorStore
+    from app.services import ai_documents, ai_outputs, ai_mindmaps
+    from app.services.conversation import ConversationMemory
+    from app.services.object_storage import ObjectStorage
+
+    if not os.getenv("AI_TEST_STORAGE") or not ai_session.enabled():
+        pytest.skip("Cần AI_DATABASE_URL và AI_TEST_STORAGE=1")
+    cfg = get_settings()
+    monkeypatch.setattr(cfg, "qdrant_collection", "tpv_ai_mapping_test_" + uuid.uuid4().hex)
+    monkeypatch.setattr(cfg, "doc_card_enabled", False)
+    monkeypatch.setattr(cfg, "legal_versioning_enabled", False)
+    monkeypatch.setattr(cfg, "output_dir", str(tmp_path))
+    store = QdrantVectorStore(cfg)
+    pipe = ingestion.IngestionPipeline(cfg, store)
+    monkeypatch.setattr(pipe.chunker, "split", lambda text, meta: [Chunk(text, 0, metadata=meta)])
+    monkeypatch.setattr(ingestion, "embed_documents", lambda texts: [HybridEmbedding(
+        [1.0] + [0.0] * (cfg.dense_vector_size-1), SparseEmbedding([1], [1.0]), SparseEmbedding([2], [1.0])) for _ in texts])
+    monkeypatch.setattr(documents, "get_vector_store", lambda: store)
+    monkeypatch.setattr(documents, "get_ingestion_pipeline", lambda: pipe)
+    app = FastAPI()
+    app.include_router(documents.router)
+    objects = set()
+    original_upload = ObjectStorage.upload
+    def upload(self, path, filename):
+        meta = original_upload(self, path, filename)
+        objects.add((meta["storage_bucket"], meta["storage_object_key"]))
+        return meta
+    monkeypatch.setattr(ObjectStorage, "upload", upload)
+    mapped = await ai_session.tables()
+    marker = uuid.uuid4().hex
+    try:
+        async with ai_session.get_engine().connect() as conn:
+            async with AsyncSession(bind=conn) as session:
+                access = await ErpAuthRepository(session).find_user(user_id=int(os.getenv("AI_TEST_USER_ID", "407")))
+            await conn.rollback()
+            assert access and access.is_static_admin
+            principal = Principal(access.tenant_id, str(access.user_id), access=access)
+            transaction = await conn.begin()
+            try:
+                with use_principal(principal):
+                    repo = AIRepository(conn, mapped)
+                    @asynccontextmanager
+                    async def scope():
+                        yield repo
+                    for module in (ai_session, ai_documents, ai_outputs, ai_mindmaps):
+                        monkeypatch.setattr(module, "repository", scope)
+                    await store.ensure_collection()
+                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
+                        content = ("Tài liệu kiểm thử ánh xạ " + marker).encode()
+                        response = await client.post("/api/documents/upload", files={"file": ("mapping.txt", content)})
+                        assert response.status_code == 200, response.text
+                        doc_id = response.json()["doc_id"]
+                        assert (await repo.document(doc_id))["ChunkCount"] == 1
+                        assert (await client.get(f"/api/documents/{doc_id}/detail")).status_code == 200
+                        original = await client.get(f"/api/documents/{doc_id}/original")
+                        assert original.content == content
+                        repeated = await client.post("/api/documents/upload", files={"file": ("mapping.txt", content)})
+                        assert repeated.json()["already_exists"]
+                        memory = ConversationMemory()
+                        await memory.append_turns(marker, [("user", "Câu hỏi"), ("assistant", "Trả lời")])
+                        assert len(await memory.get_history(marker)) == 2
+                        tree = {"id": "root", "title": "Thử", "children": []}
+                        await ai_mindmaps.save(doc_id, tree)
+                        assert (await ai_mindmaps.load(doc_id))["tree"] == tree
+                        await ai_mindmaps.delete(doc_id)
+                        assert await ai_mindmaps.load(doc_id) is None
+                        path = tmp_path / ("test__t" + str(access.tenant_id) + "__" + marker + ".pptx")
+                        path.write_bytes(b"fixture-output-" + marker.encode())
+                        @ai_outputs.tracked_workflow("Slide")
+                        async def generate(request):
+                            return {"output_path": str(path), "brief": "Nội dung thử"}
+                        result = await generate("Kiểm thử sản phẩm")
+                        assert result["ai_task_id"]
+                        path.unlink()  # Tải vẫn thành công khi không còn bản cục bộ.
+                        download = await files.output_file_response(path.name)
+                        assert download.media_type.endswith("presentation")
+                        assert download.stream.read() == b"fixture-output-" + marker.encode()
+                        download.stream.close()
+                        download.stream.release_conn()
+                        other = replace(access, user_id=access.user_id+10000000, is_static_admin=False)
+                        with use_principal(replace(principal, user_id=str(other.user_id), access=other)):
+                            private = AIRepository(conn, mapped)
+                            assert await private.document(doc_id) is None
+                            assert await private.history(marker, 20) == []
+            finally:
+                await transaction.rollback()
+            table = mapped["AI_ChatConversations"]
+            assert not (await conn.execute(select(table.c.Id).where(table.c.AiConversationId == marker))).first()
+    finally:
+        await store.client.delete_collection(store.collection)
+        await store.close()
+        storage = ObjectStorage()
+        for bucket, key in objects:
+            storage.client.remove_object(bucket, key)
+        await ai_session.dispose()
```

</details>

<details>

<summary>Thêm: docs/AI_DATABASE.md</summary>

SHA-256 nội dung hiện tại: `f055a81bd2379181e9406e8ccd6b4d7181e795c40e046cbb14f422e30c369422`.

```diff
--- /dev/null
+++ b/docs/AI_DATABASE.md
@@ -0,0 +1,99 @@
+# Ánh xạ database AI có sẵn
+
+Backend đọc schema thực tế bằng SQLAlchemy reflection trong `app/db/ai_models.py`.
+Không tạo bảng, thêm cột hoặc sửa dữ liệu mẫu. Các file mới có chú thích tiếng Việt.
+
+## Cấu hình
+
+- `AI_DATABASE_URL`: kết nối đọc/ghi bảng `AI_*`; để trống giữ cách lưu cũ.
+- `ERP_DATABASE_URL`: kết nối đọc tài khoản/quyền và truy vấn ERP, vẫn chặn ghi.
+- Hai URL cần trỏ cùng DB chứa tài khoản ABP tương ứng. Không đổi `DATABASE_URL`
+  sang SQL Server: các bảng pháp lý cũ vẫn sử dụng kết nối đó.
+- `AI_DEFAULT_PARTITION_ID`: tùy chọn cho admin, phải thuộc đúng tenant và còn hoạt động.
+  Người dùng thường chỉ nạp vào phân vùng cá nhân của chính mình.
+
+Danh tính phải được xác minh qua ERP. Không suy user ID từ tên đăng nhập trong thao tác ghi.
+Tenant `NULL` chỉ khớp dữ liệu tenant `NULL`, không có nghĩa đọc tất cả tenant.
+
+## Luồng dữ liệu
+
+| Chức năng | Nơi lưu chính |
+|---|---|
+| Metadata tài liệu, trạng thái lập chỉ mục | `AI_KnowledgeDocuments` |
+| Mẫu và phiên bản đang Published | `AI_ReportTemplate`, `AI_TemplateVersions` |
+| Hội thoại, tin nhắn | `AI_ChatConversations`, `AI_ChatMessages` |
+| Soát tài liệu, soạn báo cáo, tổng hợp, slide | Bảng đầu ra tương ứng + `AI_Tasks` |
+| Phiên bản đầu ra, nguồn tham chiếu có doc_id | `AI_OutputVersions`, `AI_SourceReferences` |
+| Nhật ký workflow | `AI_ExecutionLogs` |
+| Sơ đồ tư duy | `AI_MindMaps` |
+| File gốc và file đầu ra | MinIO; SQL lưu tham chiếu `minio://bucket/key` |
+| Nội dung chia đoạn và embedding | Qdrant; nối SQL qua `AiServerDocId` |
+
+`ai_repository.py` chứa truy vấn dùng chung. Các service `ai_documents`,
+`ai_templates`, `ai_outputs`, `ai_mindmaps` nối chúng vào luồng cũ.
+
+Nạp tài liệu: SQL Pending → MinIO/embedding/Qdrant → SQL Indexed/Extracted.
+Lỗi nạp đánh dấu Failed; không trả các đoạn đang nạp/lỗi vào RAG.
+Upload API giữ cách lưu MinIO trước khi xử lý. SQL/Qdrant/MinIO không có transaction
+phân tán: khi lỗi có thể còn file/chunk cần dọn hoặc chạy lại, không báo thành công giả.
+ID mới phân biệt tenant và người nạp để tránh ghi đè chỉ mục chéo người dùng.
+
+Workflow: tạo task Processing → chạy → lưu file MinIO → ghi sản phẩm, phiên bản,
+nhật ký và kết thúc task trong một transaction. Response có `ai_task_id`.
+Thiếu đầu vào được đánh dấu Cancelled, lỗi là Failed. Không bổ sung API UC37 ở đợt này.
+File xuất cục bộ vẫn là bản làm việc; API tải kết quả đã đăng ký lấy file từ MinIO.
+
+Hội thoại SQL là nguồn chính khi bật cấu hình; không đọc cache/Qdrant cũ để tránh
+lẫn lịch sử theo ID hội thoại. Các danh sách tài liệu đã lưu vẫn dùng JSON như trước.
+
+## Phạm vi quyền và dữ liệu cũ
+
+- Giữ chốt quyền chức năng trong `agents/quyen.py`.
+- Tài liệu: người dùng đọc bản ghi do mình tạo; admin đọc trong cùng tenant.
+  Chưa mở thêm quyền chia sẻ nhóm/đơn vị từ `AI_PartitionPermissions`.
+- Hội thoại luôn theo đúng người dùng. Mẫu có UsageScopeJson chỉ mở cho admin
+  cho đến khi triển khai bộ đánh giá phạm vi đầy đủ; không tự đoán quyền.
+- Chỉ dùng mẫu và phiên bản Published đúng CurrentVersionNo. Dữ liệu kiểm tra ban đầu
+  có 45 mẫu nhưng các phiên bản đều Retired: cần bên quản lý mẫu công bố phiên bản hợp lệ.
+- Không tự chuyển SQLite/JSON cũ, không tự nạp lại 393 tài liệu SQL. Một bản ghi
+  Indexed trên SQL chưa chứng minh collection Qdrant của môi trường này có nội dung.
+- Dữ liệu hiện có trùng một số `AiServerDocId`: bản ghi đó dùng `doc_id=sql:<Id>`
+  để xem metadata/bản gốc; loại khỏi RAG đến khi có liên kết chỉ mục duy nhất.
+  Không tự chọn một bản ghi hoặc sửa ID đã có.
+- Chế độ SQL AI yêu cầu `inputs.so_ky_hieu` do người dùng cung cấp; không dùng sổ
+  SQLite cũ để tự cấp số cho DB mới. Nhánh soạn từ tài liệu chọn mẫu qua `inputs.ma_template`.
+- Không thay cấu hình model trong `.env` bằng `AI_Models` ở đợt này.
+
+## Chạy thử trên yuu
+
+Thư mục: `/home/abc/yuu_workspace/TPV-chatbot-btl`, khác bản `/chatbot/` chạy cổng 8082.
+Profile riêng `backend/.env.ai-test` không đưa vào Git và không sửa `.env` đang có.
+Yuu kết nối trực tiếp SQL Server bị timeout; bài thử dùng reverse tunnel qua Mac.
+
+Mở tunnel trên Mac và giữ cửa sổ đó:
+
+```bash
+ssh -N -o ExitOnForwardFailure=yes \
+  -R 127.0.0.1:17679:dbnews.tpvtech.vn:7679 abc@100.105.11.124
+```
+
+Trên yuu, trong `backend`, chạy kiểm tra schema và ghi thử rollback:
+
+```bash
+.venv/bin/python -c 'from dotenv import load_dotenv; load_dotenv(".env.ai-test", override=True); import runpy,sys; sys.argv=["check_ai_database.py","--write-rollback","--user-id","407"]; runpy.run_path("scripts/check_ai_database.py",run_name="__main__")'
+```
+
+Chạy backend thử riêng, cổng 18083:
+
+```bash
+.venv/bin/python -c 'from dotenv import load_dotenv; load_dotenv(".env.ai-test", override=True); import uvicorn; uvicorn.run("app.main:app",host="127.0.0.1",port=18083)'
+```
+
+Forward cổng trên Mac: `ssh -N -L 18083:127.0.0.1:18083 abc@100.105.11.124`.
+Mở `http://127.0.0.1:18083/docs`, dùng header `X-Tenant-Id: 94`, `X-User-Id: 407`
+cho tài khoản admin đã kiểm tra. Nạp file thử rồi kiểm tra detail/original và lịch sử chat.
+
+Bài `tests/test_ai_mapping_live.py` chạy SQL/MinIO/Qdrant thật, giả lập embedding và
+phần sinh sản phẩm để kiểm tra luồng lưu trữ; không đánh giá chất lượng model.
+SQL luôn rollback, collection và object riêng của bài thử được dọn cuối bài.
+Rollback không hoàn tác số identity đã cấp. Không chạy bài ghi thử đồng thời trên cùng connection.
```

</details>


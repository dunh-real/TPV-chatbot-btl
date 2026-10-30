# Sửa theo thỏa thuận BE–AI ngày 29/10

## Đã sửa

| Phần | Hành vi mới |
|---|---|
| Upload kho | Form `erp_doc_id` + `version_no`: kiểm tra quyền/tenant/phiên bản/SHA-256; chỉ thay index và trạng thái đúng bản ghi. Không tạo kho cá nhân hoặc ghi đè metadata BE. Cặp tham số phải gửi cùng nhau. |
| Đính kèm | `/api/agent/upload` nhận `ingest=false`; không nạp kho. Giữ mặc định true để tương thích client AI cũ. BE phải gửi false rõ ràng. |
| Mã tài liệu | Công khai `sql:<ID>`; bộ lọc search/QA phân giải cả mã SQL và mã băm cũ sang index có quyền. Danh sách/search/citations trả mã SQL. |
| DELETE | Theo duyệt bổ sung của người dùng: chỉ gỡ index, đặt `IndexStatus=NotIndexed`, `ChunkCount=0`. Không xóa mềm bản ghi ERP, không xóa file MinIO; giữ lịch sử pháp lý. |
| DOCX | Giữ nội dung Điều viết gọn trên một dòng. Parser cũng đọc container Word `w:sdt` để không bỏ thân tài liệu. |
| Báo cáo | Draft/aggregate thiếu số ký hiệu vẫn xuất bản nháp; không tự sinh số `01/...`. Mô tả API và danh sách trường bắt buộc của mẫu đã sửa. |
| Tác vụ ERP | Header `X-Erp-Task-Id` nhận số dương; kiểm tra tenant, chủ sở hữu, quyền nghiệp vụ, loại và trạng thái. Response trả cùng `ai_task_id`. Không tạo task/product mới; trên task chỉ cập nhật `ProgressPercentage`, không chạm trường audit hoặc trường BE sở hữu. |
| Bảng con | Kết quả/phiên bản mẫu lưu `AI_OutputVersions`, nguồn lưu `AI_SourceReferences`, cùng `OwnerType=Task`, `OwnerId=<ERP task>`. API detail/result/source/template đọc được theo ID tác vụ mà không đổi liên kết sản phẩm BE. |
| Thông báo | Chỉ thông báo các task do AI quản lý (`_uc37.managed`); không sửa NotifyOnFinish của task ERP. |
| Giờ DB | Ghi mới nghiệp vụ theo UTC+7; đồng hồ lease riêng vẫn UTC, tương thích lease đang tồn tại. Không chạy chuyển đổi dữ liệu cũ. |
| MinIO BE | Cấu hình `BE_MINIO_*` riêng, chỉ đọc bucket được cấp. Không đổi nơi upload/output AI. Hỗ trợ tham chiếu cũ `minio:knowledge/...` với đúng một bucket BE cấu hình. Tải bằng GET trực tiếp vì proxy BE từ chối HEAD. |

Không có header: giữ luồng AI tự tạo và quản lý tác vụ/sản phẩm như trước.

## Quyền kho

Đọc danh mục phân vùng, thành viên nhóm, quyền phân vùng và category từ schema MSSQL thực tế. Giới hạn tenant/xóa mềm/active; kho cá nhân theo chủ sở hữu, kho nhóm theo thành viên, kho chung theo tenant/đơn vị. Grant user/role/group/unit và deny được áp dụng. Không suy thứ bậc mã bảo mật; grant có trần bảo mật chỉ nhận đúng mã. Quyền thư mục/đơn vị con chưa được mở rộng bằng suy luận: deny thư mục thu hẹp cả phân vùng để tránh bỏ lọt; grant Unit chỉ áp đơn vị khớp chính xác. Những trường hợp cần kế thừa đầy đủ cần xác nhận quy tắc ERP trước khi mở rộng.

## Kiểm tra

- Local: 185 test qua ở lượt kiểm tra rộng; lượt cuối tập trung 83 test qua, gồm test bổ sung số ký hiệu/đọc bảng con ERP.
- Staging Linux: 102 test qua, gồm converter PDF/DOCX, chunking, quyền, UC37 và hợp đồng BE.
- Staging cô lập: `/home/abc/yuu_workspace/TPV-chatbot-btl/artifacts/be-contract-staging`.
- MSSQL thật: lượt task ERP chỉ đổi ProgressPercentage; ghi phiên bản con thành công. Dữ liệu tác vụ thử nghiệm rollback.
- Tài liệu thật 422: SHA-256 khớp ERP. Nạp lại hai lần vào collection staging `tpv_discovery_test_documents`; không tăng số dòng tài liệu ERP, không đổi metadata nghiệp vụ/file gốc. Có 6 chunk, giữ đủ 5 Điều. Truy hồi bằng `sql:422` và mã băm cũ đều tìm được giờ làm việc.
- QA thật trên staging trả đúng giờ làm việc buổi sáng 7:30–11:30, kèm citation `sql:422`; không lưu hội thoại test vào DB.
- Nguyên nhân mất Điều được tái hiện: `Chunker._legal_sections.close()` bỏ mục có title nhưng thân rỗng; nội dung Điều nằm ngay trong title nên mất. Không phải do model không hiểu câu hỏi.
- MinIO thật: Console `console-s3.tpvtech.vn`, S3 API `s3.tpvtech.vn`, bucket `btl-ai`. HEAD bị AccessDenied nhưng GET tải file thành công. Thông tin đăng nhập chỉ ở `.env` cục bộ/cấu hình test riêng, không đưa vào tài liệu hoặc Git.
- Chưa triển khai/restart bản sửa này lên dịch vụ `yuu/chatbot` hay Kiên; chỉ cập nhật staging cô lập và index 422 trong collection staging. `.env` dịch vụ đang chạy chưa đổi.

## Các file chính

- API: `api/documents.py`, `api/agent.py`, `api/identity.py`, `api/tasks.py`.
- Nghiệp vụ: `services/ai_documents.py`, `services/erp_tasks.py`, `services/partition_access.py`, `services/object_storage.py`.
- DB: `db/ai_repository.py`, `db/ai_session.py`, `db/ai_models.py`; chỉ reflection, không DDL/migration.
- RAG: `rag/chunking.py`, `rag/retrieval.py`, `rag/vectorstore.py`, `documents/parser.py`.
- Báo cáo, schema response và test: các thay đổi liên quan trực tiếp trong graph/nodes/tools/schemas; `tests/test_be_contract.py`, `tests/test_uc37.py`.

## Cập nhật cấu hình lưu trữ theo yêu cầu sau đó

Trên Mac, `.env` backend đã chuyển MINIO_ENDPOINT sang `s3.tpvtech.vn`, TLS bật và MINIO_BUCKET=`btl-ai`. Đọc nguồn BE và ghi upload/output AI hiện dùng chung endpoint/bucket. Cấu hình mẫu không chứa khóa bí mật.

Kiểm tra upload/download thực tế khớp SHA-256. Tài khoản không có quyền DeleteObject; file test nhỏ còn tại `uploads/767f6abb97304bef3cd0f3674a177b36588fe3dda0f94342cfb16b381ac51964.txt`, cần quản trị bucket dọn giúp. Không yêu cầu quyền xóa file cho DELETE tài liệu vì API đó chỉ gỡ index.

Người dùng chốt: chỉ đổi nơi lưu từ bây giờ, không chuyển object cũ hoặc sửa hàng loạt tham chiếu DB. Kho tri thức quan trọng đã được người dùng đặt tại `btl-ai/knowledge/vanbandiaphuong/` và `btl-ai/knowledge/vanbantrunguong/`.

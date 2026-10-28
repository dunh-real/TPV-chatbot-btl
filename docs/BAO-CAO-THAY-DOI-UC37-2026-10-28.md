# Báo cáo thay đổi UC37 — ngày 28/10/2026

## 1. Mục đích báo cáo và phạm vi

Báo cáo này giải thích những thay đổi trong lượt triển khai UC37 vừa rồi: thêm/sửa ở đâu, vì sao cần, dữ liệu lưu thế nào và phần nào đã vượt phạm vi hoàn thiện API.

**Phạm vi tôi đã thực hiện rộng hơn yêu cầu API:** ngoài các endpoint theo dõi tác vụ, tôi thêm API tạo tác vụ nền, worker dùng MSSQL và màn hình quản lý tác vụ. Đây là nguyên nhân chính làm lượng code tăng. Giao diện không bắt buộc để hoàn thiện backend/API UC37.

Tài liệu phản ánh code hiện có trong workspace, không phải đề xuất đã được thực hiện thêm. Trong lượt viết báo cáo này chỉ thêm file Markdown; không gỡ hay sửa code triển khai UC37.

## 2. UC37 cần giải quyết những gì?

| Yêu cầu | Backend/API cần làm | Phần giao diện thuộc trách nhiệm frontend |
|---|---|---|
| Xem các tác vụ AI gần đây | Truy vấn tác vụ theo quyền; trả loại, agent, trạng thái, thời gian; phân trang và lọc | Dựng bảng danh sách, bộ lọc |
| Mở tác vụ hoàn thành | Trả kết quả, phiên bản, mẫu và nguồn; cung cấp API tải file | Dựng màn hình chi tiết, nút tải |
| Chạy lại lỗi hoặc xóa đầu ra | Kiểm quyền; thực thi chạy lại; xóa mềm; ghi nhật ký | Nút chạy lại/xóa và xác nhận |
| Bật thông báo hoàn thành/lỗi | Lưu lựa chọn, cung cấp thông báo và trạng thái đã đọc, liên kết kết quả | Hiển thị thông báo trên trang chủ |

**Điểm cần phân biệt:** API đọc danh sách/chi tiết có thể ngắn. API chạy lại cần có cơ chế thực thi và cập nhật trạng thái. Một worker là lựa chọn triển khai cho việc này, nhưng không phải cách duy nhất. Tôi đã chọn làm hàng đợi MSSQL tương đối đầy đủ thay vì chỉ bổ sung các API tối thiểu quanh luồng chạy hiện có.

## 3. So với trước lượt triển khai này

| Nội dung | Trước | Sau thay đổi UC37 |
|---|---|---|
| Ghi tác vụ/sản phẩm | Đã có `tracked_workflow` và ghi sản phẩm vào MSSQL | Mở rộng để lưu tham số chạy lại, nguồn bền, thiết lập thông báo và thông tin lease |
| Theo dõi tác vụ qua API riêng | Chưa có router UC37 trong bản gốc của lượt sửa | Có `/api/tasks` và các endpoint chi tiết bên dưới |
| Chạy workflow | Qua các API/công cụ nghiệp vụ hiện có | Giữ đường cũ; thêm đường gửi vào hàng đợi chạy nền |
| Nguồn đầu vào của workflow | Có đường dẫn/file_id phục vụ lần chạy | Ghi thêm tham chiếu MinIO để lần chạy lại không phụ thuộc file tạm còn tồn tại |
| Xử lý tiến trình bị ngắt | Chưa có cơ chế lease UC37 trong bản gốc | Có heartbeat, phát hiện hết lease và chặn tiến trình cũ ghi kết quả |
| Thông báo UC37 | Chưa có API và màn hình thông báo UC37 | Lấy từ trạng thái tác vụ; lưu lựa chọn và đã đọc trên MSSQL |
| Frontend | Chưa có trang tác vụ | Có menu và màn hình “Tác vụ AI” — phần mở rộng ngoài API |

## 4. Danh sách file mới và vai trò

### 4.1. `backend/app/api/tasks.py` — các endpoint UC37

Router mới có prefix `/api/tasks`, nhóm Swagger `tasks-uc37`.

| Method và đường dẫn | Hành vi | Mục tiêu |
|---|---|---|
| `GET /api/tasks` | Lọc trang, kích thước trang, trạng thái, loại, thời gian | Danh sách tác vụ theo quyền |
| `POST /api/tasks` | Kiểm tra yêu cầu, lưu nguồn, tạo tác vụ `Queued`; trả HTTP 202 | **Phần mở rộng:** tạo tác vụ nền mới |
| `GET /api/tasks/{id}` | Trả input/result đã lọc thông tin nội bộ, phiên bản, nguồn, mẫu | Chi tiết tác vụ |
| `GET /api/tasks/{id}/result` | Tải file MinIO hoặc JSON; nhận `version_id` tùy chọn | Lấy kết quả và phiên bản |
| `GET /api/tasks/{id}/source` | Tải file nguồn đã lưu bền | Xem lại đầu vào của lần chạy |
| `GET /api/tasks/{id}/template` | Tải đúng phiên bản mẫu đã ghi nhận | Kiểm tra mẫu đã sử dụng |
| `POST /api/tasks/{id}/retry` | Tạo lần chạy mới liên kết lần lỗi; trả HTTP 202 | Chạy lại và giữ lịch sử |
| `DELETE /api/tasks/{id}/output` | Xóa mềm đầu ra; trả HTTP 204 | Quản lý sản phẩm theo quyền |
| `PATCH /api/tasks/{id}/notify` | Lưu `enabled` cho chủ tác vụ | Bật/tắt thông báo |
| `GET /api/tasks/notifications` | Trả thông báo hoàn thành/lỗi/đã dừng của chính người dùng | Frontend lấy thông báo |
| `PATCH /api/tasks/notifications/{id}/read` | Ghi nhận đã đọc; trả HTTP 204 | Không hiển thị mãi thông báo chưa đọc |

Endpoint không nhận tên hàm Python hoặc đường dẫn thực thi từ client. Download đọc file qua backend có kiểm tra quyền; không trả URL MinIO nội bộ cho client tự truy cập.

### 4.2. `backend/app/schemas/tasks.py` — kiểm tra đầu vào

- Khai báo loại tác vụ tạo mới: `Review`, `Summarize`, `TaskExtract`, `Draft`, `Aggregate`, `Slide`.
- Khai báo các trạng thái lọc: `Queued`, `Processing`, `Completed`, `Failed`, `Cancelled`.
- `TaskCreate`: loại, tham số và lựa chọn thông báo.
- `ReviewParameters`: `file_id`, `noi_gui`, `rule_set` cho nhóm xử lý file.
- `TaskNotify`: bật/tắt thông báo.
- Chặn trường thừa ở các schema UC37. Tham số báo cáo/slide tái sử dụng schema nghiệp vụ hiện có.

### 4.3. `backend/app/services/tasks.py` — nghiệp vụ quản lý tác vụ

File này chứa phần nghiệp vụ mà API gọi:

- Ánh xạ loại tác vụ với quyền nghiệp vụ và operation được phép.
- Đọc tác vụ theo tenant/người sở hữu hoặc quyền xem toàn bộ.
- Chuẩn hóa dữ liệu danh sách: trạng thái, thời gian, agent, khả năng chạy lại/xóa, liên kết tải.
- Đọc phiên bản từ `AI_OutputVersions`, nguồn từ `AI_SourceReferences`, mẫu từ `AI_ReportTemplate`.
- Kiểm tra quyền đọc hiện hành khi nguồn là tài liệu trong kho tri thức.
- Ẩn các trường đường dẫn nội bộ, bucket/key và metadata điều phối trước khi trả API.
- Xóa mềm sản phẩm/phiên bản/liên kết nguồn và ghi `AI_ExecutionLogs`.
- Đọc thông báo và ghi thời điểm đã đọc.

**Mục tiêu:** gom quy tắc quyền và dữ liệu vào một nơi, không viết lại SQL và điều kiện quyền trong từng endpoint.

### 4.4. `backend/app/services/task_queue.py` — hàng đợi và chạy lại

Đây là phần làm tăng độ phức tạp nhiều nhất ngoài các API đọc dữ liệu.

| Thành phần | Thực hiện | Vì sao thêm |
|---|---|---|
| `source_file` | Upload file nguồn lên MinIO, lấy tham chiếu bền | Chạy lại không dựa hoàn toàn vào file local |
| `submit` | Kiểm quyền/tham số, kiểm số văn bản, tạo `Queued` | Hỗ trợ API tạo tác vụ nền mới |
| `retry` | Kiểm tác vụ lỗi, khôi phục nguồn, tạo lần chạy mới | Đáp ứng chạy lại trong UC37 |
| `claim` | Khóa SQL và nhận một tác vụ chờ | Tránh hai worker nhận cùng một tác vụ |
| `execute` | Tra lại quyền, tải nguồn vào thư mục tạm, gọi workflow được cho phép | Thực thi ngoài request tạo tác vụ |
| `lease` | Gia hạn quyền sở hữu lần chạy bằng heartbeat | Phân biệt tác vụ còn chạy với tiến trình bị mất |
| `recover` | Tác vụ hết lease chuyển sang `Failed`, ghi nhật ký | Không để trạng thái xử lý treo mãi |
| `fence` | Kiểm tra token và trạng thái trước khi ghi | Chặn lần chạy cũ ghi đè sau khi mất quyền xử lý |
| `serve` / `fail` | Vòng lặp worker, timeout và xử lý lỗi | Vận hành hàng đợi trong backend |

Worker chạy cùng tiến trình FastAPI, không dùng Redis/Celery và không có service riêng. Một vòng lặp worker xử lý lần lượt; nhiều tiến trình backend có thể lấy các tác vụ khác nhau nhờ khóa MSSQL.

**Phần có thể thu gọn nếu chỉ giữ API:** bỏ API tạo tác vụ nền mới và thiết kế lại cơ chế chạy lại gọn hơn. Không thể chỉ xóa file worker nhưng giữ nguyên API retry hiện tại: retry đang tạo `Queued`, nếu không có worker thì tác vụ sẽ không chạy.

### 4.5. File kiểm thử và tài liệu mới

| File | Mục đích |
|---|---|
| `backend/tests/test_uc37.py` | Kiểm tra API, quyền, cách ly tài khoản/tenant, nguồn/phiên bản, chạy lại, thông báo, xóa mềm và lease |
| `backend/tests/test_uc37_ui.py` | Kiểm thử trình duyệt cho trang tác vụ; chỉ chạy khi bật `UC37_UI_TEST=1` |
| `backend/scripts/check_uc37.py` | Tạo bản ghi thử riêng trên MSSQL, kiểm tra nhận đồng thời/lease/chạy lại/thông báo/xóa rồi dọn |
| `docs/UC37.md` | Hướng dẫn vận hành và thử API |
| File báo cáo này | Giải thích phạm vi và từng thay đổi để kiểm tra |

## 5. Các file backend hiện có đã sửa

### 5.1. `backend/app/services/ai_outputs.py`

**Trước:** đã có decorator theo dõi workflow và hàm lưu sản phẩm vào SQL/MinIO. Không phải toàn bộ chức năng này được viết mới cho UC37.

**Thay đổi:**

1. Nếu workflow chạy từ hàng đợi thì dùng lại tác vụ đã nhận; không tạo thêm tác vụ trùng.
2. Với đường chạy cũ, ghi thêm metadata điều phối và thiết lập thông báo vào tác vụ.
3. Ghi file_id có thể phục hồi và nguồn MinIO trước khi gọi xử lý AI.
4. Không lưu lại các tham số tạm như lịch sử chat, ảnh/chart nhị phân để phát lại tùy tiện; đánh dấu trường hợp không hỗ trợ chạy lại đầy đủ.
5. Kiểm lease trước các lần ghi quan trọng; cập nhật trạng thái kết thúc và nhật ký lỗi.
6. Lưu phiên bản/nguồn cho kết quả tóm tắt và trích nhiệm vụ bằng quan hệ với `AI_Tasks`.
7. Giữ đường gọi workflow cũ; bổ sung `ai_task_id` trong kết quả của wrapper.

**Mục tiêu:** API UC37 lấy được dữ liệu đúng từ các lần chạy thực tế, không chỉ có endpoint mà thiếu thông tin đầu vào/kết quả.

### 5.2. `backend/app/agents/graph.py`

Thêm `run_analysis_workflow`: đọc tài liệu và chạy phân tích tóm tắt/trích nhiệm vụ bằng các node có sẵn. Nếu không có kết quả phân tích thì trả lỗi thay vì ghi hoàn thành với nội dung rỗng.

**Mục tiêu:** nhóm tác vụ tóm tắt/trích nhiệm vụ trong API tạo nền không cần chạy toàn bộ quy trình soát lỗi. Không viết lại các workflow soạn/tổng hợp/tạo slide.

### 5.3. `backend/app/agents/quyen.py`

Thêm hằng số cho ba quyền ERP đã có:

- `Ai.AiTask`: vào chức năng quản lý tác vụ.
- `Ai.AiTask.ViewAll`: xem tác vụ trong cùng tenant.
- `Ai.AiTask.Delete`: xóa đầu ra theo phạm vi được phép.

Không thêm quyền mới vào DB. Tạo/chạy lại/thực thi còn kiểm quyền nghiệp vụ tương ứng; quyền xem toàn bộ không tự cho phép xóa đầu ra người khác.

### 5.4. `backend/app/db/ai_session.py`

Thêm hai biến ngữ cảnh:

- `execution_task`: mang tác vụ hàng đợi đang chạy để decorator sử dụng lại.
- `notify_on_finish`: mang lựa chọn thông báo từ request tới nơi ghi tác vụ.

Không đổi connection string, engine hay schema trong lượt UC37.

### 5.5. `backend/app/db/ai_repository.py`

Sửa điều kiện kiểm tra số văn bản: tính cả tác vụ `Queued`, bên cạnh `Processing` và `Completed`.

**Mục tiêu:** hai yêu cầu đang chờ không cùng giữ một số văn bản. Thay đổi này gắn với việc thêm hàng đợi; nếu bỏ đường tạo `Queued` thì cần đánh giá lại nhu cầu giữ nó.

### 5.6. `backend/app/api/identity.py`

Đọc thêm header `X-Notify-On-Finish: true` vào context và reset sau request.

**Mục tiêu:** các API nghiệp vụ cũ vẫn có thể ghi lựa chọn thông báo mà không phải thêm tham số vào tất cả hàm workflow. API tạo tác vụ nền có trường `notify_on_finish` riêng trong body.

### 5.7. `backend/app/main.py`

- Đăng ký router UC37.
- Khởi động worker khi có `AI_DATABASE_URL` và bật `TASK_WORKER_ENABLED`.
- Dừng worker trong quá trình shutdown backend.

**Mục tiêu:** endpoint hoạt động và tác vụ chờ có tiến trình xử lý. Nếu thu gọn API, phần đăng ký router vẫn cần; phần vòng đời worker phụ thuộc thiết kế chạy lại được chọn.

### 5.8. `backend/app/core/config.py` và `backend/.env.example`

Thêm bốn cấu hình:

| Biến | Mặc định | Vai trò |
|---|---:|---|
| `TASK_WORKER_ENABLED` | `true` | Bật worker khi backend có DB AI |
| `TASK_WORKER_POLL_SECONDS` | `2` | Khoảng nghỉ khi không có tác vụ |
| `TASK_LEASE_SECONDS` | `120` | Thời hạn sở hữu lần chạy nếu không gia hạn |
| `TASK_EXECUTION_TIMEOUT_SECONDS` | `1800` | Giới hạn một lần thực thi tác vụ nền |

Không sửa file `.env` chứa thông tin kết nối trong lượt triển khai UC37.

### 5.9. Hai file kiểm thử cũ

- `backend/tests/conftest.py`: cập nhật fixture nghiệp vụ để giả lập kiểm tra lease trong các kiểm thử workflow cũ.
- `backend/tests/test_storage_split.py`: cập nhật kỳ vọng lời gọi kiểm tra số văn bản với tham số loại trừ tác vụ hiện tại.

Đây là cập nhật tương thích kiểm thử, không phải thêm nghiệp vụ cho người dùng.

## 6. Frontend đã thêm/sửa — phần vượt phạm vi API

| File | Thay đổi | Mục tiêu |
|---|---|---|
| `frontend/js/tasks.js` — mới | Gọi API, danh sách/chi tiết, lọc, tải, chạy lại, xóa, thông báo, form tạo tác vụ nền | Trang quản lý UC37 |
| `frontend/index.html` | Menu “Tác vụ AI”, cấu trúc trang và nạp script; cập nhật phiên bản asset | Có chỗ hiển thị UI mới và tránh cache cũ |
| `frontend/js/api.js` | Hàm gọi API tác vụ, tải file có header danh tính, header lựa chọn thông báo, sự kiện đổi danh tính | Tích hợp trang mới với cơ chế gọi API hiện tại |
| `frontend/js/app.js` | Đăng ký view và xử lý liên kết `#tasks/{id}` | Mở đúng trang/chi tiết từ thông báo |
| `frontend/styles.css` | Bố cục, bảng, nút, form cho UC37 | Hiển thị trang mới |

Các phần trên không bắt buộc để bàn giao backend/API cho frontend khác sử dụng. Có thể gỡ khi thu gọn phạm vi, nhưng phải gỡ đồng bộ script, menu, route giao diện và các hàm bổ trợ chỉ dùng cho UC37.

## 7. Dữ liệu lưu cụ thể ở đâu?

### 7.1. MSSQL

| Bảng/trường | Dữ liệu UC37 sử dụng |
|---|---|
| `AI_Tasks` | Chủ tác vụ, tenant, loại, agent, trạng thái, tiến độ, thời gian, tác vụ gốc khi retry, lựa chọn thông báo |
| `AI_Tasks.InputJson` | Tham số chạy, tham chiếu nguồn MinIO, metadata `_uc37` |
| `AI_Tasks.ResultJson` | Kết quả xử lý được lưu để xem lại |
| `AI_Tasks.OutputFileUrl` | Tham chiếu file kết quả trên MinIO |
| `AI_Tasks.TextSearch` | Tên file đầu ra phục vụ đường tải cũ; giữ làm dấu chặn fallback khi xóa |
| `AI_OutputVersions` | Phiên bản nội dung/file của sản phẩm |
| `AI_SourceReferences` | Tài liệu kho tri thức hoặc nguồn bên ngoài được sử dụng |
| `AI_DocumentReviews`, `AI_DocumentDrafts`, `AI_ReportSummaries`, `AI_Slides` | Sản phẩm tương ứng theo workflow hiện có |
| `AI_ReportTemplate`, `AI_TemplateVersions` | Mẫu và phiên bản mẫu đã sử dụng |
| `AI_ExecutionLogs` | Nhật ký thực thi, yêu cầu chạy lại, thay đổi thông báo, xóa đầu ra và gián đoạn |

Metadata `_uc37` sử dụng JSON có sẵn, gồm các thông tin như `managed`, `origin`, `lease_token`, `lease_until`, `output_deleted`, `notification_read_at`, `unreplayable`. Đây là mở rộng dữ liệu JSON; không phải thêm cột SQL.

### 7.2. MinIO và thư mục local

- MinIO lưu file nguồn được giữ bền và file kết quả.
- Worker tải file nguồn vào thư mục tạm dưới `UPLOAD_DIR`, chạy xong thì dọn thư mục tạm.
- File upload/local từ các luồng cũ không tự bị dọn hàng loạt bởi UC37.
- Xóa đầu ra là xóa mềm metadata và chặn đường tải tương ứng; không xóa vật lý object MinIO.
- SQLite pháp lý không thay đổi. Qdrant không được thay thế bằng MSSQL và không có migration dữ liệu Qdrant trong lượt này.

## 8. Ví dụ luồng xử lý hiện tại

### Ví dụ A — chỉ xem tác vụ đã chạy

1. API nghiệp vụ cũ chạy workflow.
2. Decorator cập nhật `AI_Tasks`, lưu kết quả và các liên kết sản phẩm.
3. `GET /api/tasks` đọc danh sách theo quyền.
4. `GET /api/tasks/{id}` đọc chi tiết; khi cần tải, backend đọc file MinIO.

Việc xem lại lịch sử không gọi LLM để tạo lại kết quả.

### Ví dụ B — chạy lại tác vụ lỗi

1. Client gọi `POST /api/tasks/123/retry`.
2. Backend kiểm quyền xem và quyền nghiệp vụ, kiểm khả năng khôi phục tham số/nguồn.
3. Tạo tác vụ mới, ví dụ `124`, có `RetryOfTaskId=123`, trạng thái `Queued`.
4. Nếu lần chạy lại còn đang chờ/xử lý, gửi lặp trả lại ID lần chạy đang hoạt động.
5. Worker nhận `124`, kiểm quyền hiện hành, tải nguồn và gọi workflow cũ.
6. Ghi kết quả lần mới; giữ nguyên lịch sử lần `123`.

Đây là lần chạy mới có liên kết lịch sử, không phải ghi đè tác vụ `123` hay tự tăng phiên bản sản phẩm cũ.

### Ví dụ C — thông báo

1. Người dùng bật `notify_on_finish` cho tác vụ.
2. Khi tác vụ kết thúc, API thông báo lấy trạng thái từ `AI_Tasks`.
3. Frontend hiện tại thăm dò mỗi 5 giây để hiển thị số chưa đọc/thông báo và liên kết kết quả.
4. Người dùng mở thông báo thì ghi `notification_read_at`.

Không có bảng thông báo mới, email, WebSocket hoặc push hệ điều hành. Nếu chỉ bàn giao API, frontend tích hợp sau sẽ đảm nhiệm bước thăm dò và hiển thị.

## 9. Phần nào nên giữ nếu mục tiêu chỉ là API?

| Nhóm | Nhận định |
|---|---|
| Router/schema/service danh sách, chi tiết, tải, xóa, thông báo | Giữ: trực tiếp đáp ứng các API UC37 |
| Phân quyền và nhật ký | Giữ: yêu cầu của UC37 |
| Ghi trạng thái/kết quả/nguồn/mẫu từ workflow | Giữ phần cần để API có dữ liệu thực tế |
| API retry và cơ chế thực thi | Giữ chức năng; có thể thiết kế lại gọn hơn theo phạm vi đã chốt |
| API tạo tác vụ nền mới | Có thể bỏ nếu tiếp tục tạo tác vụ qua API nghiệp vụ hiện có |
| Worker đầy đủ, lease, heartbeat, recovery | Đánh giá theo yêu cầu chạy nền/độ bền; là lựa chọn kiến trúc tôi đã thêm, không mặc nhiên bắt buộc cho mọi API |
| Toàn bộ màn hình UC37 và kiểm thử UI | Có thể gỡ nếu chỉ bàn giao backend/API |
| Kiểm thử backend và hướng dẫn API | Nên giữ tương ứng phần code còn lại |

**Chưa thực hiện việc thu gọn trong báo cáo này.** Đặc biệt, tắt worker chỉ là dừng xử lý hàng đợi, không biến API retry hiện tại thành chạy trực tiếp. Muốn gỡ worker phải sửa lại đường thực thi retry trước.

## 10. Kết quả kiểm thử và giới hạn xác nhận

- UC37/API/giao diện Chrome: **13 bài đạt** trong lượt chạy riêng.
- Bộ kiểm thử rộng: **1.008 bài đạt, 2 bỏ qua, 41 loại khỏi lượt chạy**. Nhóm loại gồm live/GPU và một bài PDF có lỗi cũ đã biết (`test_pdf_giu_bang_va_danh_dau_trang`).
- MSSQL thực tế: đã kiểm tra nhận tác vụ đồng thời, hết lease, chặn tiến trình cũ ghi, thông báo/đã đọc, retry chống trùng và xóa mềm/nhật ký. Bản ghi thử đã được dọn.
- Kiểm tra cú pháp JavaScript và `git diff --check` đạt.
- Kiểm thử đơn vị/điều phối có giả lập LLM/MinIO; kết quả đó không xác nhận chất lượng sinh nội dung thực tế.
- Chưa kiểm thử sinh nội dung với LLM thật trên server trong lượt UC37 này.
- Chưa triển khai code UC37 lên yuu/kien; chưa commit thay đổi UC37.

File kết quả kiểm thử và ảnh UI nằm trong `artifacts/uc37/`, không đưa vào commit theo yêu cầu trước của bạn.

## 11. Những việc không làm trong lượt UC37

- Không tạo bảng, thêm cột hay chạy migration schema MSSQL.
- Không sửa/xóa dữ liệu SQLite nghiệp vụ cũ hoặc dữ liệu pháp lý.
- Không thay kết nối DB và không di chuyển dữ liệu cũ.
- Không viết lại toàn bộ thuật toán soát/soạn/tổng hợp/tạo slide; tái sử dụng workflow hiện có.
- Không có thao tác xóa toàn bộ file MinIO hoặc local.
- Không xóa file mã nguồn hiện có. Các dòng bị thay chủ yếu nằm trong wrapper ghi tác vụ, cấu hình/vòng đời backend và tích hợp frontend.

Phần vượt phạm vi rõ nhất là **giao diện quản lý và API tạo mới tác vụ nền**. Phần cần cân nhắc thu gọn tiếp theo là **cơ chế hàng đợi**, đồng thời vẫn phải bảo đảm API chạy lại thực sự thực thi tác vụ.

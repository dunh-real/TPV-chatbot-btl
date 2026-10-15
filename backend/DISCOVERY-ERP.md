# ERP Text-to-SQL discovery trong TPV

Toàn bộ mã chạy nằm trong `TPV-chatbot-btl`. Không import, không cài editable và
không cần đặt thư mục `text_to_sql` cạnh dự án. Chỉ chuyển kiến trúc discovery từ
bộ thử nghiệm: khám phá metadata/danh mục → sinh SQL → validate → sửa lỗi có giới hạn.

## Luồng xử lý

```text
POST /api/agent/chat → Planner
  ├─ hỏi số liệu → tool-loop → query_erp(question)
  ├─ báo cáo Word → report/drafting → query_erp(question)
  └─ PowerPoint → presentation dùng gather_data của report → query_erp(question)
       ↓
quyền ERP của tài khoản + schema snapshot + policy JSON
       ↓
full schema được phép → model Qwen + discovery tools
       ↓
kiểm tra AST/bảng/cột/hàm → ép tenant và giới hạn dòng trên từng bảng
       ↓
SQL Server binding → SELECT có timeout → QueryResult
       ↓
chat diễn đạt / code dựng bảng Word / code dựng bảng slide
```

Planner quyết định nghiệp vụ; model bên trong discovery sinh SQL. API và Planner
không nhận SQL tùy ý từ client. Quyền `Ai.*` để dùng nghiệp vụ vẫn giữ nguyên;
quyền đọc bảng/cột là lớp độc lập trong chính sách SQL.

## Các file chính

| File | Vai trò |
|---|---|
| `app/services/text2sql.py` | Pipeline discovery nội bộ: `describe_tables`, `get_relations`, `lookup_values`, `validate_sql`; thinking tắt; giới hạn tool, thời gian và lượt sửa SQL. |
| `app/services/erp_discovery.py` | Kết nối ERP và Qwen; executor kiểm tra lại mọi SQL trước khi thực thi; nối engine với `QueryResult`; tác vụ cập nhật schema. |
| `app/services/erp_sql_catalog.py` | Đọc metadata thật của DB và ghi JSON nguyên tử; chỉ đưa schema/FK/danh mục được cấp quyền cho model. |
| `app/services/erp_sql_policy.py` | Đọc chính sách bảng/cột và giới hạn theo tài khoản; kiểm tra AST, chặn SQL ghi/cross-database/hàm lạ; bọc từng bảng bằng projection được cấp và bộ lọc tenant/dòng. |
| `config/erp_sql_policy.json` | Ánh xạ quyền ERP sang bảng/cột, cột được lookup, mô tả nghiệp vụ và phạm vi bổ sung theo user. |
| `app/schemas/erp.py` | `QueryResult` và kiểu cột; giữ Decimal và số nguyên lớn khi chuyển JSON. |
| `app/tools/data.py` | Một tool ERP: `query_erp(question)`; thay tool thống kê cố định. |
| `app/documents/query_result.py` | Dựng bảng Word và phân trang bảng slide từ cột/dòng động. Không ánh xạ ngược về `total_personnel` hay `total_equipment`. |
| `app/agents/nodes/report.py`, `drafting.py`, `presentation.py` | Workflow nhận QueryResult; chặn xuất tài liệu đầy đủ nếu kết quả bị cắt. Nhánh Word ERP dựng bảng trực tiếp nên bỏ bước RAG tra quy định và prompt viết mục cũ. |
| `app/tools/registry.py`, `app/agents/prompts.py`, `graph.py`, `quyen.py` | Đăng ký công cụ, hướng dẫn agent gọi discovery; phân quyền SQL chuyển về policy; kết quả workflow có dữ liệu truy vấn. |
| `app/schemas/reports.py`, `app/schemas/presentations.py`, `app/api/reports.py` | API Word/PowerPoint có `data.queries`, chấp nhận các mục bảng động và mô tả nguồn ERP mới. |
| `app/agents/nodes/toolloop.py` | Chat nhận JSON đầy đủ; nếu rút gọn vì ngữ cảnh thì cắt theo dòng, đánh dấu preview, đối chiếu số chỉ trong dữ liệu model nhìn thấy. |
| `app/rag/embedding.py` | Chỉ import Torch/Transformers khi cần embedding; import agent để chạy ERP không nạp thư viện model RAG. |
| `app/services/report_register.py` | Đọc sổ báo cáo của app cho nhánh tổng hợp tài liệu, tách khỏi truy vấn số liệu ERP. |
| `app/db/erp_repository.py` | Chỉ còn danh mục phòng ban cho định tuyến tài liệu. Các repository thống kê nhân sự/thiết bị/tài nguyên đã gỡ. |
| `app/main.py`, `app/core/config.py`, `.env.example`, `pyproject.toml`, `uv.lock` | Khởi động/dừng cập nhật schema, cấu hình và dependency của dự án độc lập. |

`erp_discovery_adapter.py`, `QueryTask`, lựa chọn provider `repository` và các
truy vấn ORM lấy số liệu cố định được bỏ. Truy vấn định danh/phân quyền, metadata
và sổ văn bản vẫn là truy vấn do code viết: đây là hạ tầng của engine.

## Schema tự cập nhật

`ERP_SCHEMA_FILE` mặc định `data/erp_schema.json`, chứa toàn bộ bảng/cột, kiểu,
NULL, khóa chính và FK mà tài khoản kết nối DB nhìn thấy. Không lưu dữ liệu hàng,
mật khẩu hay connection string vào file này. File không được gửi nguyên vẹn cho model.

Khi khởi động, tác vụ nền đọc metadata; mỗi `ERP_SCHEMA_REFRESH_SECONDS` (mặc định
300 giây) kiểm tra lại. Request cũng kiểm tra TTL để chạy được ngoài lifespan.
Hash thay đổi thì ghi file mới rồi thay thế nguyên tử. Lỗi kết nối khi cần refresh
làm truy vấn dừng; không âm thầm dùng schema hết hạn. Đây là polling, không phải
thông báo DDL tức thì. Không suy luận được các quan hệ logic chưa khai FK.

Bảng/cột mới có trong file **chưa được phép truy vấn**: phải thêm vào policy sau
khi xác định quyền và mức nhạy cảm. Schema và policy có version riêng trong kết quả.

## Chính sách quyền

Policy phiên bản 2 cho phép đọc mã/họ tên nhân viên trong phạm vi được cấp, vẫn
chặn giấy tờ, liên hệ cá nhân, ngân hàng và lương. Schema đầy đủ chỉ được lưu nội bộ.
Quyền hiệu lực là giao của quyền module ERP, nhóm, whitelist cột và mục `users`.
Admin vẫn chịu whitelist cột, tenant và giới hạn riêng theo tài khoản.

| Nhóm | Mapping ERP hiện tại | Phạm vi |
|---|---|---|
| `admin` | Vai trò Admin tĩnh được ERP xác nhận | Các bảng được mở trong tenant |
| `director` | Tenant 64, role Id 159 (Giám đốc) | Các bảng được mở trong tenant |
| `hr` | Chưa gán role; cần cấu hình `tenant_roles` khi có vai trò nhân sự thực tế | Nhân sự trong tenant |
| `department_manager` | Tenant 64, role Id 161, 168, 216 | Nhân sự/tài sản thuộc phòng ban hiện tại; danh mục chức vụ/loại tài sản trong tenant |
| `employee` | Mặc định khi không khớp nhóm nào | Hồ sơ bản thân, phòng ban bản thân và danh mục chức vụ |

Mapping dùng role Id kèm tenant, không đoán quyền từ tên vai trò và không lấy nhóm
từ request/model. Nhiều nhóm thì chọn một nhóm có `priority` lớn nhất; đồng mức
ưu tiên bị từ chối. Nhóm không tự thay thế quyền module: `ViewMine` chỉ cho nhóm
nhân viên đọc bản thân, không cho trưởng phòng/giám đốc đọc rộng hơn.

Phạm vi cá nhân lấy `Hrm_EmployeeProfile.EmployeeId` từ liên kết
`Dms_Employee.UserId = tài khoản`, cùng tenant, `IsDeleted=0`. Cần đúng một liên
kết; thiếu/trùng liên kết bị chặn. Phạm vi phòng ban dùng `WorkDepartmentId` của
nhân viên liên kết, không tự suy ra phòng ban con hoặc đơn vị từng quản lý trước
đây. Đây là quy ước của bản thử; nếu ERP có bảng phân công quản lý nhiều phòng,
cần cấu hình/triển khai resolver từ nguồn đó trước khi mở phạm vi rộng hơn.

Trong `groups.<tên>.tables`, `columns` thu hẹp cột, `permissions_any` yêu cầu
quyền module bổ sung, `scope` nhận `tenant`, `self`, `department`; `scope_column`
là cột cần ép. `row_filters` của nhóm và tài khoản được AND với nhau. Cột mới
chỉ được cấp khi có trong whitelist bảng chung. `column_permissions` tiếp tục
hỗ trợ quyền bổ sung theo từng cột. Model chỉ thấy schema đã lọc; executor ép lại
phạm vi ở từng bảng vật lý, kể cả JOIN/CTE/UNION. Kết quả có cảnh báo phạm vi để
không diễn giải số liệu cá nhân/phòng ban thành số liệu toàn công ty.

### Ngoại lệ đọc mở rộng của tài khoản Lâm Hải

`account_read_grants["64:153"]` cấp riêng cho tài khoản `LH` (UserId 153,
tenant 64), không dựa vào tên hiển thị. Tài khoản phải vẫn có vai trò Admin tĩnh
được ERP xác minh; mất vai trò thì quyền mở rộng bị từ chối.

Whitelist hiện có 91 bảng nghiệp vụ, 1.682 cột từ snapshot DB đã kiểm tra:
nhân sự, tài chính, chấm công, tài sản, danh mục, tuyển dụng và phê duyệt.
Bao gồm các trường cá nhân/tài chính từng bị chặn trong policy thống kê chung.
Không cấp bảng hệ thống ABP/HangFire, bí mật đăng nhập hoặc bảng thiếu TenantId
(chưa có bộ lọc tenant qua quan hệ an toàn). Mọi SELECT vẫn bị ép tenant,
giới hạn dòng, timeout và kiểm tra AST. Các Admin khác giữ quyền cũ.

Đây là danh sách cột tường minh, không phải wildcard: schema thay đổi không tự
mở quyền. Muốn cấp thêm phải cập nhật danh sách này. Mục `users` vẫn giao thêm
để giới hạn tài khoản. Xóa khóa `64:153` để trở về chính sách nhóm thông thường.

Ví dụ giới hạn thêm tài khoản ERP Id 123 của tenant 94:

```json
"users": {
  "94:123": {
    "dbo.Hrm_EmployeeProfile": {
      "columns": ["Id", "WorkDepartmentId", "HireDate", "ResignationDate"],
      "row_filters": {"WorkDepartmentId": [10, 20]}
    }
  }
}
```

Nếu user có mục riêng, chỉ các bảng trong mục đó được giữ, rồi tiếp tục giao với
quyền module và cột của policy chung. `row_filters` là điều kiện AND và danh sách
rỗng nghĩa là không dòng nào. Tenant luôn lấy từ danh tính ERP đã resolve, không
lấy từ nội dung model. Quyền thiếu/tenant không xác định hoặc không khớp → từ chối.
Thay policy có hiệu lực ở request tiếp theo; không cần sửa code.

Middleware hiện hỗ trợ chọn danh tính qua header trong môi trường demo; đây chưa
phải xác thực đăng nhập. Cần hoàn thiện xác thực token trước khi dùng header trên
mạng không tin cậy. Cấu hình `TRUST_IDENTITY_HEADERS=false` chỉ tắt đường demo,
không tự triển khai JWT. Nên dùng login SQL chỉ có SELECT làm lớp bảo vệ ở DB.

## Kết quả

```json
{
  "query_id": "...",
  "question": "Có bao nhiêu hồ sơ nhân sự hiện tại?",
  "columns": [{"name": "total", "data_type": "int", "label": null, "unit": null}],
  "rows": [[125]],
  "row_count": 1,
  "truncated": false,
  "schema_version": "...",
  "policy_version": "...",
  "source_tables": ["dbo.Hrm_EmployeeProfile"],
  "warnings": []
}
```

`row_count` là số dòng trả về, không phải giá trị chỉ tiêu trong dòng. Alias do
SQL tạo; đơn vị/nhãn chưa có căn cứ thì để null. Decimal và integer vượt độ chính
xác JavaScript ra chuỗi. `truncated=true` chỉ là phần xem trước; Word/PowerPoint
không xuất báo cáo đầy đủ từ phần đó. Slide quá 60 trang bảng yêu cầu thu hẹp hoặc
SQL tổng hợp; các trang được chấp nhận giữ đủ dòng/cột. Nhánh Word ERP dùng mẫu
cho thể thức, còn nội dung là bảng động; chưa tự sinh biểu đồ hoặc nhận xét
nghiệp vụ từ các tên cột tùy ý.

Validate bảo đảm quyền và khả năng chạy; không chứng minh chỉ tiêu đúng nghiệp vụ.
Định nghĩa chỉ tiêu nằm trong `meaning`/câu hỏi và cần kiểm tra bằng bộ gold riêng.
Ngày tuyển/nghỉ có thể suy ra một số chỉ tiêu lịch sử; Quantity/Status/phòng ban
hiện tại không đủ để tái dựng mọi trạng thái quá khứ.

## Chạy trên server

Từ thư mục `backend`:

```bash
uv sync --group dev
uv run pytest tests/test_erp_discovery.py tests/test_query_workflows.py -q
uv run uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Điền `ERP_DATABASE_URL` bằng connection string SQL Server của môi trường chạy.
`ERP_TEXT2SQL_BASE_URL` là endpoint vLLM truy cập được từ backend. Discovery cần
endpoint hỗ trợ `tool_choice=auto`; lỗi HTTP 400 về auto-tool-choice/parser là
cấu hình vLLM, không phải lỗi SQL Server. Không cần chạy `run_qwen.py` để dùng TPV.

## Semantic nghiệp vụ

`ERP_SEMANTIC_FILE=config/erp_semantic.yaml` chứa mô tả bảng/cột và lưu ý nghiệp vụ,
tách khỏi policy quyền. Đọc lại mỗi truy vấn; sửa YAML không cần restart. Để trống
biến này nếu chủ động tắt semantic. Các entry dùng tên đầy đủ `dbo.TenBang`.

- `meaning`: mô tả bảng, không đặt tên/trường nhạy cảm trong mô tả chung.
- `columns`: mô tả từng cột; chỉ nạp mô tả của cột được cấp quyền.
- `caveats`: các mục `text`, `requires` (bảng hoặc `schema.table.column`). Nếu lưu ý
  nhắc đến nguồn/cột khác, phải khai đủ `requires`; chỉ nạp khi đủ quyền mọi nguồn.
- `references`: bảng liên quan; chỉ nạp bảng được cấp, không tự tạo FK hoặc mở quyền.

Tên cột và tham chiếu của các bảng được cấp được kiểm tra với snapshot DB. Tham
chiếu bị xóa/đổi tên thì truy vấn dừng với lỗi semantic, cần người sửa DB cập nhật
YAML; không tự viết lại ý nghĩa. Bảng ngoài quyền được bỏ qua. Bảng/cột mới không
tự có mô tả hoặc quyền mới. Thay kiểu dữ liệu/ý nghĩa/enum vẫn cần đội ERP review,
không thể phát hiện đầy đủ chỉ bằng kiểm tra tên.

Full schema được cấp vẫn giữ nguyên; semantic ghi đè mô tả nghiệp vụ cũ ở bảng
đã khai báo, không thay bộ lọc tenant/cá nhân/phòng ban. Kết quả có
`semantic_version` (version + hash nội dung) để biết bản định nghĩa đã dùng.

Danh mục ban đầu có 15 bảng trọng tâm. Đã kiểm tra cột/FK trên DB ngày 15/10/2026;
DB chưa cung cấp MS_Description ở các bảng đã khảo sát, bảng cấp phát còn rỗng.
Mô tả tổng quan là bản biên soạn ban đầu, chưa thay thế xác nhận của đội ERP.
Enum, điều kiện cấp phát còn hiệu lực, quy tắc hoàn trả/lương/tiền tệ và giá trị
NULL chưa rõ được ghi là chưa xác nhận. Không lưu số liệu hiện tại vào định nghĩa.

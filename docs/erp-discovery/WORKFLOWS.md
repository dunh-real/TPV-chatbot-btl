# Ảnh hưởng tới workflow

## Hợp đồng dữ liệu chung

`query_erp(question: str) → QueryResult`; lỗi quyền/DB/model làm truy vấn dừng,
không tự chuyển về repository ERP cũ.

```json
{
  "query_id": "...",
  "question": "Số lượng thiết bị theo loại",
  "columns": [{"name": "category", "data_type": "nvarchar", "label": null, "unit": null}],
  "rows": [["Máy chủ"]],
  "row_count": 1,
  "truncated": false,
  "schema_version": "...",
  "policy_version": "...",
  "source_tables": ["dbo.Asm_Assets"],
  "warnings": []
}
```

`row_count` là số dòng trả về, không phải tổng nhân sự/tài sản. Decimal và integer
vượt độ chính xác JavaScript được xuất thành chuỗi. Không tự đổi chúng sang float.
`warnings` có thể nêu phạm vi cá nhân/phòng ban; cần giữ khi diễn đạt hoặc xuất tài liệu.

## Các nhánh bị ảnh hưởng

| Nhánh | Hành vi mới | Điểm cần giữ khi phát triển |
|---|---|---|
| Chat agent | Tool-loop gọi `query_erp`; đọc bảng động để trả lời | Không suy ra tổng công ty từ dữ liệu giới hạn phòng ban/bản thân |
| Word theo mẫu | `drafting.fetch_data_node` lấy `data.queries`; code dựng bảng động | Mẫu phục vụ thể thức; không yêu cầu alias số liệu cố định |
| Báo cáo tổng hợp từ ERP | `report.gather_data_node` lấy `data.queries` | Không thêm lại truy vấn ORM thống kê; giữ câu hỏi và ngữ cảnh tiếp nối |
| PowerPoint từ ERP | Dùng bước lấy dữ liệu của report và bảng động | Giữ đủ giá trị khi dựng slide hoặc fallback local |
| Tổng hợp báo cáo tài liệu | Giữ cấu trúc tổng hợp tài liệu riêng | Phân nhánh theo nguồn dữ liệu; không ép sang SQL |

Nhánh Word ERP bỏ bước RAG tra quy định và prompt viết từng mục kiểu cũ; nội dung
số liệu dựng từ bảng thực tế. Không mặc định có nhận xét hay biểu đồ như bộ chỉ tiêu cũ.

## API và giao diện

- Nhánh ERP trả `data.queries`; consumer cần đọc `columns`/`rows`, không truy cập
  trực tiếp `data.personnel` hoặc `data.equipment` của kiến trúc cũ.
- API Word/PowerPoint vẫn trả đường dẫn tài liệu và thông tin workflow theo schema;
  phần dữ liệu ERP đã chuyển sang bảng động.
- Tool-loop có thể cắt preview theo dòng để vừa ngữ cảnh; phần bị cắt không phải dữ liệu đầy đủ.
- Kết quả engine `truncated=true` bị chặn xuất Word/PowerPoint đầy đủ;
  yêu cầu thu hẹp phạm vi hoặc SQL tổng hợp.

## Các nhánh không đổi nguồn dữ liệu

RAG hỏi đáp tài liệu, OCR, mindmap và xử lý tài liệu vẫn dùng nguồn riêng.
Planner tiếp tục chọn workflow; quyền nghiệp vụ `Ai.*` tách khỏi quyền đọc ERP.
Embedding chuyển sang import thư viện nặng khi cần, để import graph ERP không tự
nạp Torch/Transformers; cách tạo embedding vẫn giữ nguyên.

## Khi thay đổi tiếp

1. Thêm dữ liệu ERP: sửa policy/định nghĩa nghiệp vụ và kiểm thử quyền.
2. Thêm cách trình bày: dùng `QueryResult`, giữ nguồn, cảnh báo và cờ cắt dữ liệu.
3. Thay hợp đồng `QueryResult`: cập nhật đồng thời tool-loop, Word, PowerPoint,
   API schema và `test_query_workflows.py`.

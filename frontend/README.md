# Giao diện demo — HTML/CSS/JS thuần

Không build, không npm, không CDN: ba file tĩnh nạp thẳng vào trình duyệt. Chọn
như vậy vì máy demo có thể không có mạng, và một bước `npm run build` hỏng trước
giờ trình bày thì không có gì để chiếu.

```
frontend/
├── index.html        khung 9 màn hình
├── styles.css        theme sáng/tối, responsive tới ~400px
└── js/
    ├── markdown.js   render Markdown (bảng + marker trích dẫn), escape mọi thứ LLM sinh
    ├── api.js        nơi duy nhất biết endpoint và hình dạng response, gồm cả SSE qua POST
    └── app.js        từng màn hình + các khối hiển thị dùng chung
```

## Chạy

Backend đã mount sẵn thư mục này, nên chỉ cần:

```bash
cd backend && uv run uvicorn app.main:app --reload --port 8080
```

rồi mở **http://localhost:8080/ui/** (vào `http://localhost:8080` cũng tự chuyển
sang). Đi qua chính backend thì trang và API cùng origin — không phải mở CORS,
không phải dựng thêm web server.

Muốn tách riêng (Live Server, `python -m http.server`…) thì mở **Tham số** ở góc
phải và sửa **API base URL** về `http://localhost:8080`; giá trị này lưu trong
`localStorage`.

## Màn hình ↔ endpoint

| Màn hình | Gọi | Hiển thị đặc thù |
|---|---|---|
| Agent tổng | `POST /api/agent/chat`, `POST /api/agent/upload` | nhãn định tuyến (ý định · độ tin · nguồn quyết định), file sinh ra, `missing_input` bấm được |
| Hỏi đáp tài liệu | `POST /api/chat/qa/stream` (hoặc `/qa`) | chữ chảy theo SSE, marker `[n]` bấm ra nguyên văn đoạn nguồn |
| Soát văn bản | `POST /api/documents/review`, `GET /rule-sets` | chọn bộ tiêu chí, loại văn bản hệ thống tự nhận, lỗi thể thức tách khỏi lỗi chữ nghĩa |
| Soạn báo cáo | `POST /api/reports/draft` | từng mục kèm loại (`data`/`table`/`llm`), kết quả kiểm chứng số, `missing_input` bấm được |
| Tổng hợp báo cáo | `POST /api/reports/aggregate` | chọn nguồn số liệu (CSDL hay đọc thẳng bảng trong báo cáo đơn vị), số liệu gốc value/prev/delta/%, bảng theo đơn vị, đối chiếu file |
| Tạo slide | `POST /api/presentations/create` | xem trước từng slide + JSON trung gian đã lọc |
| Kho tri thức | `POST /api/documents/upload`, `/ingest-text`, `GET /stats`, `DELETE /{doc_id}` | số point, tên các vector |
| Truy hồi (debug) | `POST /api/chat/search` | hạng từng nhánh dense/lexical/bm25, điểm RRF, điểm rerank, thời gian |
| Công cụ & hệ thống | `GET /health`, `GET /api/agent/tools` | trạng thái Qdrant/LLM/CSDL, danh mục tool |

## Vài điểm cố ý

- **Mọi chuỗi do LLM sinh đều đi qua `MD.escape` trước khi ghép HTML.** Câu trả
  lời là dữ liệu không tin được; dán thẳng vào `innerHTML` là mở cửa cho XSS
  ngay trên trang demo.
- **Yêu cầu dài thì phải dừng được.** Soạn báo cáo mất vài chục giây; nút gửi
  đổi thành nút dừng, các màn hình workflow có nút *Huỷ* ngay trong ô chờ —
  không thì người dùng chỉ còn cách tải lại trang.
- **Ô chờ đếm giây thật, không vẽ thanh tiến trình giả.** Tạo slide mất 76-90
  giây và endpoint trả về một cục (không phát sự kiện), nên `loaderNode` nhận
  `hint = {text, slowAfter}` rồi đếm thời gian đã trôi. Quá `slowAfter` thì đổi
  chữ thành "lâu hơn thường lệ — vẫn đang chạy". Thanh tiến trình đoán trước sẽ
  chạy tới 100% rồi đứng im, tức là nói dối đúng lúc người dùng cần tin nhất.
  Node trả về có `.stop()`; `runWorkflow` gọi nó ở cả nhánh xong lẫn nhánh lỗi,
  nếu không `setInterval` sống tiếp sau khi ô chờ bị gỡ khỏi DOM.
- **Thứ backend từ chối làm thì giao diện cũng không che.** `missing_input`,
  `validation.status = failed`, `rule_check.status = partial` đều hiện rõ thay
  vì hiển thị như đã xong.
- **Không bắt nhập lại thứ câu yêu cầu đã nói.** Màn soạn báo cáo bỏ ô *Mã đơn
  vị* và danh sách *Mẫu có sẵn*: `extract_params_node` đã tự nhận đơn vị từ chính
  câu yêu cầu (đối chiếu danh mục đơn vị trong ERP) và tự chọn mẫu. Không nêu đơn
  vị thì backend trả `missing_input` và giao diện hiện ra để bổ sung - hỏi đúng
  lúc cần, thay vì bắt điền trước mọi lần.
  `API.templates()` vẫn còn trong `api.js` dù không màn nào gọi: file đó là bản đồ
  đầy đủ của API, endpoint bên backend vẫn sống.
- **JSON thô luôn có một chỗ xem.** Mỗi màn hình workflow giữ một khối *JSON đầy
  đủ* để đối chiếu khi con số trên màn hình trông đáng ngờ.

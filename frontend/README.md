# Giao diện demo — HTML/CSS/JS thuần

Không build, không npm, không CDN: ba file tĩnh nạp thẳng vào trình duyệt. Chọn
như vậy vì máy demo có thể không có mạng, và một bước `npm run build` hỏng trước
giờ trình bày thì không có gì để chiếu.

```
frontend/
├── index.html        khung 8 màn hình
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
| Agent tổng | `POST /api/agent/chat`, `POST /api/agent/upload` | nhãn định tuyến, file sinh ra, `missing_input` bấm được — và **hỏi đáp tài liệu** (SSE, marker `[n]` bấm ra nguyên văn đoạn nguồn) |
| Soát tài liệu | `POST /api/documents/review`, `GET /rule-sets`, `POST /giao-viec` | **bản dựng lại tài liệu, lỗi khoanh ngay tại chỗ**; bảng phân công; soạn công văn/quyết định giao việc |
| Soạn báo cáo | `POST /api/reports/draft` (`nguon=tai_lieu`), `POST /api/agent/upload` | soạn từ MỘT tài liệu tải lên; từng mục kèm loại, kết quả kiểm chứng số, thẻ nói rõ mức bảo đảm |
| Tổng hợp báo cáo | `POST /api/reports/aggregate` (`nguon_so_lieu=csdl`) | gộp nhiều đơn vị từ CSDL; số liệu gốc value/prev/delta/%, bảng theo đơn vị, đối chiếu file |
| Tạo slide | `POST /api/presentations/create` | xem trước từng slide + JSON trung gian đã lọc |
| Sơ đồ tư duy | `POST /api/documents/upload`, `POST /api/mindmap/generate`, `POST /{doc_id}/section` | chọn file rồi bấm nút mới chạy; xem được hai kiểu — **danh sách** (bấm một mục thì mục đó mới đi truy hồi và viết nội dung, chấm xanh = đã có sẵn) và **đồ thị** (cây nằm ngang, cạnh bezier, bấm nhánh để mở/đóng, lăn chuột phóng to, kéo nền để đi) |
| OCR tài liệu | `POST /api/agent/upload`, `POST /api/ocr/extract/stream` | ảnh trang → Markdown bằng **chính model đang phục vụ cả hệ thống** (có vision, không nạp thêm mô hình); nhận trang qua SSE nên trang hiện dần chứ không đợi xong cả tài liệu, mỗi trang một thẻ có nhãn nguồn (mô hình đọc / lớp text / trang trắng / đọc hỏng), gạt qua lại giữa bản dựng và Markdown thô, chép hoặc tải `.md` |
| Kho tri thức | `POST /api/documents/upload`, `/ingest-text`, `GET /stats`, `DELETE /{doc_id}` | số point, tên các vector |
| Truy hồi (debug) | `POST /api/chat/search` | hạng từng nhánh dense/lexical/bm25, điểm RRF, điểm rerank, thời gian |
| Công cụ & hệ thống | `GET /health`, `GET /api/agent/tools` | trạng thái Qdrant/LLM/CSDL, danh mục tool |

## Vài điểm cố ý

- **Đồ thị sơ đồ tư duy tự tính bố cục, không kéo thư viện về.** Trang phải chạy
  được cả khi máy không ra được Internet. Phần việc cũng gọn: một lượt hậu thứ tự
  xếp lá nối nhau rồi đặt cha vào giữa đàn con. Hộp là `<div>` chứ không phải
  `<text>` trong SVG — tiêu đề tiếng Việt cần xuống dòng mà SVG không tự ngắt
  dòng; SVG nằm dưới chỉ để vẽ cạnh. Chiều cao hộp phải **đo sau khi gắn vào
  DOM** rồi mới xếp chỗ: đoán theo số ký tự thì tiêu đề dài ngắn khác nhau sẽ
  làm hộp đè lên nhau.
- **`[hidden]` được chốt `!important` một lần trong CSS.** Quy tắc
  `[hidden]{display:none}` là của trình duyệt, nên bất kỳ khai báo `display` nào
  của mình cũng đè được nó — `.ghost-btn{display:inline-flex}` và
  `.attach-row{display:flex}` từng làm nút và hàng đính kèm đã gắn `hidden` vẫn
  hiện nguyên trên màn hình. Chốt ở một chỗ rẻ hơn nhớ né ở từng chỗ dùng.
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
- **Hỏi đáp tài liệu nằm trong Agent tổng, không có màn riêng.** Agent vốn đã có
  ý định `qa` chạy đúng đường RAG đó và `renderAgentResult` đã dựng "Nguồn trích
  dẫn" kèm marker bấm được, nên một màn riêng chỉ là ô nhập thứ hai cho cùng một
  việc. Hai công tắc *Streaming* và *Rerank* cũng bỏ theo: chúng luôn bật, phơi ra
  chỉ mời người dùng tắt đi rồi thắc mắc sao chậm và kém chính xác hơn.
  **Đánh đổi phải biết:** câu vừa tra được CSDL vừa tra được tài liệu thì agent
  ưu tiên CSDL. "Phòng Kỹ thuật kiểm kê bao nhiêu trang thiết bị" ra **2** (ERP),
  thêm "theo tài liệu trong kho" mới ra **79** (tài liệu) kèm trích dẫn. Câu gợi ý
  trên màn trống dùng đúng cách nói đó để người dùng thấy ngay.
  Hội thoại cũ kiểu `qa` vẫn mở lại được - chúng rơi về màn Agent tổng.
- **Mỗi màn một nguồn, không bắt người dùng chọn.** Soạn báo cáo = từ MỘT tài
  liệu tải lên; Tổng hợp = từ CSDL nhiều đơn vị. Hai ô chọn nguồn trước đây bị bỏ
  vì chúng bắt người dùng hiểu sự khác nhau giữa hai workflow trước khi làm được
  việc. Backend vẫn giữ cả hai nhánh ở mỗi endpoint - agent tổng dùng nhánh CSDL
  của `/draft` qua ý định `draft` - chỉ là không phơi ra giao diện nữa.
- **Nguồn nào thì nói rõ nguồn đó.** Màn soạn báo cáo có hai nguồn cho ra hai
  văn bản trông giống hệt nhau, nhưng nhánh tài liệu chỉ bảo đảm được "số này có
  nguyên văn trong file", còn nhánh CSDL bảo đảm "số này truy về được một trường
  dữ liệu". Chọn nhánh tài liệu thì kết quả hiện một thẻ nói thẳng điều đó, kèm
  tên file đã đọc và có tìm thấy bảng số liệu hay không. Người ký cần biết mình
  đang cầm loại nào.
- **Màn soát hiện TÀI LIỆU, không hiện danh sách lỗi.** Trước đây kết quả là một
  danh sách phẳng; đọc xong vẫn phải cầm từng dòng đi dò lại trong file gốc, mà
  một văn bản vài chục lỗi thì việc dò còn lâu hơn việc soát. Giờ `blocks` trong
  phản hồi mang theo phông, cỡ chữ, in đậm, canh lề, giãn dòng và lề trang thật
  của từng khối, `findings` mang theo `start`/`end` là vị trí ký tự - đủ để dựng
  lại trang rồi khoanh đúng chỗ. Danh sách vẫn còn nhưng gập lại: nó để đối chiếu
  và để đếm, không để đọc.
- **Hai kiểu khung, cố ý không giống nhau.** Khung **liền nét** là chỗ đo được
  bằng vị trí ký tự - thừa dấu cách, thiếu dấu cách sau dấu câu, lặp từ, ngoặc
  không khớp; máy chắc chắn đúng. Khung **đứt nét** là phán đoán của LLM - chính
  tả, ngữ pháp, diễn đạt, logic; cần người xác nhận. Vẽ giống nhau là nói dối về
  mức bảo đảm, mà người ký cần biết chỗ nào máy chắc chắn.
- **Khối PDF đặt theo toạ độ thật, không đoán canh lề.** PDF không lưu canh lề của
  đoạn (`alignment` luôn `null`), mà văn bản hành chính xếp phần đầu thành hai cột:
  tên cơ quan và số ký hiệu bên trái, quốc hiệu và địa danh bên phải. Đoán "căn
  giữa hay căn phải" thì phải dò ra cột chữ trước, mà trang hai cột làm phép dò đó
  sai ngay từ đầu. Dùng thẳng `bbox` đặt `margin-left` và `width` thì cả hai cột
  về đúng chỗ. Nới 4% chiều rộng vì phông trình duyệt lệch phông trong PDF từng
  phần nghìn, khít quá thì chữ cuối rớt xuống dòng.
- **Mọi thuộc tính đọc từ file cũng là dữ liệu không tin được.** Một tệp `.docx`
  dựng có chủ đích đặt được tên phông kiểu `x;background:url(...)`; ghép thẳng
  vào `style="..."` là mở đúng cái cửa mà `MD.escape` đang đóng ở chỗ khác. Nên
  `safeFont` lọc tên phông còn chữ-số-cách-gạch, `safeNum` ép số và chặn hai đầu,
  và cả chuỗi style vẫn đi qua `esc` lần nữa trước khi vào HTML. Tên phông dùng
  nháy **đơn**: nháy kép đóng sớm thuộc tính và làm vỡ thẻ.
- **Bảng phân công xuất được thành văn bản trình ký.** Đọc bảng trên màn hình rồi
  gõ lại vào Word là chép tay một thứ hệ thống đã biết. Nút *Soạn văn bản giao
  nhiệm vụ* đổ đúng bảng đó ra `.docx` theo thể thức Nghị định 30, chọn giữa
  **công văn** và **quyết định** (mặc định là mẫu backend gợi ý theo loại văn bản
  đến). Ô thể thức để trống thì file in dấu chấm lửng - hệ thống không tự đặt số
  ký hiệu hay tên người ký.
- **JSON thô luôn có một chỗ xem.** Mỗi màn hình workflow giữ một khối *JSON đầy
  đủ* để đối chiếu khi con số trên màn hình trông đáng ngờ.

# TPV Trợ lý — Hướng dẫn tích hợp API

Tài liệu cho đội dựng giao diện trên website có sẵn. Mọi giá trị trong đây lấy
từ hệ thống đang chạy, không phải ví dụ giả.

| | |
|---|---|
| Base URL | `https://chatbot-demo.tpvtech.vn` |
| Swagger | `https://chatbot-demo.tpvtech.vn/docs` |
| OpenAPI JSON | `https://chatbot-demo.tpvtech.vn/openapi.json` |
| Giao diện mẫu | `https://chatbot-demo.tpvtech.vn/ui/` |
| Định dạng | JSON, UTF-8. Tiếng Việt có dấu ở cả yêu cầu lẫn phản hồi |

Giao diện mẫu ở `/ui/` gọi **đúng những endpoint trong tài liệu này**, không có
đường tắt nội bộ nào. Khác biệt duy nhất: nó được backend phục vụ cùng origin
nên dùng đường dẫn tương đối (`/api/agent/chat`) và không vướng CORS. Các bạn gọi
từ domain khác nên phải dùng URL tuyệt đối và xin mở CORS (mục 2.1).

Muốn đối chiếu cách dựng, mở `/ui/` rồi xem tab Network. Mã nguồn giao diện mẫu
phục vụ công khai, đọc thẳng được — không rút gọn, không đóng gói:

- `…/ui/js/api.js` — lớp gọi API, một file duy nhất biết mọi đường dẫn backend
- `…/ui/js/app.js` — dựng giao diện; hàm `renderAgentResult` là bản tham chiếu
  của mục 13b
- `…/ui/js/markdown.js` — trình render Markdown tối giản

Bí chỗ nào thì mở file tương ứng ra xem, nhanh hơn hỏi.

---

## 1. Chạy thử trong một phút

Toàn bộ sản phẩm đi qua **một endpoint duy nhất**. Không cần client chọn nghiệp
vụ — gửi câu tiếng Việt, backend tự định tuyến.

```bash
curl -X POST https://chatbot-demo.tpvtech.vn/api/agent/chat \
  -H "Content-Type: application/json" \
  -d '{"request":"Nhân sự Phòng Kinh doanh kỳ 2026-08"}'
```

```json
{
  "answer": "Nhân sự Phòng Kinh doanh kỳ 2026-08:\n\n- Tổng nhân sự: 11 người [1]\n- ...",
  "conversation_id": "9d4a88e7-51e1-4e46-b41f-553685187d80",
  "intent": "agent",
  "routing": { "intent": "agent", "confidence": 1.0, "reason": "...", "clarify": "", "source": "llm" },
  "plan":    { "steps": [ ... ], "confidence": 1.0, "clarify": "", "reason": "...", "source": "llm" },
  "steps":   [ { "id": "s1", "intent": "agent", "answer": "...", "attempts": 1, "empty": false, "error": "" } ],
  "citations": [],
  "refs":      [ { "id": 1, "kind": "cong_cu", "label": "get_personnel_statistics(...)", "snippet": "...", "locator": {...} } ],
  "artifacts": [],
  "missing_input": [],
  "result": { ... },
  "trace": null,
  "error": ""
}
```

Dựng được giao diện chạy ổn chỉ cần đọc đúng **6 trường**: `answer`,
`conversation_id`, `artifacts`, `citations` + `refs`, `missing_input`, `error`.
Những trường còn lại là để hiện tiến trình cho đẹp.

---

## 2. Bốn thứ chặn bạn trước khi viết dòng code đầu tiên

### 2.1. CORS — gần như chắc chắn bạn sẽ vướng

Backend chỉ nhận request từ origin đã khai trong biến môi trường `CORS_ORIGINS`.
Hiện tại danh sách là `http://localhost:3000` và `http://localhost:5173`.

**Domain của các bạn chưa có trong đó**, nên gọi thẳng từ trình duyệt sẽ bị chặn.
Hai cách:

- **Xin thêm origin** — báo cho đội vận hành domain của bạn (kể cả domain
  staging và localhost lúc dev). Nhanh nhất, và là cách nên dùng.
- **Gọi qua server của bạn** — trình duyệt gọi backend của bạn, backend của bạn
  gọi sang đây. Không vướng CORS, và giấu được URL nội bộ. Nhưng phải tự lo
  chuyển tiếp SSE (mục 4) nếu muốn hiện tiến trình.

`allow_credentials` đang bật, nên nếu bạn gửi cookie thì origin phải khai đích
danh, không dùng `*` được.

### 2.2. Token truy cập

Hiện tại `PUBLIC_ACCESS_TOKEN` **để trống** — API mở, không cần token.

Khi nào đội vận hành bật lên, mọi request (kể cả `/health`) phải kèm token theo
một trong ba cách, dùng một là đủ:

```
Header:      X-Access-Token: <token>
Query:       ?token=<token>          ← gọi một lần, backend set cookie 24h
Cookie:      tpv_access=<token>
```

Thiếu hoặc sai token → `401 {"detail": "Thiếu hoặc sai token truy cập"}`.
Hãy viết code sẵn nhánh này, đừng đợi lúc bật mới sửa.

### 2.3. Danh tính — **quyết định cả QUYỀN lẫn TENANT**

Gửi `X-User-Id` là thứ quan trọng nhất trong tích hợp này. Từ nó backend tra ra
**quyền** của tài khoản (mục 2.4) và **tenant** của tài khoản đó.

Thứ tự lấy danh tính:

1. Tài khoản đăng nhập — **chưa dựng**, là điểm cắm sẵn cho sau này.
2. Header `X-User-Id` — UserId trong ERP, ví dụ `414`. Backend tra quyền và
   **lấy luôn tenant của tài khoản này**, đè lên `X-Tenant-Id` nếu có.
3. Header `X-Tenant-Id` — chỉ còn tác dụng khi không gửi `X-User-Id`.
4. `ERP_TENANT_ID` trong cấu hình server — dùng khi client không khai gì.

> Tenant đi theo TÀI KHOẢN chứ không theo header, và đó là cố ý: gửi user của
> công ty A kèm tenant của công ty B mà hệ thống nghe theo header thì bạn vừa
> đọc số liệu của công ty B dưới danh nghĩa người của công ty A.

Hiện `TRUST_IDENTITY_HEADERS=true`, tức **client tự khai tenant nào cũng được**.
Đây là lỗ hổng có chủ ý cho giai đoạn chạy thử trong mạng nội bộ. Khi hệ thống
lên thật, cờ này sẽ tắt và chỉ token nói được danh tính — lúc đó header bị bỏ
qua, không báo lỗi.

Gửi thiếu header thì bạn **vẫn nhận được số liệu**, chỉ là của tenant mặc định.
Không có exception, không có cảnh báo — sai lệch chỉ lộ ra khi có người thấy con
số trong báo cáo là lạ. Vì vậy hãy tự kiểm bằng:

```bash
curl https://chatbot-demo.tpvtech.vn/whoami \
  -H "X-User-Id: 414"
# {"tenant_id":94,"user_id":"414","source":"header","trust_identity_headers":true}
```

`source` nói backend đọc danh tính từ đâu: `token` | `header` | `default`.
**Nếu bạn có gửi header mà thấy `"source":"default"` thì header đang bị rơi
đâu đó** — proxy, CDN, hoặc viết sai tên. Sửa trước khi đi tiếp.

`X-Tenant-Id` sai định dạng (không phải số) bị chặn bằng `400` kèm câu nói rõ,
chứ không âm thầm rơi về tenant mặc định — đó là cố ý.

### 2.4. Phân quyền — quyền lấy từ ERP, không đặt trong hệ thống này

**Không gửi `X-User-Id` thì không có phân quyền**: backend không có căn cứ để
chặn ai, nên cho qua tất. Gửi vào thì mọi lời gọi bị soi theo đúng quyền mà quản
trị ERP đã cấp cho tài khoản đó.

Luồng: `X-User-Id` → `AbpUsers` → `AbpUserRoles` → `AbpRoles` → `AbpPermissions`.
Cache 60 giây, nên **đổi quyền bên ERP thì hệ thống này thấy trong vòng một phút**
— không sửa code, không khởi động lại.

Vai trò `Admin` tĩnh của ABP được toàn quyền, kể cả khi bảng `AbpPermissions`
không có dòng nào cho nó (đó là cách ABP hoạt động).

#### Quyền cần cho từng việc

| Việc | Quyền ERP |
|---|---|
| Hỏi đáp, tra số liệu, soát tài liệu | `Ai.AiChatbot` |
| Soạn văn bản, tổng hợp báo cáo | `Ai.ReportSummary.Create` |
| Tạo slide | `Ai.Slide.Create` |
| Đọc số liệu **nhân sự** | `Hrm.EmployeeProfile.View` |
| Đọc số liệu **thiết bị** | `Asm.Asset.View` |
| Nạp tài liệu vào kho | `Ai.ReportSummary.Create` |
| **Xoá tài liệu khỏi kho** | **chỉ vai trò Admin** |

Hai quyền số liệu tách riêng với quyền dùng chatbot: một người được dùng trợ lý
không đương nhiên được xem hồ sơ nhân sự cả công ty. Thiếu quyền mảng nào thì
mảng đó bị cắt khỏi báo cáo **trước khi truy vấn chạy**.

#### Thiếu quyền thì nhận gì

Endpoint chuyên biệt trả **HTTP 403**:

```json
{"detail": "Tài khoản của bạn không có quyền soạn văn bản. Quyền cần có: `Ai.ReportSummary.Create`. Vai trò hiện tại: Nhân viên DEV. Liên hệ quản trị hệ thống ERP để được cấp."}
```

Qua `/api/agent/chat` thì **vẫn là HTTP 200**, câu từ chối nằm trong `answer` —
vì một yêu cầu nhiều bước có thể có bước được phép và bước không. Hiện `answer`
như câu trả lời bình thường; nó đã nêu rõ thiếu quyền nào và đi hỏi ai.

Câu từ chối đã viết sẵn cho người dùng cuối, **hiện thẳng được**, không cần dịch.

#### Lấy danh sách tài khoản (chỉ để demo)

`GET /api/agent/accounts` trả về tài khoản của tenant hiện tại kèm vai trò và số
quyền — đủ để dựng ô "đăng nhập bằng" khi chưa có đăng nhập thật:

```json
{"tenant_id": 94, "current_user_id": "414",
 "accounts": [{"user_id": 407, "user_name": "BQP1", "display_name": "BQP1",
               "roles": ["Admin"], "is_admin": true, "permission_count": 7}]}
```

> ⚠️ Endpoint này bày danh sách nhân sự cho bất kỳ ai gọi được API. **Tắt nó khi
> lên thật**, lúc đó danh tính phải đến từ phiên đăng nhập chứ không phải từ một
> ô chọn.

---

## 3. `POST /api/agent/chat` — cửa chính

### Yêu cầu

| Trường | Kiểu | Bắt buộc | Ý nghĩa |
|---|---|---|---|
| `request` | string, 1–4000 ký tự | ✅ | Câu yêu cầu tiếng Việt. Không cần nêu workflow |
| `conversation_id` | string \| null | | Bỏ trống = tạo hội thoại mới, id trả về trong phản hồi |
| `file_id` | string | | Định danh file đã tải lên (mục 6). Không có file thì agent không đi nhánh soát tài liệu |
| `ma_don_vi` | string | | Đơn vị mặc định khi soạn văn bản |
| `inputs` | object | | Người ký, số ký hiệu, nơi nhận… nếu đã biết trước |
| `include_trace` | bool | | `true` để nhận thêm `trace` gỡ lỗi. Nặng, chỉ bật khi cần |

### Phản hồi

| Trường | Dùng để làm gì |
|---|---|
| `answer` | **Markdown**, không phải plain text. Xem mục 3.1 |
| `conversation_id` | Lưu lại, gửi kèm lượt sau |
| `intent` | Nghiệp vụ của bước chính: `qa` \| `document` \| `draft` \| `report` \| `presentation` \| `agent` |
| `routing` | `{intent, confidence 0–1, reason, clarify, source}`. `source`: `llm` \| `keyword` \| `rule` |
| `plan` | Kế hoạch: `{steps[], confidence, clarify, reason, source}`. `plan.source` là `llm` \| `router` \| `rule` — **khác enum của `routing.source`** |
| `steps` | Kết quả từng bước. Xem mục 5 |
| `citations` | Nguồn của nhánh hỏi đáp tài liệu |
| `refs` | Nguồn của các nhánh còn lại |
| `artifacts` | File sinh ra (.docx / .pptx) |
| `missing_input` | Thiếu thông tin thì agent dừng để hỏi lại |
| `result` | Kết quả thô của workflow. Hình dạng đổi theo `intent` — trừ `result.validation` **luôn có và phải hiện** (mục 9) |
| `trace` | `null` trừ khi `include_trace: true` |
| `error` | Chuỗi rỗng là bình thường. Có chữ là có sự cố |

### 3.1. `answer` là Markdown

Backend trả về Markdown thật: bảng, `**đậm**`, gạch đầu dòng, xuống dòng `\n`.
Đổ thẳng vào `innerHTML` sẽ ra một đống ký tự `*` và `|`.

Bảng số liệu xuất hiện **rất thường xuyên** — mọi câu hỏi số liệu nhiều đơn vị
đều trả về bảng Markdown. Nên trình render của bạn bắt buộc phải hỗ trợ bảng.

```
| Đơn vị | Nhân sự | Kỳ trước |
| :--- | :--- | :--- |
| Phòng Kinh doanh | 11 | 10 |
```

> Giao diện mẫu dùng một trình render nhỏ tự viết, đọc được ở
> `https://chatbot-demo.tpvtech.vn/ui/js/markdown.js` (~6KB, không phụ thuộc gì).
> Bạn dùng `marked`, `markdown-it` hay gì tuỳ ý — chỉ cần **bật bảng** và
> **sanitize HTML đầu ra** (nội dung có phần do LLM sinh).

---

## 4. Streaming (SSE) — thứ làm giao diện “mượt”

`POST /api/agent/chat` là lời gọi chặn. Một yêu cầu mất **3 giây đến hơn một
phút** tuỳ nghiệp vụ (mục 9). Suốt thời gian đó giao diện chỉ có một vòng xoay,
và người dùng không phân biệt được “đang tổng hợp số liệu” với “đã treo”.

`POST /api/agent/chat/stream` nhận **cùng một body**, trả về `text/event-stream`.

> ⚠️ Là `POST`, không phải `GET` — nên **`EventSource` của trình duyệt không
> dùng được**. Dùng `fetch` + `ReadableStream`, hoặc thư viện như
> `@microsoft/fetch-event-source`.

### Định dạng khung, và cách tự parse

Khung SSE ở đây là chuẩn tối giản: hai dòng, kết thúc bằng một dòng trống, xuống
dòng bằng `\n` (không phải `\r\n`).

```
event: step_start\n
data: {"id":"s1","intent":"agent","request":"...","label":"Tra số liệu"}\n
\n
```

> 🚨 **Lỗi HTTP KHÔNG đi qua kênh SSE.** Body sai schema thì `/chat/stream` trả về
> `422` với `content-type: application/json` — **không có khung SSE nào cả**.
> Phải kiểm `res.ok` **trước khi** bắt đầu đọc stream, nếu không bạn sẽ đi parse
> một thân JSON lỗi như thể nó là luồng sự kiện. Sự kiện `error` chỉ dành cho sự
> cố xảy ra **giữa chừng**, khi stream đã chạy.

Bộ parse tối thiểu — đủ dùng, không cần thư viện:

```js
const res = await fetch(BASE + '/api/agent/chat/stream', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json',
             'Accept': 'text/event-stream',
             'X-Tenant-Id': tenantId },
  body: JSON.stringify({ request: cau }),
  signal,                                  // AbortController để huỷ giữa chừng
});
if (!res.ok) throw new Error((await res.json()).detail);   // ← BẮT BUỘC, xem trên

const reader = res.body.getReader();
const dec = new TextDecoder('utf-8');
let buf = '';

const xuly = (khung) => {
  let ev = 'message', data = [];
  for (const dong of khung.split('\n')) {
    if (dong.startsWith('event:')) ev = dong.slice(6).trim();
    else if (dong.startsWith('data:')) data.push(dong.slice(5).replace(/^ /, ''));
  }
  if (!data.length) return;                // khung không có data thì bỏ qua
  let payload;
  try { payload = JSON.parse(data.join('\n')); } catch { return; }
  handlers[ev]?.(payload);
};

for (;;) {
  const { done, value } = await reader.read();
  if (done) break;
  buf += dec.decode(value, { stream: true });
  const phan = buf.split('\n\n');
  buf = phan.pop();                        // phần chưa trọn khung, giữ lại
  phan.forEach((k) => k.trim() && xuly(k));
}
if (buf.trim()) xuly(buf);                 // đừng quên khung cuối
```

Bốn chỗ dễ sai trong đoạn trên, đều là lỗi thật:

1. **Không kiểm `res.ok`** → parse nhầm thân JSON lỗi.
2. **Quên giữ lại phần dư của buffer** (`buf = phan.pop()`) → một khung bị cắt
   đôi giữa hai gói TCP sẽ mất.
3. **Quên xử lý buffer sau khi stream kết thúc** → mất khung `done`, tức mất
   toàn bộ kết quả.
4. **Quên bỏ dấu cách sau `data:`** → `JSON.parse` vẫn chạy, nhưng chuỗi bên
   trong lệch một ký tự ở vài trình chủ khác.

Huỷ bằng `AbortController`: backend thấy client đóng kết nối thì **dừng luôn**
tác vụ, không chạy nốt một vòng công cụ mà không ai đọc.

### Thứ tự sự kiện

```
thinking → plan → ( step_start → step_progress* → [step_retry] → step_done )* → done
```

Đây là khung, không phải thứ tự cứng. `thinking`, `answer_delta` và
`answer_reset` chen vào bất cứ lúc nào — thực tế có một `thinking` nữa ngay sau
`plan` (đó là lý do agent tách bước như vậy). `error` cắt ngang ở đâu cũng được.
Hãy viết bộ xử lý theo kiểu **switch theo tên sự kiện**, đừng giả định thứ tự.

### Bảng sự kiện

| `event` | `data` | Nên làm gì |
|---|---|---|
| `thinking` | `{text}` | Hiện dòng trạng thái mờ. Đây là suy luận của model, **không phải câu trả lời** |
| `plan` | `{steps[], confidence, clarify, reason, source}` | Vẽ danh sách bước. Mỗi `step` có `{id, intent, request, depends_on, reason}` |
| `step_start` | `{id, intent, request, label}` | Đánh dấu bước đang chạy. `label` là tên tiếng Việt sẵn dùng |
| `step_progress` | `{round, max_rounds, phase, tools?}` | `phase` là `"đang suy nghĩ"` hoặc `"đang tra"`. `tools` **chỉ có ở pha `"đang tra"`**, và chỉ là **tên** công cụ — không tham số, không kết quả |
| `step_retry` ⓒ | `{id, from, to, label}` | Bước không ra kết quả, hệ thống đang thử lại bằng nghiệp vụ khác |
| `step_done` | `{id, intent, planned_intent, label, attempts, retried_as, empty, error}` | Đánh dấu xong. `empty: true` = chạy xong nhưng không có kết quả dùng được |
| `answer_delta` | `{text}` | **Nối thêm** vào chuỗi câu trả lời đang hiện |
| `answer_reset` ⓒ | `{}` (không có trường nào) | **Xoá sạch** chuỗi đã nối. Xem cảnh báo dưới |
| `done` | `AgentResponse` đầy đủ | Thay thế toàn bộ bằng payload này |
| `error` ⓒ | `{detail}` | Hiện lỗi, dừng stream |

ⓒ = **có điều kiện**, nhiều lượt chạy không phát sự kiện này lần nào. Vẫn phải
viết code xử lý: chúng chỉ xuất hiện đúng lúc mọi thứ đang đi chệch, tức đúng lúc
giao diện dễ vỡ nhất.

Một lượt thật (câu hỏi hai vế, agent gọi hai công cụ) phát ra như sau:

```
event: thinking       {"text": "Đang đọc yêu cầu và tách thành các bước…"}
event: plan           {"steps":[{"id":"s1","intent":"agent",...}],"confidence":1.0,...}
event: thinking       {"text": "<lý do agent tách bước như vậy>"}
event: step_start     {"id":"s1","intent":"agent","request":"...","label":"Tra số liệu"}
event: step_progress  {"round":1,"max_rounds":8,"phase":"đang suy nghĩ"}
event: step_progress  {"round":1,"max_rounds":8,"phase":"đang tra","tools":["get_reporting_status"]}
event: step_progress  {"round":2,"max_rounds":8,"phase":"đang suy nghĩ"}
event: step_progress  {"round":2,"max_rounds":8,"phase":"đang tra","tools":["get_personnel_statistics"]}
event: step_progress  {"round":3,"max_rounds":8,"phase":"đang suy nghĩ"}
event: answer_delta   {"text":"Kỳ báo cáo "}          ← lặp lại rất nhiều lần
event: step_done      {"id":"s1","intent":"agent","planned_intent":"agent","label":"Tra số liệu",
                       "attempts":1,"retried_as":"","empty":false,"error":""}
event: done           {<AgentResponse đầy đủ>}
```

`max_rounds` hiện là **8** — số vòng công cụ tối đa của một bước. Dùng cặp
`round / max_rounds` để vẽ thanh tiến trình.

### Ba cái bẫy của SSE

**1. `answer_reset` bắt buộc phải xử lý.** Model hay viết vài câu dẫn ("Để tôi
tra số liệu…") rồi mới quyết định gọi công cụ. Mấy câu đó đã chảy ra màn hình qua
`answer_delta` nhưng **không phải câu trả lời**, nên backend phát `answer_reset`
để bảo giao diện **xoá sạch chuỗi đã nối và bắt đầu lại**. Bỏ qua sự kiện này thì
người dùng thấy câu dẫn dính liền với câu trả lời thật, có khi lặp hai ba lần.

**2. `done` là nguồn sự thật, không phải chuỗi bạn tự nối.** Chuỗi ghép từ
`answer_delta` chỉ để hiện cho mượt. Khi `done` tới, hãy **thay thế** bằng
`data.answer` rồi dựng lại giao diện từ payload đó. Payload của `done` chính là
`AgentResponse` của `POST /chat` — nên bạn viết **đúng một hàm** dựng giao diện,
dùng chung cho cả hai đường.

**3. Không có sự kiện nào mang kết quả công cụ.** Đây là cố ý: số liệu thô chưa
qua van đối chiếu, đẩy lên màn hình là mời người đọc tin vào con số mà chính hệ
thống chưa xác nhận. Đừng cố tìm cách lấy chúng ra sớm.

Đóng kết nối giữa chừng (người dùng rời trang) thì backend **huỷ luôn** tác vụ,
không chạy nốt. Không cần gọi thêm gì để dọn.

---

## 5. Hiện tiến trình nhiều bước

Một yêu cầu có thể tách thành nhiều bước. Ví dụ *“Tổng hợp nhân sự tháng 8 rồi
làm slide”* ra hai bước: `report` rồi `presentation`.

`depends_on` rỗng nghĩa là bước đó **chạy song song** với các bước rỗng khác.
Muốn vẽ đúng như giao diện mẫu thì gom các bước thành từng đợt: đợt đầu là mọi
bước `depends_on` rỗng, đợt sau là các bước mà mọi phụ thuộc đã nằm ở đợt trước.

Trong `steps` của phản hồi, chú ý ba trường:

- `planned_intent` khác `intent` → bước đã được thử lại bằng nghiệp vụ khác.
- `retried_as` → nghiệp vụ dùng cho lần thử lại.
- `empty: true` → chạy xong nhưng không mang về gì. **Khác với lỗi.**

### Nhãn nghiệp vụ

Backend đã gửi sẵn `label` trong `step_start` / `step_done` / `step_retry`.
**Hãy dùng `label` của backend** thay vì tự dựng bảng ánh xạ — chúng tôi đã tự
vấp: giao diện mẫu từng giữ bảng riêng, rồi nó trôi, và một lượt chạy đổi tên
nghiệp vụ giữa chừng (đang là “Tra số liệu” lúc chạy, xong thì thành “Tool loop”).

Nếu vẫn cần bảng riêng cho huy hiệu cuối, đây là bảng đúng:

| `intent` | Nhãn |
|---|---|
| `qa` | Tra cứu tài liệu |
| `document` | Soát tài liệu |
| `draft` | Soạn văn bản |
| `report` | Tổng hợp báo cáo |
| `presentation` | Tạo slide |
| `agent` | Tra số liệu |

---

## 6. Hội thoại nhiều lượt

```
POST /api/chat/conversations            → {"conversation_id": "<uuid>"}
GET  /api/chat/history/{id}             → {"conversation_id", "turns":[{role, content, ts}]}
DELETE /api/chat/history/{id}           → 204
```

Không bắt buộc gọi `POST /conversations` trước. Cứ gửi `/api/agent/chat` không
kèm `conversation_id`, backend tạo giúp và trả về trong phản hồi — lưu lại rồi
gửi kèm từ lượt hai.

Có lịch sử thì agent hiểu được câu nói tắt: *“vẫn kỳ đó”*, *“đơn vị vừa rồi”*,
*“làm tiếp”*. Không gửi `conversation_id` thì mỗi lượt là một tờ giấy trắng.

---

## 7. File đính kèm

Muốn agent soát một tài liệu thì phải tải file lên trước, rồi truyền `file_id`.

```bash
curl -X POST https://chatbot-demo.tpvtech.vn/api/agent/upload \
  -F "file=@bao_cao.docx"
# {"file_id":"upload:bao_cao.docx","name":"bao_cao.docx","kind":"upload","size":40944,"modified":"..."}
```

```json
{ "request": "Soát giúp tôi tài liệu này", "file_id": "upload:bao_cao.docx" }
```

- `multipart/form-data`, tên field là `file`. Field `kind` tuỳ chọn, mặc định
  `upload`.
- Nhánh soát tài liệu nhận **`.docx`, `.pdf`, `.txt`, `.md`**. Định dạng khác
  trả lỗi nói rõ.
- **Không có `file_id` thì agent không bao giờ chọn nhánh `document`.** Câu
  “soát giúp tài liệu này” mà quên gửi `file_id` sẽ bị định tuyến sang nghiệp vụ
  khác. Nếu giao diện của bạn có nút đính kèm, hãy chắc là `file_id` được gửi kèm.

---

## 8. File kết quả

Nghiệp vụ `draft`, `report`, `presentation` sinh file. Chúng nằm trong
`artifacts`:

```json
"artifacts": [
  {
    "file_id": "BC_TONGHOP_2026-08__t64__20260920-140433.docx",
    "file_name": "BC_TONGHOP_2026-08__t64__20260920-140433.docx",
    "kind": "docx",
    "download_url": "/api/agent/download/BC_TONGHOP_2026-08__t64__20260920-140433.docx"
  }
]
```

`download_url` là **đường dẫn tương đối, đã encode sẵn**. Ghép với base URL là
tải được. Đừng tự dựng URL từ `file_name` — tên file có dấu và ký tự đặc biệt.

`kind` là `docx` | `pptx` | `pdf`.

---

## 9. Trích dẫn `[1]`, `[2]`

Trong `answer` có marker dạng `[1]`, `[2]`, đôi khi `[1][3]`. Chúng trỏ tới
**một trong hai mảng**, tuỳ nghiệp vụ:

| Mảng | Khi nào có | Hình dạng |
|---|---|---|
| `citations` | nhánh `qa` — hỏi đáp trên kho tài liệu | `{id, doc_id, doc_title, section, source, page, snippet, score, matched_by}` |
| `refs` | các nhánh còn lại | `{id, kind, label, snippet, locator, score}` |

`refs[].kind` là `block` | `quy_dinh` | `du_lieu` | `cong_cu` — tương ứng khối
văn bản, quy định, số liệu CSDL, kết quả công cụ.

Cả hai đều mang sẵn `snippet` (nguyên văn đoạn nguồn), nên bấm vào `[1]` là hiện
được ngay, **không phải gọi ngược lại backend**.

Cách dựng tối thiểu: regex `\[(\d+)\]` trong `answer`, đổi thành nút bấm, tra số
đó trong `citations` rồi `refs`, hiện `snippet` trong popover.

Marker trỏ tới số không có trong hai mảng thì bỏ qua, đừng để vỡ giao diện.

---

## 9b. `result.validation` — van kiểm chứng số liệu · **bắt buộc hiện**

Đây là tính năng dễ bị bỏ sót nhất khi dựng giao diện mới, và bỏ sót nó thì hậu
quả nặng: **người dùng đọc một con số chưa được xác minh mà tưởng đã xác minh.**

Sau khi sinh câu trả lời hoặc dựng file, hệ thống dò **từng con số** trong đầu ra
và đối chiếu ngược về dữ liệu nguồn. Kết quả nằm ở `result.validation`, có mặt
ở **mọi nghiệp vụ** (`agent`, `draft`, `report`, `presentation`):

```json
"result": {
  "validation": {
    "status": "passed",
    "checked_numbers": 14,
    "issues": []
  }
}
```

| Trường | |
|---|---|
| `status` | `passed` \| `warning` \| `failed` \| `skipped` |
| `checked_numbers` | Số lượng con số đã dò được và đối chiếu |
| `issues[]` | `{type, section, severity, numbers[], quote}` — `severity` là `error` \| `warning` |

Giao diện mẫu hiện nó thành một huy hiệu ngay cạnh câu trả lời:

| `status` | Nhãn | Màu |
|---|---|---|
| `passed` | `Số liệu khớp · 14 số` | xanh |
| `warning` | `Có cảnh báo · N số` | vàng |
| `failed` | `Không đạt · N số` | đỏ |
| `skipped` | `Bỏ qua kiểm chứng` | xám |

`issues` không rỗng thì mở thêm một khối liệt kê: `type` và `section` cho biết
lỗi ở mục nào, `numbers` là **những con số không truy được về nguồn**, `quote` là
nguyên văn câu chứa nó.

> Đây là thứ phân biệt hệ thống này với một con bot chép số. Một câu trả lời
> `failed` mà hiện y như một câu `passed` là kiểu sai tệ nhất mà giao diện có thể
> gây ra — người dùng không có cách nào tự biết.

---

## 10. Khi agent cần hỏi lại

`missing_input` không rỗng nghĩa là agent dừng vì thiếu thông tin bắt buộc:

```json
{ "missing_input": ["ma_template"], "answer": "Chưa xác định được mẫu báo cáo phù hợp. Bạn muốn dùng mẫu nào?" }
```

Hãy hiện `answer` như một câu hỏi và cho người dùng trả lời trong cùng hội thoại
(gửi lại `conversation_id`), hoặc truyền thẳng giá trị qua `inputs` /
`ma_don_vi`.

Khoá hay gặp: `ma_template` (chưa rõ mẫu báo cáo), và các khoá trong `inputs` như
người ký, số ký hiệu.

`plan.clarify` và `routing.clarify` là câu hỏi lại **tuỳ chọn** — thường rỗng.
Hiện hay không tuỳ bạn; `missing_input` mới là thứ chặn thật.

---

## 11. Giới hạn thời gian — **đọc trước khi dựng màn tạo slide**

Đây là ràng buộc khó chịu nhất của hạ tầng hiện tại.

**Đường public đi qua Cloudflare, trần timeout là 125 giây.** Quá mốc đó
Cloudflare trả `524` và cắt kết nối — backend vẫn chạy tiếp nhưng bạn không nhận
được kết quả. Trần này chỉ gói Enterprise mới nâng được, nên coi như cố định.

Thời gian đo thật trên hệ thống đang chạy:

| Nghiệp vụ | Thời gian điển hình |
|---|---|
| `agent` (tra số liệu) | 3–8 giây |
| `draft` (soạn văn bản) | 4–6 giây |
| `report` (tổng hợp) | 5–8 giây |
| `document` (soát tài liệu) | 11–12 giây |
| `qa` (hỏi đáp tài liệu) | 11–19 giây |
| **`presentation` (tạo slide)** | **66–72 giây** |

Tạo slide chiếm hơn nửa ngân sách 125 giây. Một yêu cầu nhiều bước có slide
(*“tổng hợp rồi làm slide”*) **có thể chạm trần**.

Khuyến nghị:

- **Dùng `/chat/stream` cho mọi yêu cầu có thể sinh slide.** Stream giữ kết nối
  sống bằng sự kiện liên tục, và người dùng thấy tiến trình thay vì màn hình chết.
- Đặt timeout phía client **ít nhất 150 giây**, đừng để mặc định 30 giây.
- Hiện thời gian ước tính khi `intent` là `presentation` — “việc này mất khoảng
  một phút” giúp người dùng không bấm lại.
- Gặp `524`: báo “hệ thống xử lý lâu hơn dự kiến”, **đừng tự động gọi lại** —
  lần chạy trước vẫn đang chiếm GPU.

---

## 12. Lỗi

| Mã | Khi nào | Thân phản hồi |
|---|---|---|
| `400` | `X-Tenant-Id` sai định dạng | `{"detail": "x-tenant-id phải là số nguyên, nhận được 'abc'"}` |
| `401` | Sai/thiếu token (khi đã bật) | `{"detail": "Thiếu hoặc sai token truy cập"}` |
| `403` | Tài khoản thiếu quyền (mục 2.4) | `{"detail": "Tài khoản của bạn không có quyền ..."}` |
| `404` | Tải file không tồn tại | `{"detail": "Không tìm thấy file 'x.docx'"}` |
| `422` | Body sai schema | `{"detail": [{"type":"missing","loc":["body","request"],...}]}` chuẩn FastAPI |
| `500` | Sự cố phía server | `{"detail": "<câu tiếng Việt>"}` |
| `524` | Quá 125 giây | Trang lỗi của Cloudflare, **không phải JSON** |

Ngoài ra `200` vẫn có thể kèm `error` khác rỗng: một bước hỏng nhưng những bước
khác vẫn xong. Hãy hiện `answer` **và** cảnh báo `error`, đừng vứt cả phản hồi.

Thông báo lỗi đã được dịch sẵn sang tiếng Việt cho người dùng cuối, hiện thẳng
được. Chi tiết kỹ thuật (câu SQL, traceback) nằm trong log server, cố ý không
gửi ra ngoài.

**`524` trả về HTML.** Code của bạn phải chịu được việc `response.json()` ném lỗi.

---

## 13. Endpoint chuyên biệt

Nếu website của bạn muốn màn riêng cho từng nghiệp vụ thay vì một ô chat, các
endpoint dưới đây gọi thẳng workflow, bỏ qua bước định tuyến:

| Endpoint | Việc | Quyền cần có |
|---|---|---|
| `POST /api/chat/qa` | Hỏi đáp trên kho tài liệu | `Ai.AiChatbot` |
| `POST /api/chat/qa/stream` | Như trên, dạng SSE | `Ai.AiChatbot` |
| `POST /api/chat/search` | Tìm kiếm thuần, không sinh câu trả lời | `Ai.AiChatbot` |
| `POST /api/documents/review` | Soát tài liệu (multipart) | `Ai.AiChatbot` |
| `GET /api/documents/rule-sets` | Danh sách bộ tiêu chí soát | — |
| `GET /api/documents/stats` | Thống kê kho tri thức | `Ai.AiChatbot` |
| `POST /api/documents/upload` | Nạp file tài liệu vào kho (multipart) | `Ai.ReportSummary.Create` |
| `POST /api/documents/ingest-text` | Nạp tài liệu dạng văn bản thuần | `Ai.ReportSummary.Create` |
| `DELETE /api/documents/{doc_id}` | Xoá tài liệu khỏi kho | **chỉ Admin** |
| `POST /api/reports/draft` | Soạn văn bản cho một đơn vị | `Ai.ReportSummary.Create` |
| `POST /api/reports/aggregate` | Tổng hợp nhiều đơn vị | `Ai.ReportSummary.Create` |
| `GET /api/reports/templates` | Danh sách mẫu báo cáo | — |
| `POST /api/presentations/create` | Tạo slide | `Ai.Slide.Create` |
| `GET /api/reports/download/{filename}` | Tải file do `draft` / `aggregate` sinh ra |
| `GET /api/presentations/download/{filename}` | Tải file .pptx do `create` sinh ra |
| `GET /api/agent/tools` | Danh sách công cụ agent có, kèm mô tả và tham số |
| `GET /health` | Trạng thái Qdrant / LLM / CSDL |

### Tham số của từng endpoint

Chỉ liệt kê trường **bắt buộc** và trường dễ dùng sai. Schema đầy đủ xem `/docs`.

> 🚨 **Tên trường KHÔNG giống nhau giữa các endpoint.** Đây là chỗ sai nhiều nhất
> khi chuyển từ `/api/agent/chat` sang gọi thẳng workflow:
>
> | Endpoint | Tên trường câu hỏi |
> |---|---|
> | `/api/agent/chat` | `request` |
> | `/api/chat/qa` | **`question`** |
> | `/api/chat/search` | **`query`** |
> | `/api/reports/draft`, `/aggregate`, `/api/presentations/create` | `request` |

**`POST /api/chat/qa`** · `/qa/stream`

```json
{ "question": "...", "conversation_id": null, "top_n": null,
  "use_rerank": true, "include_trace": false,
  "filters": { "doc_ids": [], "sources": [], "doc_types": [] } }
```

**`POST /api/chat/search`** — tìm kiếm thuần, không sinh câu trả lời

```json
{ "query": "...", "top_n": 20, "use_rerank": true, "rewrite": true,
  "filters": { "doc_ids": [], "sources": [], "doc_types": [] } }
```

**`POST /api/reports/draft`** — soạn văn bản cho **một** đơn vị

```json
{ "request": "...", "ma_don_vi": "00001", "nguon": "csdl",
  "inputs": { "nguoi_ky": "...", "chuc_vu_ky": "..." } }
```

- `nguon`: `csdl` = lấy số liệu ERP theo mẫu · `tai_lieu` = soạn từ một file đã
  tải lên. **`nguon: "tai_lieu"` thì `file_id` là bắt buộc** (lấy từ
  `POST /api/agent/upload`).
- `ma_don_vi` bỏ trống thì hệ thống hỏi lại qua `missing_input`.

> Nhánh `nguon: "tai_lieu"` là một năng lực riêng: soạn báo cáo **tổng hợp lại
> nội dung một tài liệu người dùng vừa gửi**, thay vì lấy số từ CSDL. Qua
> `/api/agent/chat` thì agent tự chọn nhánh này khi có `file_id`; gọi thẳng thì
> bạn phải tự khai `nguon`.

**`POST /api/reports/aggregate`** — tổng hợp **nhiều** đơn vị

```json
{ "request": "...", "inputs": { "nguoi_ky": "...", "so_ky_hieu": "...", "noi_nhan": "..." } }
```

**`POST /api/presentations/create`**

```json
{ "request": "...", "inputs": { } }
```

**`POST /api/documents/review`** — `multipart/form-data`

| Field | Bắt buộc | |
|---|---|---|
| `file` | ✅ | `.docx`, `.pdf`, `.txt`, `.md` |
| `noi_gui` | | Nơi gửi, nếu biết trước |
| `rule_set` | | Mã bộ tiêu chí — lấy trường `id` từ `GET /api/documents/rule-sets` |

`GET /api/documents/rule-sets` trả về `[{"id","label","version"}]`. Hiện chỉ có
một bộ: `{"id":"chung","label":"Cấu trúc & trình bày (mọi loại tài liệu)"}`. Bỏ
trống `rule_set` là dùng bộ mặc định — **hãy đọc danh sách qua API**, các bộ mới
được thêm bằng file YAML phía server mà không đổi API.

**`POST /api/documents/upload`** — `multipart/form-data`: `file` (bắt buộc), `doc_type`.

**`POST /api/documents/ingest-text`** — nạp văn bản thuần

```json
{ "text": "...", "doc_title": "...", "doc_id": null,
  "source": "", "doc_type": "", "metadata": {} }
```

`text` và `doc_title` bắt buộc.

> ⚠️ **Đường tải file ở đây KHÁC đường của agent.** Gọi `/api/agent/chat` thì
> file nằm trong `artifacts[].download_url` trỏ vào `/api/agent/download/...`.
> Gọi thẳng `/api/reports/draft`, `/api/reports/aggregate` hay
> `/api/presentations/create` thì phản hồi có `download_url` riêng, trỏ vào
> `/api/reports/download/...` hoặc `/api/presentations/download/...`.
>
> Cả ba trường hợp đều **đã có sẵn `download_url` trong phản hồi** — cứ ghép với
> base URL rồi dùng, đừng tự dựng đường dẫn từ tên file.

`POST /api/presentations/create` còn trả thêm `edit_url` (mở bộ slide để sửa
trong Presenton), `slide_count` đếm từ chính file .pptx, và `engine` cho biết
slide do Presenton dựng hay do đường lùi nội bộ dựng.

> **Lấy danh sách mẫu báo cáo qua API, đừng hardcode.** Mẫu được thêm bớt theo
> nhu cầu nghiệp vụ — hiện có `BC_NHANSU`, `BC_THIETBI`, `BC_TAINGUYEN`,
> `BC_TONGHOP`, nhưng danh sách này sẽ đổi.

> ⚠️ `DELETE /api/documents/{doc_id}` xoá thật khỏi kho tri thức, không lấy lại
> được. Giờ chỉ vai trò `Admin` gọi được — nhưng **chỉ khi bạn gửi `X-User-Id`**.
> Không gửi danh tính thì không có gì chặn, vì backend không biết bạn là ai.

---

## 13b. Đối chiếu ngang hàng với giao diện mẫu

Danh sách này lấy từ chính hàm dựng màn chat của giao diện mẫu
(`renderAgentResult` trong `https://chatbot-demo.tpvtech.vn/ui/js/app.js`).
Đây là **toàn bộ** những gì nó
đọc từ một phản hồi `/api/agent/chat` — không hơn. Dựng đủ 10 mục dưới đây là
giao diện của bạn ngang hàng, không thiếu tính năng nào.

| # | Đọc từ | Hiện thành | Mục |
|---|---|---|---|
| 1 | `answer` | Thân câu trả lời, render Markdown | 3.1 |
| 2 | `routing` + `intent` | Huy hiệu nghiệp vụ, kèm % tin cậy và nguồn định tuyến | 5 |
| 3 | `plan.steps.length` | Huy hiệu `N bước` khi kế hoạch có nhiều hơn một bước | 5 |
| 4 | **`result.validation`** | **Huy hiệu kiểm chứng số liệu + khối liệt kê `issues`** | **9b** |
| 5 | `error` | Huy hiệu `lỗi` **và** một khối đỏ ghi nội dung lỗi | 12 |
| 6 | `plan` + `steps` | Sơ đồ các bước, gom theo đợt chạy song song | 5 |
| 7 | `artifacts` | Danh sách file tải về | 8 |
| 8 | `missing_input` | Chip bấm được, mở ô nhập tham số tương ứng | 10 |
| 9 | `citations` + `refs` | Hai khối nguồn, và `[n]` trong câu trả lời bấm được | 9 |
| 10 | `result`, `trace` | Khối JSON thô gập lại, cho người gỡ lỗi | — |

Mục 4 là mục hay bị bỏ sót nhất. Mục 5 chú ý: `error` khác rỗng **không** làm
mất `answer` — giao diện mẫu hiện cả hai.

### Nếu bạn dựng màn riêng cho từng nghiệp vụ

Phản hồi của các endpoint chuyên biệt (mục 13) có thêm vài trường mà màn chat
không dùng tới. Muốn ngang hàng với các màn riêng của giao diện mẫu thì hiện
thêm:

| Trường | Có ở | Ý nghĩa |
|---|---|---|
| `assumptions[]` | draft, aggregate, presentation | Giả định hệ thống đã phải tự đặt ra. **Nên hiện** — đây là chỗ nói ra điều người đọc không tự đoán được |
| `data_notes[]` | draft | Ghi chú về chất lượng dữ liệu nguồn |
| `sections[]` | draft, aggregate | Từng mục của văn bản, để xem trước khi tải file |
| `reconciliation[]` | aggregate | Đối chiếu số trong báo cáo đơn vị gửi lên với số trong CSDL |
| `template` · `template_name` | draft | Mẫu đã dùng |
| `brief` | presentation | Toàn bộ dữ liệu đã gửi cho bộ dựng slide — đối chiếu ở đây để biết một con số lạ trên slide là do số liệu sai hay do bên dựng viết thêm |
| `engine` | presentation | `presenton` \| `local` (đường lùi hệ thống tự dựng, khô khan hơn) |
| `slide_count` · `edit_url` | presentation | Số slide đếm từ chính file, và link mở ra sửa |
| `elapsed_seconds` | presentation | Thời gian dựng thật |

---

## 14. Checklist nghiệm thu

Chạy hết danh sách này trước khi bàn giao:

- [ ] `GET /whoami` trả `"source":"header"` khi có gửi `X-User-Id` — không
      phải `"default"`, và `tenant_id` là tenant CỦA TÀI KHOẢN đó
- [ ] Gửi `conversation_id` từ lượt thứ hai, và câu *“vẫn kỳ đó”* hiểu đúng
- [ ] `answer` render được **bảng Markdown**
- [ ] `[1]` trong câu trả lời bấm được, hiện `snippet`
- [ ] `artifacts[].download_url` tải được file .docx và .pptx
- [ ] **`result.validation` hiện thành huy hiệu**, và `status: failed` trông
      KHÁC HẲN `status: passed` (mục 9b)
- [ ] Đã đối chiếu đủ 10 mục ở bảng ngang hàng (mục 13b)
- [ ] SSE: xử lý `answer_reset` (thử câu cần nhiều vòng công cụ)
- [ ] SSE: `done` **thay thế** chứ không nối thêm vào chuỗi đã stream
- [ ] SSE: gửi body thiếu `request` vào `/chat/stream` → bắt được `422` JSON,
      không đi parse nó như luồng sự kiện
- [ ] SSE: huỷ giữa chừng bằng `AbortController` không làm vỡ giao diện
- [ ] `missing_input` hiện thành câu hỏi, không phải lỗi
- [ ] Timeout client ≥ 150s; thử một yêu cầu tạo slide đến cùng
- [ ] Bắt được `524` mà không vỡ (thân phản hồi là HTML)
- [ ] `error` khác rỗng nhưng HTTP `200` vẫn hiện được `answer`
- [ ] Không hardcode danh sách mẫu báo cáo, cũng không hardcode `rule_set`
- [ ] **Gửi `X-User-Id` ở MỌI lời gọi** — thiếu là mất sạch phân quyền
- [ ] Bắt được `403` và hiện `detail` thẳng cho người dùng (đã viết sẵn tiếng Việt)
- [ ] Qua `/api/agent/chat`, từ chối quyền về dưới dạng `200` + `answer`, **không phải** `403`
- [ ] Thử một tài khoản không có quyền: mọi đường đều bị chặn, kể cả gọi thẳng endpoint
- [ ] Nếu có gọi thẳng workflow: dùng đúng tên trường (`question` cho
      `/api/chat/qa`, `query` cho `/api/chat/search`, `request` cho phần còn lại)
- [ ] Nếu có gọi thẳng workflow: dùng `download_url` của **chính phản hồi đó**,
      không ghép nhầm sang `/api/agent/download/`

### Câu mẫu để thử từng nghiệp vụ

```
qa            Công văn 105 của Ban Giám đốc yêu cầu các phòng ban làm những gì?
agent         Nhân sự toàn công ty kỳ 2026-08 là bao nhiêu, chia theo phòng ban?
draft         Soạn báo cáo nhân sự tháng 8/2026 cho Phòng Kinh doanh
report        Tổng hợp báo cáo nhân sự và trang thiết bị toàn công ty tháng 8/2026
presentation  Làm slide báo cáo nhân sự và trang thiết bị tháng 8/2026 để họp giao ban
document      Soát giúp tôi tài liệu này          (kèm file_id)
nhiều bước    Tổng hợp nhân sự tháng 8/2026 rồi làm slide
```

---

## 15. Những cái bẫy chúng tôi đã vấp

Ghi lại để các bạn khỏi mất thời gian tìm lại.

**`403 Forbidden` khi gọi bằng script.** Cloudflare chặn một số user-agent tự
động — `Python-urllib/3.10` bị chặn, `curl` thì qua. Gặp `403` mà gọi bằng
trình duyệt vẫn được thì đặt `User-Agent` thường cho script test.

**`EventSource` không dùng được** cho `/chat/stream` vì nó là `POST`. Mất thời
gian nhất là lúc chưa nhận ra điều này.

**Không gửi `file_id` thì nhánh `document` không bao giờ được chọn**, dù câu chữ
rõ đến đâu. Đây là ràng buộc cứng phía backend, không phải model đoán sai.

**Quên `X-User-Id` là mất sạch phân quyền, mà không báo gì.** Backend không có
căn cứ để chặn ai nên cho qua tất — giao diện chạy mượt, và bạn chỉ phát hiện
lúc có người xem được thứ họ không được xem. Kiểm bằng `GET /whoami`.

**403 đến từ hai chỗ khác nhau.** Endpoint chuyên biệt trả `403` thật. Còn
`/api/agent/chat` trả `200` kèm câu từ chối trong `answer` — đừng bắt lỗi theo mã
HTTP ở đường agent.

**Tên trường câu hỏi đổi theo endpoint.** `/api/agent/chat` nhận `request`,
nhưng `/api/chat/qa` nhận `question` và `/api/chat/search` nhận `query`. Gửi sai
thì nhận `422` với `"loc":["body","question"]` — đọc kỹ `loc` là ra ngay, nhưng
rất dễ mất mười phút nếu bạn đang tin rằng cả API dùng chung một tên.

**`plan.source` và `routing.source` có enum khác nhau.** `routing.source` là
`llm | keyword | rule`, còn `plan.source` là `llm | router | rule`. Đừng dùng
chung một hàm ánh xạ cho cả hai.

**`result` đổi hình dạng theo `intent`** — trừ `result.validation`. Các khoá
khác là kết quả thô của workflow, hữu ích lúc gỡ lỗi nhưng không phải hợp đồng
ổn định. `result.validation` thì có mặt ở mọi nghiệp vụ và **bắt buộc phải hiện**
(mục 9).

**Số liệu hiển thị đúng như backend trả về.** Mọi con số đã qua van đối chiếu
với nguồn gốc. **Đừng tự cộng trừ hay tính lại tỷ lệ ở phía giao diện** — các
trường `delta`, `delta_pct`, `share_pct` đã tính sẵn, và một con số do FE tự tính
là một con số không ai truy được về nguồn.

---

## Liên hệ

Sự cố hoặc cần thêm origin vào `CORS_ORIGINS` — báo đội vận hành backend kèm:

- URL đầy đủ và body đã gửi
- Mã HTTP và thân phản hồi nhận được
- `conversation_id` (nếu có) — đủ để truy lại toàn bộ lượt chạy trong log
- Kết quả `GET /whoami` từ chính môi trường đang lỗi

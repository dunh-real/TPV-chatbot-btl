# TPV Chatbot - Backend

Backend đa workflow (LangGraph + FastAPI). Đã hoàn thiện:

- **Workflow 1** — hỏi đáp / tra cứu tài liệu (RAG hybrid 3 nhánh)
- **Workflow 2** — xử lý văn bản tự động (rule engine thể thức + soát chữ nghĩa + định tuyến)
- **Workflow 3** — soạn văn bản theo mẫu (số liệu từ CSDL + mẫu DOCX + kiểm chứng số)
- **Workflow 4** — tổng hợp báo cáo nhiều đơn vị (data tool + biểu đồ + đối chiếu file)
- **Workflow 5** — tạo bộ slide PowerPoint (JSON trung gian + python-pptx)
- **Agent tổng** — một endpoint tự lập kế hoạch rồi chạy một hoặc nhiều workflow

## Agent tổng: một câu yêu cầu → một kế hoạch → nhiều bước

```
POST /api/agent/chat
     │
   plan                 LLM phân rã yêu cầu thành ≤ 3 bước, mỗi bước một nghiệp vụ
     │                  hỏng ở bất kỳ đâu ⇒ lùi về định tuyến một bước như cũ
     │                  không có file đính kèm ⇒ không bước nào là `document`
     ├──────────────┐
  clarify        execute        đợt 1: các bước độc lập, chạy SONG SONG
     │              │           đợt 2: bước phụ thuộc, nhận bối cảnh từ đợt trước
     │              │           bước không ra kết quả ⇒ đổi nguồn, chạy lại 1 lần
     └──────────────┤
                 finalize       ghép câu trả lời, gom MỌI file, ghi lượt hội thoại
                    │
                   END
```

Mỗi bước gọi đúng một nghiệp vụ, và nghiệp vụ nào cũng giữ nguyên đường đi cũ:

| Bước | Nguồn | Sản phẩm |
|---|---|---|
| `qa` | `search_documents` (RAG hybrid 3 nhánh) | câu trả lời + trích dẫn |
| `document` | `analyze_document` (parser + rule engine) | lỗi thể thức + nhiệm vụ |
| `draft` | `get_template` + SQL Server + RAG (căn cứ) | `.docx` |
| `report` | `get_*_statistics` (SQL Server) | `.docx` + biểu đồ |
| `presentation` | `get_*_statistics` (SQL Server) | `.pptx` |
| `agent` | vòng lặp tự chọn công cụ | câu trả lời + nguồn công cụ |

### Ba thứ tầng này phải làm được

**Phân rã.** "Tổng hợp quân số tháng 8 rồi làm slide" là hai sản phẩm, không phải một.
Trước đây `intent_router` chỉ chọn được một nghiệp vụ nên một nửa yêu cầu rơi mất.
[app/agents/planner.py](app/agents/planner.py) trả về danh sách bước kèm `depends_on`;
mọi lỗi của nó — LLM chết, JSON hỏng, ý định lạ, phụ thuộc vòng — đều quy về gọi
`classify_intent` một bước như trước, nên thêm khả năng mà không đánh đổi độ tin cậy.

**Song song.** Hai bước không dùng kết quả của nhau thì không có lý do gì phải nối đuôi.
`Plan.waves()` gom các bước thành từng đợt, `execute_node` chạy mỗi đợt bằng
`asyncio.gather`. Trong vòng lặp công cụ cũng vậy: model phát ba lời gọi trong một lượt
thì ba lời gọi chạy cùng lúc. Chỉ tool **ghi file** là bị tách riêng — chạy song song một
việc ghi với một việc đọc thì không còn nói được thứ tự nào đã xảy ra.

**Thử lại có giới hạn.** Bước không ra kết quả thì đổi **nguồn**, không phải hỏi lại cùng
một nguồn to hơn: `qa` rỗng → hỏi CSDL nghiệp vụ; `agent` rỗng → tìm trong báo cáo các
đơn vị đã nộp; báo cáo lỗi vì kỳ đó không có dữ liệu → tra xem thật sự có gì. Đúng **một**
lần thử lại (`MAX_STEP_ATTEMPTS = 2`): lần thứ ba cùng câu hỏi trên cùng nguồn không đổi
được gì ngoài việc bắt người dùng chờ lâu hơn. Thiếu đầu vào bắt buộc thì **không** thử
lại — chạy lại bằng nghiệp vụ nào cũng vẫn thiếu, việc phải làm là hỏi người dùng.

Nhánh `clarify` tồn tại vì đoán bừa đắt hơn hỏi lại: soạn nhầm loại báo cáo thì người
dùng phải đọc hết mới phát hiện, còn một câu hỏi lại chỉ mất năm giây.

`AgentResponse` trả thêm `plan` (các bước đã lập) và `steps` (mỗi bước đã chạy ra sao,
có phải thử lại không); `intent` và `routing` giữ nguyên hình dạng cũ, trỏ vào bước cuối.

### Xem agent làm việc theo thời gian thực

`POST /api/agent/chat/stream` phát tiến trình bằng SSE thay vì bắt chờ một cục:

```
event: thinking   → "Đang đọc yêu cầu và tách thành các bước…"
event: plan       → [{s1 report []}, {s2 presentation [s1]}]
event: step_start → {id: s1, label: "Tổng hợp báo cáo"}
event: step_done  → {id: s1, attempts: 1, empty: false}
event: step_progress → {round: 2, max_rounds: 8, tools: ["search_documents"]}
event: step_retry → {id: s2, from: "qa", to: "agent"}     (chỉ khi phải đổi nguồn)
event: answer_delta → {text: "Dựa trên báo cáo kiểm kê…"}   (chữ, ngay lúc model sinh)
event: answer_reset → {}                                   (chữ vừa phát là câu dẫn)
event: done       → nguyên vẹn AgentResponse của POST /chat
```

Ba lớp phản hồi, vì ba khoảng lặng có độ dài khác nhau:

| Lớp | Lấp khoảng lặng nào |
|---|---|
| `plan` + `step_start`/`step_done` | giữa các bước nghiệp vụ (vài giây tới vài chục giây) |
| `step_progress` | bên trong một vòng lặp công cụ - mỗi lượt LLM là 5-15 giây im lặng |
| `answer_delta` | lượt cuối, lúc model viết câu trả lời - trước đây cả đoạn về một cục |

`step_progress` mang **tên** công cụ chứ không mang tham số hay kết quả: tên là nhãn
việc ("đang tra: trang thiết bị"), không phải dữ liệu. Có test quét toàn bộ payload để
chặn giá trị số và tham số lọt ra.

`answer_delta` chảy được là nhờ `LLMClient.stream_chat_with_tools` - vòng lặp agent gọi
LLM ở chế độ stream, gom mảnh `tool_calls` rời rạc theo `index` rồi trả về đúng một
`AssistantTurn` như bản cũ, nên phần còn lại của vòng lặp không biết mình đang chạy ở
chế độ nào. Model đôi khi viết vài câu dẫn rồi mới quyết định gọi tool; chữ đó đã lên
màn hình nhưng không phải câu trả lời, nên vòng lặp phát `answer_reset` để giao diện
dọn đi.

Payload của `done` **chính là** `AgentResponse`, nên giao diện dùng đúng một hàm dựng
kết quả cho cả hai đường - nó không cần biết mình đang xem streaming hay không.

Nối dây bằng `ContextVar` ([app/agents/progress.py](app/agents/progress.py)): node nào
muốn báo thì gọi `progress.emit`, không phải nhận thêm tham số và không cần biết ai
đang nghe. Không ai nghe thì `emit` là lệnh rỗng - `POST /api/agent/chat` không đổi một
dòng nào và không trả giá gì. ContextVar đi theo task con khi `asyncio.gather` tạo task,
nên các bước chạy song song vẫn phát về đúng một hàng đợi.

**Không sự kiện nào mang kết quả công cụ.** Số liệu thô chưa qua van `check_numbers`;
đẩy nó lên màn hình là mời người đọc tin vào con số mà chính hệ thống chưa xác nhận.
Tiến trình chỉ nói agent đang làm *gì*, còn nói số liệu *bao nhiêu* là việc của câu trả
lời cuối. Hàng đợi có trần và **bỏ sự kiện** khi đầy chứ không chặn: giao diện đọc chậm
không được làm agent đứng lại.

### Tầng tool — [app/tools/](app/tools/)

Tất cả những gì hệ thống làm được, gom vào một danh mục duy nhất
([app/tools/registry.py](app/tools/registry.py)). Giá trị của nó không phải là gọi hộ
hàm, mà là ba ràng buộc đặt đúng một lần cho mọi tool: **tên tool phải có thật**,
**tham số phải hợp lệ** (sai tên là chặn, không "đoán ý"), và **kết quả là dữ liệu
thuần** nên mọi con số vào prompt đều đối chiếu ngược lại được.

| Tool | Việc | File |
|---|---|---|
| `search_documents(query, document_type)` | tìm đoạn liên quan, kèm nguồn trích dẫn | [tools/rag.py](app/tools/rag.py) |
| `get_document(document_id)` | đọc trọn một văn bản, không bỏ sót đoạn | [tools/rag.py](app/tools/rag.py) |
| `analyze_document(file_id)` | soát thể thức + chữ nghĩa + phân rã nhiệm vụ | [tools/document.py](app/tools/document.py) |
| `get_template(template_type)` | tra mẫu, kèm `required_inputs` phải hỏi người dùng | [tools/templates.py](app/tools/templates.py) |
| `get_personnel_statistics(start_date, end_date, unit)` | quân số theo kỳ, đã tính sẵn delta/tỷ lệ | [tools/data.py](app/tools/data.py) |
| `get_equipment_statistics(start_date, end_date, unit)` | trang thiết bị theo kỳ | [tools/data.py](app/tools/data.py) |
| `get_reporting_status(...)` | đơn vị nào đã gửi / chưa gửi báo cáo | [tools/data.py](app/tools/data.py) |
| `generate_docx(template_id, content)` | đổ nội dung đã chốt ra .docx | [tools/document.py](app/tools/document.py) |
| `generate_presentation(data, template_id)` | dựng .pptx từ đặc tả JSON | [tools/presentation.py](app/tools/presentation.py) |

Hai tool sinh file cố tình "ngu": chúng dựng đúng những gì được đưa, không tự thêm số
liệu, không tự suy ra người ký. Biểu đồ không đi lọt qua JSON nên luôn là PNG do code
vẽ, truyền riêng và tham chiếu bằng `chart_key`.

Tool số liệu nhận cả `start_date`/`end_date`/`unit` lẫn `ky`/`compare_to`/`ma_don_vi`:
người dùng nói "từ tháng 6 đến tháng 9", còn kiểm kê chốt theo tháng nên một khoảng quy
về đúng hai mốc — kỳ báo cáo và kỳ đối chiếu.

Định danh file đi qua [app/services/storage.py](app/services/storage.py) chứ không phải
đường dẫn trần: đó là chỗ duy nhất chặn `../../etc/passwd`.

## Ingest: file → Markdown → chunk

```
file
 ├─ .pdf   phân loại TỪNG trang (số ký tự / font nhúng / ngưỡng thích ứng, vote 2/3)
 │         ├─ trang digital → pymupdf4llm  (bảng ra bảng Markdown)
 │         └─ trang scan    → OCR bằng VLM trên vLLM (tuỳ chọn, OCR_ENABLED)
 ├─ ảnh                     → OCR
 ├─ .docx .xlsx .pptx .csv .json → MarkItDown
 └─ .txt .md                → đọc thẳng
      └─> cleanup: bỏ số trang, đường kẻ, gỡ ** vỡ của tiếng Việt, chuẩn hoá heading
          └─> chunk theo cấu trúc: heading > khối bảng > đoạn > câu
```

Mọi định dạng đều quy về Markdown vì đó là dạng giữ được **bảng biểu**. Bảng dài hơn
`CHUNK_MAX_TOKENS` được cắt theo dòng nhưng **lặp lại dòng tiêu đề** ở mỗi phần —
không có nó thì "3.000.000" ở chunk sau không còn biết thuộc cột nào.

Bốn ngưỡng token điều khiển việc cắt: `MIN=200` (mảnh vụn thì gộp vào chunk kề, nhưng
không gộp xuyên chương), `IDEAL=700` (kích thước nhắm tới), `MAX=1200` (phải cắt tiếp),
`HARD_CAP=1500` (trần tuyệt đối).

### Thẻ thông tin văn bản

Văn bản hành chính được nạp thêm **một chunk riêng** gom phần thể thức:

```
Cong_van_chi_thi_kiem_ke. Thông tin thể thức văn bản:
- Số, ký hiệu văn bản: 18/CV-BP
- Địa danh và ngày ban hành: Hà Nội, ngày 18 tháng 9 năm 2026
- Trích yếu nội dung: Kiểm kê và tổng hợp thông tin về nhân sự và trang thiết bị
- Kính gửi: Các phòng ban trực thuộc Công ty ...
- Nơi nhận: Như Kính gửi (để thực hiện); Ban Tổng Giám đốc (để chỉ đạo); Lưu: VT, HC-NS
- Người ký: TỔNG GIÁM ĐỐC Nguyễn Văn Phúc
```

Lý do: reranker chấm **cả chunk**. Ai ký, số mấy, ngày nào là *siêu dữ liệu* của
văn bản, nằm rải ở đầu và cuối một văn bản dài nói về chuyện khác - chunk chứa
chúng không *về* chúng, nên điểm luôn thấp. Thẻ ngắn thì về đúng những câu hỏi ấy:

| Câu hỏi | Chunk cũ | Có thẻ |
|---|---|---|
| Ai ký công văn 18/CV-BP? | 0.026 → bị loại | **0.863** |
| Nguyễn Văn Phúc | 0.018 → bị loại | **0.239** |
| Công văn 18/CV-BP ban hành ngày nào? | — | **0.987** |

Quốc hiệu và tiêu ngữ cố tình bỏ ra ngoài thẻ: văn bản nào cũng có và giống hệt
nhau, đưa vào chỉ tạo một chunk trùng lặp trong mọi tài liệu. Tệp không đủ dấu
hiệu văn bản hành chính (dưới 2 thành phần) thì không có thẻ.

Tắt bằng `DOC_CARD_ENABLED=false`. **Đổi giá trị này thì phải nạp lại kho** -
thẻ sinh ra lúc ingest, không phải lúc truy vấn.

## Kiến trúc workflow 1

```
câu hỏi
  └─> rewrite_query      LLM: giải đại từ theo lịch sử + sinh 3 biến thể truy vấn
      └─> retrieve       mỗi biến thể chạy song song 3 nhánh trên Qdrant:
          ├─ dense       vector 1024-d (Vietnamese_Embedding, cosine)   - hiểu ngữ nghĩa
          ├─ lexical     sparse, trọng số token học được (BGE-M3)       - từ khoá có ngữ cảnh
          └─ bm25        sparse TF chuẩn hoá, IDF do Qdrant tính        - khớp từ/mã hiệu
          └─> RRF        gộp thứ hạng: score = Σ weight / (k + rank)
              └─> rerank cross-encoder Vietnamese_Reranker chấm lại top-30
                  └─> build_context  đánh số [1][2] + metadata nguồn
                      └─> generate   LLM trả lời, bắt buộc trích dẫn
```

Cả 3 biểu diễn của cùng một chunk nằm chung một point trong Qdrant, nên RRF chỉ việc
gộp theo point id. Không chunk nào vượt ngưỡng reranker → đi nhánh `no_context`.

### Van cứu từ khoá — vì reranker chấm cả chunk

Reranker cho điểm cho *cả đoạn*, không cho từng câu. Một công văn 900 token nói
về kiểm kê, dòng cuối ghi `| TỔNG GIÁM ĐỐC Nguyễn Văn Phúc |`, khi hỏi *"Ai là
Tổng giám đốc?"* chỉ được **0.07** — dưới ngưỡng 0.1 nên bị loại sạch, dù nhánh
BM25 đã xếp nó hạng 1. Hệ thống trả lời "không có trong tài liệu" về một thứ có
thật trong tài liệu, và người dùng không có cách nào biết mình vừa bị nói dối.

Nên trước khi trả về rỗng, [retrieval.py](app/rag/retrieval.py) hỏi thêm một câu:
*có chunk nào chứa nguyên văn mọi từ khoá của câu hỏi không?* Có thì giữ lại, cắm
cờ `matched_by="keyword"` để trace và giao diện nói rõ chunk này vào bằng đường
nào, kèm điểm rerank thật chứ không làm tròn thành 0.

Hai ràng buộc giữ cho van không thành cửa mở toang:

- **Đủ mọi từ, không phải một vài từ.** Khớp một phần chỉ là "đoạn gần giống" -
  đúng thứ mà ngưỡng sinh ra để chặn.
- **Ít nhất hai từ mang nghĩa** (đã bỏ *ai, là, gì, của, có...*). Một từ chung
  như "báo cáo" mà cũng cứu thì chunk nào cũng lọt.

Van chỉ lấp **chỗ còn trống** trong `RERANK_TOP_N`, không đẩy chunk đã qua ngưỡng
ra. Câu hỏi về thứ không có trong kho vẫn trả về rỗng như cũ — thử `"Thuế suất
thuế giá trị gia tăng là bao nhiêu?"` vẫn nhận "chưa tìm thấy".

Tắt bằng `LEXICAL_RESCUE_ENABLED=false`.

Nhánh đó **không** trả về một câu cứng: phần lớn lượt rơi vào đây là chào hỏi hoặc
hỏi hệ thống làm được gì, đáp lại bằng "không tìm thấy trong tài liệu" thì người
dùng tưởng máy hỏng. Model được đối đáp bình thường nhưng bị chặn đúng một việc —
không nêu điều khoản, con số, thời hạn hay tên văn bản nào khi trong tay không có
nguồn, kể cả khi nó "biết" (hỏi thuế suất VAT vẫn phải nhận là chưa có trong kho).
Truy hồi *hỏng* thì khác truy hồi *không ra gì*: Qdrant chết là nói thẳng sự cố,
không tán gẫu đè lên.

| Thành phần | Lựa chọn | File |
|---|---|---|
| Đọc file | pymupdf4llm (PDF) + MarkItDown (Office) + OCR VLM cho trang scan | [app/rag/converter.py](app/rag/converter.py), [app/rag/ocr.py](app/rag/ocr.py) |
| Chunking | heading/bảng/đoạn/câu, ngưỡng 200/700/1200/1500, smart merge | [app/rag/chunking.py](app/rag/chunking.py) |
| Embedding | `AITeamVN/Vietnamese_Embedding` + `sparse_linear.pt` của `BAAI/bge-m3` (1 forward → dense + lexical) | [app/rag/embedding.py](app/rag/embedding.py) |
| Keyword | BM25 tự cài, tokenizer tiếng Việt có bigram, IDF phía Qdrant | [app/rag/embedding.py](app/rag/embedding.py) |
| Vector DB | Qdrant, 1 dense + 2 sparse named vectors | [app/rag/vectorstore.py](app/rag/vectorstore.py) |
| Fusion | RRF phía client (giữ được hạng từng nhánh để debug) | [app/rag/retrieval.py](app/rag/retrieval.py) |
| Rerank | `AITeamVN/Vietnamese_Reranker` (cross-encoder, sigmoid → [0,1]) | [app/rag/reranker.py](app/rag/reranker.py) |
| LLM | vLLM qua API OpenAI-compatible | [app/services/llm.py](app/services/llm.py) |
| Orchestration | LangGraph | [app/agents/graph.py](app/agents/graph.py) |

## Chạy

```bash
cp .env.example .env          # sửa LLM_MODEL, QDRANT_URL nếu cần
uv sync

# 1. Qdrant
docker run -d -p 6333:6333 -p 6334:6334 -v $PWD/qdrant_storage:/qdrant/storage qdrant/qdrant

# 2. vLLM — Qwen3.6-35B-A3B-FP8 trên 1 card (xem chú thích trong script)
./scripts/serve_vllm.sh

# 3. Backend
uv run uvicorn app.main:app --reload --port 8080
```

Giao diện demo nằm ở [../frontend/](../frontend/) và được backend phục vụ sẵn:
mở **http://localhost:8080/ui/** (vào `/` cũng tự chuyển sang). HTML/CSS/JS
thuần, không build, cùng origin nên không phải mở CORS.

### Mở ra Internet để demo từ xa

```bash
./scripts/serve_public.sh     # Cloudflare quick tunnel, in ra URL https://...trycloudflare.com
```

Không cần tài khoản lẫn domain, nhưng **URL đổi mỗi lần chạy lại** và không có
cam kết uptime. Đã kiểm: SSE của `/api/chat/qa/stream` không bị Cloudflare gom
bộ đệm, upload multipart và tải `.docx`/`.pptx` đều qua được.

Giới hạn cần nhớ: Cloudflare cắt request quá **100 giây** (lỗi 524). Các
workflow hiện chạy 3-10 giây nên còn xa ngưỡng, nhưng nếu đổi sang model chậm
hơn thì phải chuyển các endpoint sinh file sang dạng chạy nền + hỏi trạng thái.

URL public ai biết cũng gọi được, kể cả `DELETE /api/documents/{doc_id}`. Chốt
cửa bằng một dòng trong `.env`:

```
PUBLIC_ACCESS_TOKEN=chuoi-bi-mat-cua-ban
```

Khi đó mở `https://<url>/ui/?token=chuoi-bi-mat-cua-ban` một lần (cookie giữ 24
giờ), hoặc gọi API kèm header `X-Access-Token`. Để rỗng = không chặn ai, hợp
với chạy trong mạng nội bộ.

Có domain cố định rồi thì chuyển sang **named tunnel** (vẫn miễn phí, URL không
đổi, bật được Cloudflare Access) - các lệnh ghi sẵn ở cuối
[scripts/serve_public.sh](scripts/serve_public.sh).

Model dùng cho cả sinh văn bản lẫn OCR (nó có sẵn vision encoder), nên chỉ cần một
server vLLM trên một card. Card còn lại dành cho embedding + reranker của workflow 1
(`EMBEDDING_DEVICE=cuda:1`).

Kiểm tra: `curl localhost:8080/health` → `status: ok` khi cả Qdrant và vLLM đều sống.
Backend vẫn khởi động được khi chúng chết, chỉ trả lời ở chế độ degraded.

## Nạp tài liệu

```bash
uv run python scripts/ingest.py data/corpus            # cả thư mục
uv run python scripts/ingest.py --recreate data/corpus # tạo lại collection
```

Hỗ trợ `.pdf .docx .xlsx .pptx .csv .json .txt .md` và ảnh (`.jpg .png .tiff`…).
PDF scan cần bật OCR — dựng thêm một vLLM phục vụ VLM rồi đặt `OCR_ENABLED=true`:

```bash
vllm serve Qwen/Qwen3-VL-8B-Instruct --port 8001 --gpu-memory-utilization 0.4
```

Khi OCR tắt, PDF scan vẫn ingest được nhưng những trang ảnh sẽ rỗng (log cảnh báo rõ).

Hoặc qua API: `POST /api/documents/upload` (multipart), `POST /api/documents/ingest-text`.
Ingest lại cùng `doc_id` sẽ ghi đè sạch bản cũ.

## API chính

| Endpoint | Mô tả |
|---|---|
| `POST /api/agent/chat` | **Agent tổng** — tự chọn workflow, trả `answer` + `artifacts` |
| `POST /api/agent/upload` | Tải file lên, trả `file_id` để đưa vào `/api/agent/chat` |
| `GET /api/agent/tools` | Danh mục tool — những việc agent làm được |
| `GET /api/agent/download/{file}` | Tải file agent vừa tạo |
| `POST /api/chat/qa` | Hỏi đáp, trả `answer` + `citations` |
| `POST /api/chat/qa/stream` | Như trên nhưng SSE: `meta` (trích dẫn) → `delta`* → `done` |
| `POST /api/chat/search` | Chỉ truy hồi — xem hạng từng nhánh, điểm RRF, điểm rerank |
| `GET/DELETE /api/chat/history/{id}` | Lịch sử hội thoại (Redis) |
| `GET /api/documents/stats` | Số point trong collection |

```bash
# Agent tự định tuyến — không cần nói mình muốn workflow nào
curl -X POST localhost:8080/api/agent/chat -H 'Content-Type: application/json' -d '{
  "request": "tổng hợp tình hình quân số toàn cơ quan tháng 8/2026"
}'

# Soát một văn bản: tải lên lấy file_id rồi đưa vào cùng câu yêu cầu
FILE_ID=$(curl -sX POST localhost:8080/api/agent/upload -F file=@ban_thao.docx | jq -r .file_id)
curl -X POST localhost:8080/api/agent/chat -H 'Content-Type: application/json' -d "{
  \"request\": \"kiểm tra thể thức văn bản này\", \"file_id\": \"$FILE_ID\"
}"

# Gọi thẳng một workflow khi client đã biết mình cần gì
curl -X POST localhost:8080/api/chat/qa -H 'Content-Type: application/json' -d '{
  "question": "Hoá đơn điện tử có bắt buộc chữ ký số không?",
  "include_trace": true
}'
```

`include_trace: true` trả kèm số hit mỗi nhánh, thời gian từng bước và điểm rerank —
dùng để chỉnh tham số khi chất lượng chưa như ý.

## Workflow 2 — Xử lý văn bản tự động

```
DOCX / PDF / MD
   └─> parse (CÓ định dạng: font, cỡ, lề)   app/documents/parser.py
       └─> dò thành phần thể thức           app/documents/structure.py
           ├─> rule_check   tất định, KHÔNG LLM     app/documents/rules.py + config/rules/nd30.yaml
           ├─> llm_review   soát chữ nghĩa theo lô, mọi lỗi phải trích dẫn được nguyên văn
           ├─> classify     định tuyến, chỉ chọn trong danh mục phòng ban lấy từ CSDL
           └─> tasks        tóm tắt + phân rã nhiệm vụ cho từng phòng
               └─> assemble -> JSON có địa chỉ lỗi (block_id)
```

Nguyên tắc phân việc: **cái gì đo được thì không hỏi LLM**. Font sai, cỡ chữ sai,
thiếu "Nơi nhận" đều do rule engine phát hiện từ chính file — LLM chỉ nhận text nên
không thể biết những điều đó, hỏi nó là mời nó bịa.

Ba van chặn ảo giác ở nhánh LLM:

| Van | Cách làm |
|---|---|
| Lỗi chữ nghĩa | mỗi phát hiện phải kèm `quote` trích nguyên văn; không khớp chuỗi thật trong đoạn thì bị loại |
| Mã phòng ban | chỉ nhận mã có trong danh mục CSDL; mã lạ bị bỏ, không có danh mục thì không đề xuất |
| Văn bản scan | `rule_check.status = "partial"` kèm lý do, **không bao giờ** báo "đạt" |

Ngưỡng context: mỗi lô soát ~2400 ký tự chạy song song, phân loại chỉ đọc ~2500 ký
tự đầu (văn bản hành chính đặt hết thông tin phân loại ở đầu). Không đưa cả file
kèm câu hỏi chung chung.

```bash
curl -X POST localhost:8080/api/documents/review -F "file=@cong_van.docx"
```

### Không phải văn bản nào cũng là công văn

Trước khi soát, hệ thống hỏi hai câu — cả hai đều trả lời được từ chính văn bản,
không cần LLM:

**1. Tệp này có phải văn bản hành chính không?** Không có lấy một dấu hiệu nào
(quốc hiệu, tiêu ngữ, số ký hiệu, tên loại) thì `rule_check.status = "skipped"`
kèm lý do, chứ không dội ra vài chục lỗi. Đem thước đo công văn soi một bản đặc
tả kỹ thuật thì ra 74 lỗi đúng về máy móc mà vô nghĩa — tệ hơn là lỗi chữ nghĩa
thật chìm nghỉm trong đống đó. Vẫn muốn soát thì `force_rules=true`.

**2. Là loại gì?** Tên loại nằm ngay dòng đầu; riêng công văn nhận ra bằng chỗ
TRỐNG ở dòng đó — theo NĐ 30 nó là loại duy nhất không ghi tên loại. Mỗi loại có
danh sách thành phần bắt buộc riêng:

| Loại | Khác biệt |
|---|---|
| Công văn | không có tên loại → không đòi; bắt buộc có "Kính gửi" |
| Quyết định, báo cáo, thông báo… | có tên loại, trích yếu nằm ngay dưới dạng "Về …" chứ không phải "V/v" |
| Biên bản | thường không vào sổ → không đòi số ký hiệu, nơi nhận |
| Tờ trình, giấy mời | bắt buộc có "Kính gửi" |

Đòi đủ mọi thành phần cho mọi loại thì loại nào cũng bị báo sai vài chỗ, và người
dùng học được đúng một điều: bỏ qua cảnh báo của máy.

### Tiêu chí nằm ở đâu

[config/rules/nd30.yaml](config/rules/nd30.yaml) — lấy Nghị định 30/2020/NĐ-CP làm
nền. Trong đó: ngưỡng phông/cỡ chữ/lề/khổ giấy, danh sách thành phần bắt buộc,
phần riêng của từng loại, **danh sách chức danh người ký** (doanh nghiệp ký "TỔNG
GIÁM ĐỐC", đơn vị quân đội ký "CHỈ HUY TRƯỞNG" — liệt kê ở đây chứ không chôn
trong regex), và cả câu chữ thông báo lỗi. Xoá hẳn một khối là tắt luôn tiêu chí đó.

Thả thêm một file `.yaml` vào `config/rules/` là có thêm một bộ tiêu chí, không
sửa code và không khởi động lại: `GET /api/documents/rule-sets` liệt kê, còn
`POST /api/documents/review -F rule_set=<tên file>` chọn bộ để áp.

**Các con số trong đó cần đối chiếu lại với văn bản gốc** trước khi dùng chính
thức — phần riêng theo loại văn bản cũng vậy.

Còn *cứng* thật là cách **nhận diện** thành phần: regex trong
[app/documents/structure.py](app/documents/structure.py). Thêm một loại văn bản
ngoài danh sách, hay một dạng số ký hiệu khác, là phải sửa code.

## Workflow 3 — Soạn văn bản theo mẫu

```
"Soạn báo cáo tình hình trang bị tháng 8"
  └─> trích tham số (kỳ, đơn vị)      thiếu đơn vị -> HỎI LẠI, không đoán
      └─> chọn mẫu theo mô tả
          ├─> lấy số liệu: SQL theo đúng khai báo của mẫu (kỳ 2026-08)
          └─> tra quy định: 2-3 đoạn, chỉ để lấy căn cứ
              └─> dựng từng mục:  data/table = CODE điền · llm = viết nhận xét
                  └─> đối chiếu số: mọi con số phải truy về được CSDL
                      ├─ sai -> viết lại (tối đa 2 lần) -> vẫn sai thì KHÔNG xuất file
                      └─ đạt -> xuất DOCX -> ghi sổ văn bản
```

**Số liệu do SQL lấy, LLM chỉ viết văn quanh số liệu đó.** Mục `data`/`table` hoàn
toàn do code dựng, không gọi model lần nào. Chỉ mục `llm` (nhận xét, đề xuất) mới
qua model, và [verify.py](app/documents/verify.py) đối chiếu từng con số nó viết ra
với dữ liệu gốc — bịa "70 người" hay "15.000.000 đồng" là bị chặn, trong khi "quá hạn
12 tháng" hay "nêu tại mục 2" vẫn được cho qua.

File sinh ra phải **qua được rule engine của workflow 2** — hiện đạt 12/12 tiêu chí
thể thức. Ưu tiên điền vào [mẫu .docx](data/templates/) đã đúng thể thức; mẫu nào
chưa có file thì dựng bằng code với style NĐ 30.

```bash
uv run python scripts/make_template_docx.py    # tạo file mẫu
curl -X POST localhost:8080/api/reports/draft -H 'Content-Type: application/json' -d '{
  "request": "Soạn báo cáo tình hình trang bị tháng 8",
  "ma_don_vi": "DV02",
  "inputs": {"nguoi_ky": "Trần Văn B", "chuc_vu_ky": "TRƯỞNG ĐƠN VỊ"}
}'
```

## Workflow 4 — Tổng hợp báo cáo

```
"Tổng hợp báo cáo quân số và trang thiết bị tháng 8"
  └─> trích tham số (kỳ, phạm vi đơn vị, nội dung)
      └─> data tool: SQL gộp nhiều đơn vị, tính sẵn delta / % / tỷ trọng
          ├─> đối chiếu file báo cáo đơn vị đã gửi với số kiểm kê
          └─> vẽ biểu đồ bằng code (matplotlib -> PNG)
              └─> LLM viết nhận xét (chỉ được dùng số đã cho)
                  └─> đối chiếu số -> DOCX kèm biểu đồ -> ghi sổ
```

**Không dùng RAG để lấy số liệu.** Chunking cắt bảng làm số rời khỏi nhãn cột,
retrieval trả về thứ *gần giống* chứ không phải thứ *đúng*, và không ai kiểm chứng
được kết quả. [tools/data.py](app/tools/data.py) là tầng duy nhất chạm vào số liệu.

### Hai nguồn số liệu: CSDL hoặc chính báo cáo đơn vị gửi lên

Nhiều nơi chưa có CSDL kiểm kê - thứ duy nhất tồn tại là tập báo cáo các đơn vị
gửi lên, và số nằm trong **bảng** của những báo cáo đó. `inputs.nguon_so_lieu`
chọn nguồn:

| | `csdl` (mặc định) | `tai_lieu` |
|---|---|---|
| Số lấy từ | bảng `kiem_ke_trang_bi` | bảng kiểm kê trong file báo cáo |
| So sánh kỳ trước | có (`delta`, `delta_pct`) | **không** - một báo cáo chỉ là ảnh chụp một thời điểm |
| File đơn vị gửi dùng để | đối chiếu, phát hiện lệch | chính là nguồn |

```bash
curl -X POST localhost:8080/api/reports/aggregate -H 'Content-Type: application/json' -d '{
  "request": "Tổng hợp báo cáo trang thiết bị tháng 8",
  "inputs": {"nguon_so_lieu": "tai_lieu", "files": "upload:BC_Phong_Ky_Thuat.docx"}
}'
```

File được lấy từ sổ văn bản (báo cáo đã vào sổ trong kỳ) cộng với các `file_id`
truyền thêm ở `inputs.files`.

Bảng đọc bằng code, **không hỏi LLM**: [extract_figures.py](app/documents/extract_figures.py)
dò hàng tiêu đề theo tên cột (`Tổng SL`, `Hoạt động tốt`, `Cần bảo dưỡng`, `Đề xuất
thanh lý`...), đọc từng dòng, và đọc riêng dòng `Tổng cộng`. Mỗi con số trong báo
cáo tổng hợp đều chỉ ngược lại được về *file nào, dòng nào*, và mục **NGUỒN SỐ
LIỆU** trong file xuất ra liệt kê đúng những file đã đọc.

Ba thứ được nói thẳng thay vì làm mượt:

- **Dòng "Tổng cộng" khác tổng các dòng** → ghi ra cả hai con số; đó là lỗi của
  chính báo cáo đơn vị.
- **Bảng thiếu cột** (chỉ có số lượng, không có tình trạng) → chỉ tiêu đó không
  có trong kết quả, không điền 0.
- **File không đọc được** → liệt kê tên file kèm lý do, không im lặng bỏ qua.

Cột `Bảo dưỡng gần nhất` là cột **ngày**, không phải cột đếm - nhận nhầm nó
thành "số thiết bị cần bảo dưỡng" thì mọi con số hỏng theo, nên tên cột phải
khớp cụm đầy đủ (`cần bảo dưỡng`), và cột nào không đọc ra số nào thì bị bỏ hẳn.

Rủi ro lớn nhất không nằm ở bước diễn đạt mà ở bước **chọn tool và tham số**: gọi
nhầm `ky="2026-07"` thì mọi con số đều có thật và báo cáo vẫn sai. Ba lớp chặn:
tool nằm trong whitelist có schema chặt (`ky` phải đúng `YYYY-MM`, `ma_don_vi` phải
có trong danh mục), thiếu tham số thì hỏi lại, và phạm vi truy vấn được ghi thẳng
vào báo cáo (*"3 đơn vị, 1 đã gửi báo cáo"*).

Data tool tính sẵn **mọi con số sẽ xuất hiện trong câu văn** — `value`, `prev`,
`delta`, `delta_pct`, `share_pct` — để LLM không có lý do phải tính. Quy tắc: nếu
model phải làm một phép tính, dù chỉ là `60/1240`, thì data tool còn thiếu.

Trước khi số liệu tới tay LLM, code kiểm ràng buộc nghiệp vụ (`có mặt + vắng =
quân số`). Lệch thì báo ra thành một mục riêng trong báo cáo, không để model làm mượt.

```bash
curl -X POST localhost:8080/api/reports/aggregate -H 'Content-Type: application/json' -d '{
  "request": "Tổng hợp báo cáo quân số và trang thiết bị tháng 8",
  "inputs": {"nguoi_ky": "Lê Văn C"}
}'
```

## Workflow 5 — Tạo PowerPoint

```
"Tạo slide báo cáo quân số tháng 8"
  └─> lấy số liệu + vẽ biểu đồ   (tái dùng nguyên của workflow 4)
      ├─> dàn ý (LLM)   -> JSON trung gian: slide nào, kiểu gì
      │     └─> LỌC: kiểu slide phải trong whitelist, chart/table phải có dữ liệu thật
      └─> nội dung từng slide (LLM) -> bullets ≤ 15 từ, chỉ dùng số đã cho
          └─> đối chiếu số -> render .pptx bằng code
```

**LLM không chạm vào python-pptx.** Nó chỉ sinh JSON trung gian; việc chọn layout,
đặt shape, chèn ảnh, dựng bảng đều do [pptx_builder.py](app/documents/pptx_builder.py)
làm. Nhờ vậy dàn ý kiểm tra được trước khi dựng file, và bố cục test được.

Năm kiểu slide cố định: `title`, `summary` (ô số lớn), `bullet`, `chart`, `table`.
Dàn ý do LLM sinh bị lọc qua whitelist — slide kiểu lạ hoặc `chart_key` không có dữ
liệu tương ứng đều bị bỏ. LLM hỏng thì có dàn ý mặc định, vẫn ra được bộ slide.

Khác workflow 3-4 một điểm: số bịa trên slide **không chặn cả file** mà chỉ bỏ đúng
gạch đầu dòng đó (`removed_bullets`). Slide là tài liệu nội bộ để trình bày; chặn hẳn
thì người dùng không còn gì để sửa, trong khi văn bản hành chính thì phải chặn.

Ô chỉ tiêu ở slide `summary` do code dựng thẳng từ số liệu, nên LLM hỏng thì slide
vẫn còn số, chỉ mất phần chữ.

```bash
curl -X POST localhost:8080/api/presentations/create -H 'Content-Type: application/json' -d '{
  "request": "Tạo slide báo cáo quân số tháng 8"
}'
```

## CSDL nghiệp vụ

Bốn bảng phục vụ workflow 2+ (định tuyến văn bản, sinh báo cáo từ số liệu thật):

| Bảng | Vai trò |
|---|---|
| `phong_ban` | danh mục phòng ban; cột `mo_ta` là căn cứ để LLM đề xuất đơn vị xử lý |
| `template_bao_cao` | mẫu báo cáo; `mo_ta` để chọn mẫu, `truong_du_lieu` (JSON) để điền |
| `don_vi` / `trang_bi` | quân số và trang thiết bị hiện trạng — tách hai bảng để quân số không lặp theo từng dòng trang bị |
| `kiem_ke` / `kiem_ke_trang_bi` | số liệu **theo kỳ** (`"2026-08"`) — không có nó thì "báo cáo tháng 8" và "tháng 9" ra cùng một con số |
| `van_ban` | sổ văn bản, `doc_id` nối sang Qdrant của workflow 1 |

```bash
uv run python scripts/seed_demo.py --reset        # tạo bảng + dữ liệu mẫu (SQLite)
uv run python scripts/gen_schema.py -o scripts/schema_sqlserver.sql   # DDL cho SQL Server
```

Chuyển sang SQL Server chỉ cần đổi `DATABASE_URL` trong `.env`, không sửa code:

```
DATABASE_URL=mssql+aioodbc://sa:MatKhau@localhost:1433/tpv?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes
```

**Nguyên tắc bắt buộc khi sinh báo cáo:** mọi số liệu đi qua [repository.py](app/db/repository.py),
LLM chỉ nhận `dict` đã truy vấn sẵn và diễn đạt thành văn. Không bao giờ để LLM tự
sinh con số — nó sẽ ra những giá trị trông hợp lý và hoàn toàn bịa.

## Tinh chỉnh

Tất cả qua `.env`, không cần sửa code:

- Truy hồi thiếu → tăng `RETRIEVAL_BRANCH_LIMIT`, `RRF_TOP_K`.
- Chunk quá dài/ngắn → chỉnh `CHUNK_IDEAL_TOKENS`; nhớ `RERANKER_MAX_LENGTH` phải đủ
  chứa cả câu hỏi lẫn chunk (mặc định 2048 cho chunk 1200 token).
- Nhiễu nhiều → tăng `RERANK_SCORE_THRESHOLD` (0.1 → 0.3), giảm `RERANK_TOP_N`.
- Hỏi theo mã hiệu/số văn bản chưa chuẩn → tăng `WEIGHT_BM25`.
- Hỏi diễn đạt tự do chưa chuẩn → tăng `WEIGHT_DENSE`.
- Muốn nhanh hơn → `QUERY_REWRITE_ENABLED=false` (mất khả năng hiểu đại từ) hoặc đặt
  `LLM_UTILITY_MODEL` là một model nhỏ cho bước viết lại truy vấn.

## Test

```bash
uv run pytest              # toàn bộ
uv run pytest -m "not slow"   # bỏ qua phần cần GPU
```

`tests/test_pipeline_e2e.py` chạy trọn pipeline trên Qdrant in-memory với model thật
(LLM được thay bằng bản giả), nên không cần dựng hạ tầng để kiểm tra chất lượng truy hồi.

`tests/test_agent.py` kiểm tầng tool, tầng lập kế hoạch và agent tổng mà không cần GPU,
Qdrant hay vLLM — tập trung vào các ràng buộc khiến những tầng này tồn tại: tên tool và
tham số phải có thật, file chỉ đọc được trong vùng cho phép, không workflow nào chạy khi
thiếu đầu vào bắt buộc, kế hoạch hỏng thì lùi được về định tuyến một bước, và hai bước
độc lập phải **thật sự chồng lấn về thời gian** chứ không chỉ "được gọi đủ".

`tests/test_toolloop.py` kiểm những thứ giữ cho vòng lặp công cụ không chạy vô hạn và
không mất việc: năm điều kiện dừng, lời gọi trùng bị bỏ qua mà **không kéo theo** những
lời gọi còn lại trong cùng lượt, chu trình A→B→A→B cũng bị bắt, và nguồn trống được báo
cho model bằng lời khác hẳn lời báo lỗi.

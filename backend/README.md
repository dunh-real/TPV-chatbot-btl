# TPV Chatbot - Backend

Backend đa workflow (LangGraph + FastAPI). Đã hoàn thiện:

- **Workflow 1** — hỏi đáp / tra cứu tài liệu (RAG hybrid 3 nhánh)
- **Workflow 2** — xử lý văn bản tự động (rule engine thể thức + soát chữ nghĩa + định tuyến)
- **Workflow 3** — soạn văn bản theo mẫu (số liệu từ CSDL + mẫu DOCX + kiểm chứng số)
- **Workflow 4** — tổng hợp báo cáo nhiều đơn vị (data tool + biểu đồ + đối chiếu file)
- **Workflow 5** — tạo bộ slide PowerPoint (JSON trung gian + python-pptx)
- **Agent tổng** — một endpoint tự định tuyến yêu cầu về đúng workflow

## Agent tổng: một câu yêu cầu → đúng một workflow

```
POST /api/agent/chat
     │
 intent_router          LLM phân loại; hỏng thì lùi về luật từ khoá
     │                  không có file đính kèm ⇒ không bao giờ đi nhánh document
     ├─ qa           → search_documents            → LLM  → answer + citations
     ├─ document     → analyze_document (parser)   → LLM  → lỗi thể thức + nhiệm vụ
     ├─ draft        → get_template + SQL          → LLM  → .docx
     ├─ report       → get_*_statistics (SQL)      → LLM  → .docx + biểu đồ
     ├─ presentation → get_*_statistics (SQL)      → LLM  → .pptx
     └─ clarify      → hỏi lại, không chạy workflow nào
     │
  finalize            gom file sinh ra + ghi lượt hội thoại
```

Nhánh `clarify` tồn tại vì đoán bừa đắt hơn hỏi lại: soạn nhầm loại báo cáo thì người
dùng phải đọc hết mới phát hiện, còn một câu hỏi lại chỉ mất năm giây.

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
gộp theo point id. Không chunk nào vượt ngưỡng reranker → đi nhánh `no_context`,
trả lời "không tìm thấy thông tin" thay vì để LLM tự suy diễn.

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

Tiêu chí thể thức ở [config/rules/nd30.yaml](config/rules/nd30.yaml) — lấy Nghị định
30/2020/NĐ-CP làm nền. **Các con số trong đó cần đối chiếu lại với văn bản gốc**
trước khi dùng chính thức; để trong YAML chính là để sửa mà không phải đụng code.

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

`tests/test_agent.py` kiểm tầng tool và định tuyến ý định mà không cần GPU, Qdrant hay
vLLM — tập trung vào các ràng buộc khiến tầng tool tồn tại: tên tool và tham số phải có
thật, file chỉ đọc được trong vùng cho phép, và không workflow nào chạy khi thiếu đầu
vào bắt buộc.

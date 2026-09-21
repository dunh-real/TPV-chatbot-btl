# Việc còn lại — cập nhật 20/09/2026

Ghi cho người tiếp tục sửa backend (Tiến Anh và đội dev khi nối giao diện).

Trạng thái: **523 test mặc định xanh** + 31 test `live` (chạy trên ERP, vLLM và
Presenton thật, không nằm trong lần chạy mặc định).

---

## 0a. WORKFLOW 2 LÀM LẠI (phiên 20/09, đợt sau)

Bốn việc, xuất phát từ một nhận xét: **bản soát báo oan thì người dùng mất lòng
tin vào cả bản soát**, kể cả những lỗi nó báo đúng.

### Bỏ kiểm đánh số — vì nó báo sai trên văn bản soạn ĐÚNG

Tái hiện được hai lỗi oan, cả hai đều trên cách đánh số chuẩn Nghị định 30:

| Văn bản | Máy nói | Vì sao |
|---|---|---|
| `a) b) c) d) đ) e) g)` | "e nhảy sang g" | `LETTERS` trong `outline.py` có chữ **"f"**. Tiếng Việt không có f/j/w/z, nên "g" bị tính là chữ thứ 8 thay vì thứ 7. |
| `A. B. C. D. Đ.` | "B nhảy sang Đ" | "C" và "D" khớp pattern số La Mã TRƯỚC (100 và 500), vượt `MAX_ORDINAL = 99` nên bị **bỏ hẳn** — mất luôn khỏi dàn ý. |

Cả hai đã vá trong `outline.py` (bảng chữ cái đúng; ngoài khoảng thì `continue`
xuống pattern sau thay vì `return None`). **Nhưng tiêu chí vẫn tắt** — bỏ khoá
`numbering` trong `config/rules/chung.yaml`, lý do ghi ngay tại đó. Còn những chỗ
mơ hồ không vá được: "L." trong dãy chữ cái vẫn là số La Mã 50, "I." vừa là số 1
vừa là chữ thứ chín. Một tiêu chí chỉ đúng phần lớn thời gian còn hại hơn không
có. Bật lại = bỏ dấu `#` của ba dòng trong YAML.

### Chính tả: LLM soát, prompt riêng

Hai nhánh LLM tách bạch, cùng chạy song song:

| Nhánh | Prompt | Lo việc |
|---|---|---|
| `chinh_ta_llm` | `DOC_SPELL_SYSTEM` | chính tả |
| `llm_review` | `DOC_REVIEW_SYSTEM` | ngữ pháp, diễn đạt, logic, thiếu ý |

Không gộp làm một: prompt chính tả phải dành gần hết chỗ cho bẫy "hai từ đúng đứng
cạnh nhau" cùng bảng lỗi hay gặp, mà đó là phần quyết định bản soát có báo oan hay
không. Nhét chung vào một prompt lo năm việc thì phần đó bị loãng.

**Đường đã đi qua, để khỏi ai đi lại:**

1. Ban đầu LLM soát chính tả trong cùng prompt với ngữ pháp → không đáng tin.
2. Thay bằng bảng cặp sai→đúng dò từ điển (215 cặp) → **26 cặp báo oan**. Tiếng
   Việt viết rời từng âm tiết nên hễ cả hai vế đều là từ có thật thì luôn có câu
   ĐÚNG đặt chúng cạnh nhau: *"Đơn vị **cũng cố** gắng hoàn thành"*, *"**Hồ sơ
   xuất** khẩu đã duyệt"*, *"Thành **tựu chung** của đơn vị"*. Không vá được.
3. Thêm cổng gác LLM (bảng tìm ứng viên, LLM phán ngữ cảnh) → 2/20 báo oan, nhưng
   phải nuôi thêm một node, một prompt và 215 dòng bảng mà **vẫn cần LLM**.
4. Bỏ hẳn bảng, giao chính tả cho LLM với prompt riêng → **0/23 báo oan, 0/14 bỏ
   sót**, ổn định qua 3 lượt đo.

**Ba thứ trong prompt làm nên khác biệt** (bỏ cái nào là báo oan quay lại):

- **Bẫy "hai từ đứng cạnh nhau"** với 16 cặp ví dụ BÁO/BỎ QUA đặt sát nhau.
- **Dạy tìm ranh giới từ ở CẢ HAI BÊN.** Không có phần này thì model tách đôi ngay
  hai chữ bị nghi rồi kết luận "vô nghĩa": nó tách `nổ | lực` trong *"sau vụ nổ
  lực lượng cứu hộ"* thay vì `vụ nổ | lực lượng`.
- **Bắt model KHAI phép thử ra** (`tach_roi`, `van_xuoi`), `loc_tach_roi` thi hành
  lời khai đó. Phải viết phép thử ra thì mới thật sự làm phép thử, và khi nó báo
  oan thì đọc `tach_roi` là biết ngay nó nghĩ sai ở đâu.

**Bốn van chặn trong `verify_findings`** — lọc thứ model trả về, không tin thẳng:

| Van | Chặn cái gì |
|---|---|
| trích được nguyên văn | lỗi bịa (van cũ, vẫn giữ) |
| `quote` ≤ 240 ký tự | model chép cả đoạn rồi "gợi ý" viết lại nguyên đoạn - khoanh vùng kiểu đó thì khung ôm trọn đoạn, vô dụng |
| `quote` phải có chữ | rác của khâu đọc file: số trang, số hiệu rời ("1516 11" ở chân trang) |
| `suggest` ≠ `quote` (bỏ qua khoảng trắng) | **riêng PDF**: khâu đọc file làm mất dấu xuống dòng, model tưởng câu chạy liền rồi "sửa" bằng cách thêm xuống dòng. Đó là vá lỗi của máy đọc file, không phải lỗi văn bản |

Đo trên công văn PDF thật của Sở GD Quảng Ngãi (27 khối, 3 trang): 9 phát hiện,
8 cái dài dưới 35 ký tự, tất cả đều là lỗi thật - trong đó có `NÐ` viết bằng chữ
Eth Latin thay cho `Đ` (mắt người gần như không thấy) và `Công căn` → `Công văn`.

**`config/chinh_ta.yaml` giờ chỉ còn `co_hoc` là chạy.** Bảng 215 cặp vẫn nằm đó
làm tư liệu soạn prompt - code không đọc. Thêm cặp vào bảng KHÔNG có tác dụng gì;
muốn model để ý lỗi mới thì sửa prompt.

### Trace của workflow 2 cần reducer

`DocumentState.trace` là `Annotated[dict, gop_trace]`. Không có reducer thì hai
nhánh song song cùng ghi `trace` trong một nhịp bị LangGraph coi là xung đột và
ném `InvalidUpdateError` - cả workflow chết chỉ vì hai nhánh muốn ghi số đo của
mình. Thêm nhánh song song nào có ghi `trace` thì nhớ chỗ này.

### Phản hồi mang theo tài liệu và vị trí ký tự

`blocks[]` (định dạng thật từng khối), `findings[]` (phẳng, có `start`/`end` và
`source`), `document.geometry`. Giao diện dựng lại trang rồi khoanh đúng chỗ thay
vì bắt người đọc dò lại danh sách phẳng. `llm_review` giữ nguyên tên nhưng giờ
chứa cả lỗi `source: "rule"` — xem ghi chú trong `HUONG-DAN-TICH-HOP-API.md`.

### Bảng phân công xuất ra văn bản trình ký

`POST /api/documents/giao-viec` → `.docx`, hai mẫu `cong_van` / `quyet_dinh`.
Không gọi LLM. `giao_viec_goi_y` trong phản hồi `/review` là mẫu nên chọn sẵn.
Trường thể thức để trống thì in dấu chấm lửng — **không tự đặt số ký hiệu hay tên
người ký**, vì một số văn bản bịa trông y như số thật.

---

Phiên 20/09 (đợt đầu) dọn bốn thứ, tất cả đều nhắm vào buổi demo:

1. Ô chờ của các workflow dài có **đồng hồ đếm giây thật** (§1) — 76-90 giây mà
   màn hình đứng im thì người xem tưởng treo.
2. **Đo thời gian thật qua đường public**: 76-90 giây so với trần 125 giây của
   Cloudflare, biên còn ~35-49 giây (§1) — đủ cho demo, nhưng trần đó không nâng
   được nếu không phải gói Enterprise, nên **chưa sửa hẳn**.
3. `PUBLIC_HOSTNAME` trong `.env` ghi sai tên miền, mở ra là trang lỗi (§6).
4. `scripts/seed_demo.py` viết lại cho khớp schema ERP (§5.1).

Phiên 19/09 trước đó: đổi phần tạo slide sang Presenton, tinh chỉnh workflow 3,
sửa mấy chỗ truy xuất CSDL cho đúng, và mở biến thể truy vấn bằng `group_by`.

---

## 0. ĐÃ KIỂM CHỨNG BẰNG CHẠY THẬT

### Workflow 4 (tổng hợp) — dùng được cho demo

Chạy thật tenant 64, kỳ 2026-08: `passed`, xuất file, 5/5 lần không câu nào sai.

```
I.   TÌNH HÌNH GỬI BÁO CÁO      9 đơn vị, 1 đã gửi
II.  TÌNH HÌNH NHÂN SỰ          [bảng 9 dòng]  28 người, +1
III. TÌNH HÌNH TRANG THIẾT BỊ   [bảng 33 dòng] 194 cái, 33 chủng loại
IV.  ĐỐI CHIẾU SỐ LIỆU          chỉ hiện khi thật sự lệch
V.   SỐ LIỆU CẦN KIỂM TRA LẠI   49/194 thiết bị chưa gán phòng ban
```

### Workflow 5 (slide) — mình soạn nội dung, Presenton render

```
engine=presenton | 6 slide | 45 giây | validation passed, 0 con số đáng ngờ
```

Đường đi cuối cùng: `build_slides_markdown` dựng sẵn TỪNG SLIDE (một chuỗi
markdown = một slide) rồi gửi qua `slides_markdown`; Presenton bỏ bước tự lập
dàn ý, chỉ chọn layout và render. Không còn slide bảng do code ghép thêm ở cuối
- thứ trước đây lạc hẳn phong cách với phần Presenton dựng.

Đã xem tận mắt bản PDF render ra: slide chỉ tiêu, bảng chi tiết cắt trang
(1/2, 2/2) và slide ghi chú đều đúng và đủ dòng.

### Workflow 3 (soạn báo cáo) — hết ba lỗi chữ, chạy 3/3 lần sạch

Trước khi sửa, cả ba lần chạy đều có: "đang ở trạng thái 0", "cần được bảo trì
vào ngày 19/9/2026" (lịch bảo trì không tồn tại), "có 5 loại đang cần bảo dưỡng"
(chỉ tiêu không tồn tại). Sau khi sửa: không còn câu nào trong ba loại đó.

---

## 1. Phần tạo slide chạy bằng Presenton

### Dựng lại sau khi khởi động máy

```bash
cd backend
docker compose --env-file .env -f docker/presenton.yml up -d
curl -s localhost:5002/api/v1/ppt/presentation/all >/dev/null && echo "sống"
```

Container `tpv-btl-presenton`, cổng **5002** (dự án khác đã chiếm 5001 — và
container của họ đang crash-loop vì key Gemini hỏng; đừng dùng nhờ).

### Image có MỘT bản vá

`slides_markdown` - thứ cho phép mình soạn nội dung còn Presenton chỉ render -
chết ở image gốc (27/05/2026):

```
LLM API error: PresentationLayoutModel.to_string()
got an unexpected keyword argument 'with_schema'
```

Lỗi của chính image, không phải của cách gọi. `docker/presenton/Dockerfile` vá
đúng một file; khi nâng image gốc thì **thử bỏ dòng COPY trước** - upstream sửa
rồi mà vẫn đè bản cũ lên là tự tạo ra lỗi khó tìm.

### Ba thiết lập không được đổi nếu chưa hiểu lý do

| Thiết lập | Vì sao |
|---|---|
| `DISABLE_AUTH=true` | bật đăng nhập thì bước export tự gọi API của chính nó, nhận **401**, và trả về file rỗng: `Export task failed ... Presentation slides not found`. Tái hiện trong 10 giây bằng `POST /api/v1/ppt/presentation/derive`, hỏng với cả curl lẫn httpx. An toàn vì cổng chỉ mở trên `127.0.0.1`. |
| cổng chỉ bind `127.0.0.1` | bộ slide chứa số liệu nhân sự |
| bảng KHÔNG giao cho Presenton | mọi template của nó chặn bảng ở 3-6 dòng; bảng 9 dòng làm schema từ chối, dựng lại 3 lần rồi trả về bộ slide rỗng — **hỏng cả bộ vì một cái bảng**. Bảng chi tiết do `append_to_pptx` ghép vào cuối file. |

### Model

`PRESENTON_CUSTOM_MODEL=openai/gpt-5.6-luna-pro` qua OpenRouter, khoá
`llm_slide_api_key`. Chat/agent **vẫn chạy vLLM nội bộ** — chỉ phần sinh slide
đi ra ngoài.

Đã thử `qwen/qwen3.6-plus`: chạy được nhưng là model suy luận (285 token suy
luận cho một câu hỏi tầm thường), và lần thử đầu bộ slide về rỗng. Đổi model thì
`up -d --force-recreate`, Presenton đọc cấu hình lúc khởi động.

**Tốn tiền thật** cho mỗi lần tạo slide. Trước buổi demo nên chạy thử một lần để
biết còn hạn mức.

### Còn lại của phần này

- ~~**Giao diện cần hiện tiến trình**~~ — **đã làm 20/09**. `loaderNode` nay
  nhận `hint = {text, slowAfter}` và hiện đồng hồ đếm giây thật; quá `slowAfter`
  thì đổi chữ thành "lâu hơn thường lệ — vẫn đang chạy". Chỉ đếm thời gian THẬT,
  không vẽ thanh tiến trình giả (các endpoint này trả về một cục, không phát sự
  kiện). Đã gắn cho 4 màn: slide, soạn báo cáo, tổng hợp, soát văn bản.
- **Cloudflare 524 — xem `README` §"Giới hạn 524 của Cloudflare".**
  Đo 20/09: **80,1 giây** qua `localhost`, **76,1 giây** qua
  `chatbot-demo.tpvtech.vn` (cả hai `200`). Trần là **125 giây** (không phải 100
  như bản ghi đầu tiên của phiên này — 100 là số cũ hay bị trích lại), nên biên
  còn **~35-49 giây**: đủ cho demo, chưa phải chữa gấp.
  **Trần này KHÔNG nâng được** trừ khi zone là gói Enterprise — Free/Pro/Business
  cố định. Nên đường sửa hẳn là phát SSE hoặc trả `202` + hỏi trạng thái, chứ
  không phải chỉnh cấu hình Cloudflare. **Chưa làm.**
- **`data/output/` chỉ dồn thêm.** Nay mỗi lần tạo là một file mới; cần dọn định kỳ.
- **Slide bìa in chữ "Chưa cung cấp"** nếu không truyền `inputs.nguoi_trinh_bay`.
  Cố ý không tự điền — bịa một cái tên ngay trang đầu thì tệ hơn.
- **Van chắn số chỉ soi phần Presenton viết** (`verify_upto` = số slide đầu).
  Bảng ghép thêm đến thẳng từ CSDL; soi chúng thì chú thích phân trang do chính
  code viết ("dòng 12-22/33") bị đọc thành ba con số bịa — tầng `live` đã đỏ
  đúng chỗ đó một lần.

---

## 2. Workflow 3 đã tinh chỉnh những gì

| Sửa | Vì sao |
|---|---|
| `bao_duong_cuoi` → `cap_nhat_cuoi`, nhãn "Cập nhật gần nhất (ERP)" | cột này là `Asm_Assets.LastModificationTime` — giờ SỬA BẢN GHI, không phải ngày bảo dưỡng. Nhãn cũ nói sai về dữ liệu, và model tin theo rồi suy tiếp thành lịch bảo trì. |
| mẫu `BC_TAINGUYEN` bỏ yêu cầu "số loại đang cần bảo dưỡng" | mẫu hỏi một chỉ tiêu KHÔNG TỒN TẠI thì model buộc phải dựng ra một con số để trả lời. Sửa ở mẫu rẻ hơn sửa ở prompt. |
| phạm vi đối chiếu số thu về từng mục (`_section_data`) | trước đây mọi mục nhận nguyên khối dữ liệu đơn vị nên van chắn rộng bằng cả khối; giờ `checked_numbers` còn 6 thay vì vài chục. Đây là việc `§3` bản ghi chú cũ nói là "phải đổi thiết kế". |
| số ký hiệu lấy từ sổ văn bản | trước gán cứng `01/BC-<đơn vị>`, báo cáo thứ hai trong năm trùng số và ghi đè bản trước lúc vào sổ. Nay ra 02, 03, 04. |
| `strip_markers` dọn dấu câu | gỡ `[1]` để lại `,,` và `,.` ngay trong văn bản trình ký. |

Prompt `DRAFT_SECTION_SYSTEM` thêm bốn điều cấm, mỗi điều ứng với một lỗi đã
thật sự xảy ra — đừng rút gọn lại cho ngắn.

---

## 3. Truy xuất CSDL đã sửa những gì

- **`van_ban` có thêm `ma_don_vi` và `ky`.** Mục "tình hình gửi báo cáo" trước
  đây luôn in 0/9 đơn vị: nó dò TÊN đơn vị trong `noi_gui` và lấy `ngay_van_ban`
  làm kỳ, mà báo cáo kỳ tháng 8 thì ký vào tháng 9. Bản ghi cũ không có hai
  trường này vẫn dò theo cách cũ.
- **`create_all()` tự thêm cột nullable còn thiếu.** Bảng cũ thiếu cột thì trước
  đây mọi truy vấn chết với "no such column" — trên CSDL đang có dữ liệu thật.
  Không thay cho migration: chỉ thêm cột, không xoá, không đổi kiểu.
- **Danh mục phòng ban cache theo phiên VÀ theo thuê bao.** Một báo cáo tổng hợp
  từng quét bảng phòng ban 9 lần cho cùng một danh mục. Cache phải khoá theo
  tenant — quên khoá thì báo cáo của thuê bao này in tên đơn vị của thuê bao
  khác, và không van chắn nào bắt được vì tên đó là tên thật.
- **Mốc thời gian không còn bị đọc thành số liệu.** Trích yếu "V/v báo cáo quân
  số ... tháng 8/2026" khớp mẫu nhân sự và trả về 8, nên bản tổng hợp in ra mục
  "ĐỐI CHIẾU SỐ LIỆU: báo cáo ghi 8, kiểm kê 3" — một chênh lệch không có thật,
  trong văn bản trình ký.

---

## 3b. Biến thể truy vấn: `group_by`

Model vẫn không sinh SQL. Nó chọn một khoá trong danh sách đóng, code dựng SQL:

| Tool | `group_by` |
|---|---|
| `get_personnel_statistics` | `phong_ban` (mặc định) / `chuc_vu` |
| `get_equipment_statistics` | `phong_ban` (mặc định) / `chung_loai` |

Đường từ câu tiếng Việt xuống: `dimension_from_request` khoanh bằng từ khoá
trước ("theo chức vụ", "theo chủng loại"), chỉ khi câu chữ không nói rõ mới lấy
phán đoán của model - cùng lối với `scope_from_request` đã có.

**Bất biến phải giữ khi thêm chiều mới:** gộp là chia lại các dòng, không phải
lọc, nên chỉ tiêu tổng không được đổi theo chiều gộp, và tổng các dòng phải bằng
chỉ tiêu tổng. Đã có test cho cả hai, kể cả một test `live` trên ERP thật. Gài
lại lỗi bỏ dòng "chưa gán" thì test đỏ đúng chỗ (`4 == 5`).

**Ba chỗ dễ quên khi thêm chiều thứ ba:**

1. `scope["units_with_data"]/["units_missing"]` chỉ có nghĩa khi gộp theo đơn vị.
   Để nguyên chúng lúc gộp theo chức vụ thì `current` đang khoá theo chức vụ, tra
   bằng id phòng ban ra 0 hết, và báo cáo in "cả 9 đơn vị chưa cung cấp dữ liệu".
2. `reconcile_node` so số theo ĐƠN VỊ. Bảng gộp chiều khác thì không dòng nào quy
   về được một đơn vị - phải bỏ qua, không so bừa.
3. Cột bảng lấy từ `breakdown_columns` do tool khai. Đừng viết cứng lại ở nơi
   dựng bảng: `report.py` và `presentation.py` từng mỗi bên giữ một danh sách.

**Chưa mở** (dữ liệu có sẵn, chỉ thiếu code): gộp theo đơn vị cha
(`Dms_WorkDepartment.ParentId`), thiết bị mua trong kỳ (`PurchaseDate`), chuỗi
nhiều kỳ liên tiếp. Gộp theo tình trạng thiết bị thì vẫn vướng enum ở mục 4.1.

---

## 4. Ba việc CHẶN, không sửa bằng code được

### 4.1 Mã trạng thái thiết bị
`Asm_Assets.Status` có đúng một giá trị `0` trên toàn bộ 98 dòng, enum nằm trong
mã nguồn ERP. Báo cáo và slide vì thế in `Trạng thái 0` trong bảng.

Cần xin đội ERP bảng tra rồi khai vào `.env`:

```
ERP_ASSET_STATUS_LABELS=0=Đang dùng,1=Hỏng,2=Chờ thanh lý
ERP_ASSET_STATUS_GOOD=0
```

Chưa khai thì hệ thống **cố ý** bỏ hai chỉ tiêu "tình trạng tốt"/"cần xử lý".
Đừng khai bừa cho đẹp — cả 98 dòng đang là `0`, khai xong sẽ thành "100% tốt".

### 4.2 Chưa có tool "thông tin nhân viên"
Hỏi "báo cáo thông tin nhân viên" hiện trả về nhân sự theo đơn vị, không có tên,
chức vụ, ngày vào làm. `Hrm_EmployeeProfile` có sẵn `FullName`,
`WorkPositionId`, `HireDate`; `Dms_WorkPosition` đã trong `ALLOWED_TABLES` —
thiếu đúng một tool phơi ra. Làm theo khuôn `get_personnel_statistics`, và nhớ:
danh sách nhân sự là dữ liệu cá nhân.

### 4.3 Chất lượng dữ liệu ERP
- 49/194 đơn vị thiết bị chưa gán `WorkDepartmentId` (đang được điền dần).
- Vị trí nằm trong `Description` dạng văn xuôi ("24 màn ở tầng 4"). Không nối
  được với phòng ban, và **đừng** viết code đoán.
- Tenant 78 có 52 nhân sự, 10 phòng ban, **0 thiết bị**.

---

## 5. Việc nhỏ còn tồn

1. ~~**`scripts/seed_demo.py` đã hỏng**~~ — **đã sửa 20/09**. Viết lại cho khớp
   schema hiện tại: chỉ nạp `template_bao_cao` + `van_ban`, bỏ hẳn phần seed
   `DonVi`/`TrangBi`/`KyKiemKe` (ERP sở hữu, chỉ đọc). `--reset` nay xoá ĐÚNG
   những dòng nó tạo thay vì `drop_all` — bản cũ xoá cả sổ `van_ban`, tức tua
   bộ đếm số ký hiệu về `01` rồi ghi đè lên báo cáo đã phát hành. Chạy hai lần
   liên tiếp không đổi số dòng (3 mẫu / 54 văn bản trên `data/demo.db`).
2. ~~**Màn "Kho tri thức" chết hoàn toàn**~~ — **đã sửa 20/09**. `/api/documents/upload`
   và `/ingest-text` đều trả **500**: `IngestResponse(**result.__dict__)` mà
   `IngestResult` là `@dataclass(slots=True)` — dataclass có `slots` thì KHÔNG có
   `__dict__`. Sửa bằng `dataclasses.asdict()`. Đáng chú ý: **ingest đã chạy xong
   rồi mới nổ** ở bước dựng response, nên tài liệu vẫn vào Qdrant còn người dùng
   thấy lỗi — dễ nạp trùng vì tưởng chưa được. Lọt qua 447 test vì **không test nào
   chạm tầng API của `documents`**; nay có `tests/test_kho_tri_thuc_api.py` (3 ca).
3. **Câu nhắc cả hai mảng** (`"thiết bị cấp cho nhân viên"`) vẫn rơi về phán
   đoán của LLM. Cố ý không đè vì ý định thật sự mơ hồ.
4. **Workflow 4 từng có 1/10 lần bị van chắn số chặn** và không bắt lại được.
   Phiên này chạy 5 lần đều `passed`, nhưng chưa đủ để kết luận là hết. Cách tìm
   nếu gặp lại: log `quote` và `numbers` của mọi issue mức `error` ra file, chạy
   vài chục lần rồi đọc. Đừng đoán.

---

## 6. Bẫy vận hành

- **Server không chạy `--reload`.** Sửa `app/` hay `.env` xong phải khởi động lại.

  ```bash
  PID=$(ss -ltnp | grep ':8081' | grep -oP 'pid=\K[0-9]+') && kill $PID
  cd backend && .venv/bin/uvicorn app.main:app --port 8081 --host 127.0.0.1
  ```

- **Presenton cũng đọc cấu hình lúc khởi động**: đổi `.env` thì
  `docker compose --env-file .env -f docker/presenton.yml up -d --force-recreate`.

- **`PUBLIC_HOSTNAME` trong `.env` từng ghi sai** (`chatbot.tpvtech.vn`). Tên đó
  trả **525** (SSL handshake hỏng); tên chạy được là **`chatbot-demo.tpvtech.vn`**
  → `200`. `serve_public.sh` in đúng giá trị này ra khung URL cuối màn hình, nên
  ghi sai là người mở link lúc demo gặp trang lỗi. Đã sửa 20/09. Đổi hostname
  thì phải sửa cả ô **Public hostname** bên dashboard Cloudflare.

- **Đang có 2 tiến trình `cloudflared` của dự án này** (một chạy bằng
  `--config ~/.cloudflared/tpv-chatbot.yml`, một bằng `--token` từ
  `serve_public.sh`), cạnh vài tunnel của dự án khác trên cùng máy. Khác token
  nên không chia tải lẫn nhau, nhưng trước khi demo nên xác minh cái nào đang
  phục vụ `chatbot-demo.tpvtech.vn` để khỏi tắt nhầm.

- **`PUBLIC_ACCESS_TOKEN` vẫn chưa đặt.** Đường public hiện ai biết cũng gọi
  được, kể cả `DELETE /api/documents/{id}`. Trước khi đưa link cho khách thì đặt
  token, hoặc bật Cloudflare Access.

- **`TRUST_IDENTITY_HEADERS=true` là lỗ hổng có chủ ý.** Ai gọi được API cũng tự
  xưng tenant bất kỳ qua `X-Tenant-Id`. Khi giao diện có đăng nhập: viết phần
  giải mã JWT trong `_from_token()` (`app/core/context.py`) rồi đặt `false`.

- **File đầu ra cũ không mang dấu tenant thì không tải được nữa** (cố ý). Xoá
  `backend/data/output/*` không có hậu tố `__t<N>`.

---

## 6b. Reranker nhạy từ vựng — ĐÃ SỬA 20/09

### Bệnh

`AITeamVN/Vietnamese_Reranker` gần như so khớp từ vựng chứ không hiểu diễn đạt
khác. Cùng MỘT chunk của `CV-105-BGD`, cùng một ý:

| Câu hỏi | Điểm |
|---|---|
| "**hạn nộp** báo cáo là khi nào" | 0,0073 |
| "báo cáo **gửi về trước ngày** nào" | **0,9399** |

Văn bản viết *"Báo cáo **gửi về** ... **trước ngày** 20/9/2026"*. Ai tình cờ dùng
đúng chữ của văn bản thì được trả lời, ai dùng chữ khác thì nhận "không tìm thấy"
về một thứ có thật trong tài liệu.

Dài hơn KHÔNG giúp — đo tách bạch hai yếu tố:

| Nhóm | Độ dài | Từ vựng | Điểm |
|---|---|---|---|
| ngắn, từ vựng lệch | 7-12 từ | lệch | 0,0034-0,0043 |
| **dài, từ vựng lệch** | **28-33 từ** | lệch | **0,0000-0,0002** |
| ngắn, trùng từ văn bản | 7-10 từ | trùng | 0,86-0,94 |
| dài, trùng từ văn bản | 30 từ | trùng | **1,0000** |

Nối dài mà không thêm từ ngữ của văn bản thì **loãng đi, điểm tụt**.

### Cách sửa đã làm

1. **`QUERY_REWRITE_SYSTEM`** ([prompts.py](app/agents/prompts.py)): buộc sinh
   **ít nhất một** biến thể theo LỐI VĂN BẢN HÀNH CHÍNH, bỏ số hiệu khỏi biến thể
   đó. Quy tắc cũ "giữ nguyên mọi mã hiệu" vẫn còn nhưng nay chỉ áp cho **ít nhất
   một** biến thể — nhánh BM25 vẫn cần mã hiệu để ra đúng tài liệu.
2. **`CrossEncoderReranker.score_best`** ([reranker.py](app/rag/reranker.py)): chấm
   với MỌI cách diễn đạt rồi lấy **max** cho từng ứng viên. `rerank()` nay nhận
   `str` hoặc danh sách. Trần `rerank_max_queries = 5`.
3. **Câu GỐC luôn nằm trong tập chấm** ([qa.py](app/agents/nodes/qa.py),
   [chat.py](app/api/chat.py)). Đây là chỗ suýt hỏng: bản viết lại và biến thể đều
   do máy sinh, có lúc kém hơn chính câu người dùng hỏi. "Ai ký công văn chỉ thị
   kiểm kê" được 0,1298 với câu gốc, nhưng cả 4 câu viết lại đều dưới ngưỡng và
   một tài liệu KHÁC trèo lên 0,1201 — hệ thống trả lời về sai văn bản.

### Kết quả đo lại (20/09, sau khi sửa)

| Câu hỏi | Trước | Sau |
|---|---|---|
| "Công văn 105 yêu cầu báo cáo gì, hạn nộp khi nào" | 0,0116 ❌ | **0,865** ✅ |
| "Ai ký công văn chỉ thị kiểm kê" | 0,1298 ✅ | **0,130 ✅ đúng tài liệu** |
| "Phòng Kỹ thuật kiểm kê bao nhiêu trang thiết bị" | 0,9985 ✅ | 0,999 ✅ |
| "Công văn 105/CV-BGĐ do ai ký, ban hành ngày nào" | 1 nguồn | **3 nguồn** ✅ |
| *ngoài kho*: nghỉ phép / thuế GTGT / lương tối thiểu | rỗng ✅ | **rỗng ✅** |

**Van không mở toang** — đây là ranh giới quan trọng nhất, có test giữ
(`tests/test_rerank_nhieu_cach_hoi.py`, 7 ca).

**Chi phí:** rerank 194ms → 768ms (5 cách hỏi thay vì 1, trên 14 ứng viên). Cả
câu hỏi QA là ~9,3s nên phần tăng ~6%. Kho lớn hơn (`rrf_top_k=20`) thì ước
~1,1s. Đặt `rerank_max_queries = 1` là quay về hành vi cũ.
**Không thêm lời gọi LLM nào** — biến thể vốn đã sinh sẵn cho khâu truy hồi.

### Còn lại

- **Câu trống ngữ cảnh vẫn rớt**: "hạn nộp báo cáo là khi nào" (không nói báo cáo
  nào) → 0 nguồn, hệ thống hỏi lại. Chấp nhận được: câu đó mơ hồ thật.
- **Không hạ `RERANK_SCORE_THRESHOLD`.** Đã đo: câu ngoài kho 0,0002 còn ca hỏng
  0,0116 — hai vùng chồng nhau, không ngưỡng nào tách được.

### Hai cách ĐÃ THỬ VÀ LOẠI — đừng thử lại

| Cách | Vì sao loại |
|---|---|
| Gắn số hiệu làm tiền tố chunk | 0,0001 → 0,0345, vẫn dưới ngưỡng, và làm TỤT câu hỏi nội dung (0,9009 → 0,8638) |
| Mở rộng câu hỏi bằng từ vựng lấy từ chính chunk (PRF) | Chữa được ca hỏng (0,0043 → 0,9971) nhưng thổi câu NGOÀI KHO từ 0,0000 lên **0,9082** — mất hẳn khả năng nói "không có trong tài liệu" |

---

## 6c. Workflow 3 có hai nguồn (thêm 20/09)

`nguon=csdl` (như cũ) hoặc `nguon=tai_lieu` (mới) - xem `README` §"Workflow 3".

**Điều quan trọng nhất phải nhớ:** hai nhánh cho ra hai văn bản trông giống hệt
nhau, nhưng bảo đảm về con số khác hẳn.

| | bảo đảm |
|---|---|
| `csdl` | mỗi con số truy được về **một trường dữ liệu** của ERP |
| `tai_lieu` | con số **có xuất hiện nguyên văn** trong tài liệu nguồn |

Nhánh `tai_lieu` chặn được model bịa số mới; **không** chặn được model lấy một số
có thật rồi đặt sai chỗ. Đã ghi rõ ở docstring `drafting_doc.py`, ở docstring
endpoint, và hiện thành một thẻ cảnh báo trên giao diện. Đừng gỡ mấy chỗ đó đi
cho gọn - người ký cần biết mình đang cầm loại nào.

Giới hạn đã biết, có test ghi lại: `79 - 72 = 7` mà "7" tình cờ có trong tài liệu
thì van không bắt được. Đó là lý do prompt cấm mọi phép tính.

**Chạy thật 20/09, ba ca đều ra file:**

| Tài liệu | Kết quả |
|---|---|
| `Bao_cao_kiem_ke_Phong_Ky_Thuat.docx` (có bảng) | 9,1s · passed · 30 số · 3 mục |
| `CV-105-BGD.md` (thuần chữ, không bảng) | passed · 11 số · 3 mục · `co_bang_so_lieu: false` kèm lý do |
| thiếu `file_id` | `missing_input: ['file_id']`, không soạn bừa |

**Hai lỗi thể thức tìm được khi đọc file xuất ra** (đã sửa) - cả hai chỉ lộ khi
mở file lên xem, test không bắt:
- `Số: 01/BC-` cụt đuôi, vì nhánh này không có mã đơn vị để ghép
- `Kính gửi:` in ra rồi để trống khi không có nơi nhận; nay bỏ hẳn dòng đó

**Giao diện chỉ phơi một nguồn cho mỗi màn** (chốt chiều 20/09):

| Màn | Gọi | Nguồn |
|---|---|---|
| Soạn báo cáo | `/draft` với `nguon=tai_lieu` | MỘT tài liệu tải lên |
| Tổng hợp báo cáo | `/aggregate` với `nguon_so_lieu=csdl` | CSDL, nhiều đơn vị |

Hai ô chọn nguồn đã bỏ khỏi giao diện. **Backend vẫn giữ đủ cả bốn nhánh** -
`/draft` còn `nguon=csdl` và `/aggregate` còn `nguon_so_lieu=tai_lieu`, có test
phủ, gọi thẳng API vẫn chạy. Đừng xoá chúng: **agent tổng dùng nhánh `csdl` của
`/draft`** qua ý định `draft` (`graph.py:draft_branch` gọi `run_draft_workflow`
không truyền `nguon`, tức mặc định `csdl`). Xoá là agent mất khả năng soạn báo
cáo một đơn vị từ ERP.

**Hệ quả cần biết:** không còn đường nào TRÊN GIAO DIỆN để soạn báo cáo một đơn
vị theo mẫu từ ERP. Muốn dùng thì qua màn Agent tổng, hoặc gọi thẳng
`/api/reports/draft` với `nguon=csdl`.

**Chưa làm:** nhánh `tai_lieu` mới nhận **một** file. Muốn gộp nhiều nguồn thì đó
là việc của workflow 4.

---

## 6d. Presenton tự sửa tiêu đề slide — đã chặn 20/09

Sáu slide của cùng một bảng bị cắt trang mang **bốn cách đặt tên khác nhau**, dù
code viết tiêu đề giống hệt nhau. Presenton tự rút gọn cho vừa layout:

```
(1/6) Trang thiết bị theo đơn vị          <- đủ chữ
(2/6) Thiết bị theo đơn vị                <- mất "Trang"
(3/6) Chi tiết trang thiết bị             <- mất "theo đơn vị"
 4/6  Chi tiết trang thiết bị theo đơn vị <- mất cả dấu ngoặc
```

Không sai số liệu, nhưng chiếu lên màn hình là thấy ngay máy viết.

Chữa bằng hai việc cùng lúc: **rút ngắn tiêu đề code sinh** (bỏ chữ "Chi tiết",
42 → 31 ký tự, bớt cớ để nó sửa) và thêm **quy tắc 7** vào `INSTRUCTIONS` cấm
đổi tiêu đề. Đo lại bằng một lần chạy thật: cả 6 slide ra
`Trang thiết bị theo đơn vị (n/6)` giống hệt nhau, chỉ khác số trang.

**Khi đọc bộ slide bằng python-docx/pptx, đừng dùng `shape.has_table`.**
Presenton dựng bảng bằng **các text box ghép lại**, không phải đối tượng table
của pptx — `has_table` trả về `False` trên mọi slide và nhìn như bộ slide rỗng.
Cách kiểm đúng: gom toàn bộ text của các shape rồi dò từng dòng của CSDL. Đo
20/09: 33/33 dòng có mặt, không dòng nào rơi.

---

## 7. Điều đáng lo nhất

Vẫn như phiên trước, và phiên này lặp lại y hệt: **mỗi lần chạy thật một câu hỏi
mới là lòi ra một lỗi mới**, không lần nào tìm ra trước bằng đọc code hay chạy
test. Lỗi nhãn cột `bao_duong_cuoi`, lỗi mẫu hỏi chỉ tiêu không tồn tại, lỗi
trích yếu bị đọc thành nhân sự — cả ba đều lọt qua 419 test cũ.

Cách duy nhất đang hiệu quả: **chạy thật, đọc từng câu trong file xuất ra**, và
mỗi lần tìm ra lỗi thì thêm ca đó vào test TRƯỚC khi sửa, kiểm rằng nó đỏ.

Và đừng coi "439 test xanh" là bằng chứng phần tạo slide/báo cáo đã dùng được
cho văn bản trình ký. Văn bản vẫn cần người duyệt.

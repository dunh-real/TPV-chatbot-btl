# Việc còn lại — cập nhật 19/09/2026 (phiên chiều)

Ghi cho người tiếp tục sửa backend (Tiến Anh và đội dev khi nối giao diện).

Trạng thái cuối phiên: **439 test mặc định + 31 test `live` đều xanh**, tầng
`live` chạy trên ERP, vLLM và Presenton thật.

Phiên này làm bốn việc: đổi phần tạo slide sang Presenton, tinh chỉnh workflow 3,
sửa mấy chỗ truy xuất CSDL cho đúng, và mở biến thể truy vấn bằng `group_by`.

---

## 0. ĐÃ KIỂM CHỨNG BẰNG CHẠY THẬT

### Workflow 4 (tổng hợp) — dùng được cho demo

Chạy thật tenant 64, kỳ 2026-08: `passed`, xuất file, 5/5 lần không câu nào sai.

```
I.   TÌNH HÌNH GỬI BÁO CÁO      9 đơn vị, 1 đã gửi
II.  TÌNH HÌNH QUÂN SỐ          [bảng 9 dòng]  28 người, +1
III. TÌNH HÌNH TRANG THIẾT BỊ   [bảng 33 dòng] 194 cái, 33 chủng loại
IV.  ĐỐI CHIẾU SỐ LIỆU          chỉ hiện khi thật sự lệch
V.   SỐ LIỆU CẦN KIỂM TRA LẠI   49/194 trang bị chưa gán phòng ban
```

### Workflow 5 (slide) — đã đổi sang Presenton, chạy thật được

```
engine=presenton | 6 slide | 62 giây | validation passed, 0 con số đáng ngờ
```

Bộ slide gồm 5 slide Presenton dựng + 1 slide bảng chi tiết 9 dòng do code ghép.
Không còn câu bịa kiểu "Phòng Kinh doanh và Kỹ thuật thiếu số liệu".

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

- **Mỗi bộ slide mất khoảng 60-90 giây.** Giao diện cần hiện tiến trình, nếu
  không người dùng tưởng treo. `elapsed_seconds` có trong kết quả trả về.
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
  số ... tháng 8/2026" khớp mẫu quân số và trả về 8, nên bản tổng hợp in ra mục
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
(`Dms_WorkDepartment.ParentId`), trang bị mua trong kỳ (`PurchaseDate`), chuỗi
nhiều kỳ liên tiếp. Gộp theo tình trạng trang bị thì vẫn vướng enum ở mục 4.1.

---

## 4. Ba việc CHẶN, không sửa bằng code được

### 4.1 Mã trạng thái trang bị
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
Hỏi "báo cáo thông tin nhân viên" hiện trả về quân số theo đơn vị, không có tên,
chức vụ, ngày vào làm. `Hrm_EmployeeProfile` có sẵn `FullName`,
`WorkPositionId`, `HireDate`; `Dms_WorkPosition` đã trong `ALLOWED_TABLES` —
thiếu đúng một tool phơi ra. Làm theo khuôn `get_personnel_statistics`, và nhớ:
danh sách nhân sự là dữ liệu cá nhân.

### 4.3 Chất lượng dữ liệu ERP
- 49/194 đơn vị trang bị chưa gán `WorkDepartmentId` (đang được điền dần).
- Vị trí nằm trong `Description` dạng văn xuôi ("24 màn ở tầng 4"). Không nối
  được với phòng ban, và **đừng** viết code đoán.
- Tenant 78 có 52 nhân sự, 10 phòng ban, **0 trang bị**.

---

## 5. Việc nhỏ còn tồn

1. **`scripts/seed_demo.py` đã hỏng** từ đợt chuyển sang ERP: nó import `DonVi`,
   `TrangBi`, `KyKiemKe` — các model đã bị xoá. Mẫu báo cáo hiện nằm sẵn trong
   `data/demo.db`. Hoặc sửa lại script cho khớp, hoặc xoá hẳn và ghi rõ mẫu lấy
   từ đâu; để nguyên là bẫy cho người cài mới.
2. **Câu nhắc cả hai mảng** (`"trang bị cấp cho nhân viên"`) vẫn rơi về phán
   đoán của LLM. Cố ý không đè vì ý định thật sự mơ hồ.
3. **Workflow 4 từng có 1/10 lần bị van chắn số chặn** và không bắt lại được.
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

- **`TRUST_IDENTITY_HEADERS=true` là lỗ hổng có chủ ý.** Ai gọi được API cũng tự
  xưng tenant bất kỳ qua `X-Tenant-Id`. Khi giao diện có đăng nhập: viết phần
  giải mã JWT trong `_from_token()` (`app/core/context.py`) rồi đặt `false`.

- **File đầu ra cũ không mang dấu tenant thì không tải được nữa** (cố ý). Xoá
  `backend/data/output/*` không có hậu tố `__t<N>`.

---

## 7. Điều đáng lo nhất

Vẫn như phiên trước, và phiên này lặp lại y hệt: **mỗi lần chạy thật một câu hỏi
mới là lòi ra một lỗi mới**, không lần nào tìm ra trước bằng đọc code hay chạy
test. Lỗi nhãn cột `bao_duong_cuoi`, lỗi mẫu hỏi chỉ tiêu không tồn tại, lỗi
trích yếu bị đọc thành quân số — cả ba đều lọt qua 419 test cũ.

Cách duy nhất đang hiệu quả: **chạy thật, đọc từng câu trong file xuất ra**, và
mỗi lần tìm ra lỗi thì thêm ca đó vào test TRƯỚC khi sửa, kiểm rằng nó đỏ.

Và đừng coi "439 test xanh" là bằng chứng phần tạo slide/báo cáo đã dùng được
cho văn bản trình ký. Văn bản vẫn cần người duyệt.

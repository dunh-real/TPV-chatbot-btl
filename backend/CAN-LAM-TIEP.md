# Việc còn lại — ghi ngày 19/09/2026

Ghi cho người tiếp tục sửa backend (Tiến Anh và đội dev khi nối giao diện).

Trạng thái cuối phiên: **419 test mặc định xanh**, 24 test `live` chưa chạy lại
được vì CSDL ERP đang tắt.

---

## 0. Việc đầu tiên khi ERP mở lại

`dbnews.tpvtech.vn:7679` hiện không nhận kết nối. Kiểm bằng:

```bash
bash -c '</dev/tcp/dbnews.tpvtech.vn/7679' && echo mở || echo tắt
```

Mở rồi thì chạy theo thứ tự:

```bash
cd backend
.venv/bin/python -m pytest -m live -q        # 24 ca: câu tiếng Việt -> tham số -> file
```

Ba thứ **đã sửa nhưng chưa chạy thật lần nào**, phải mắt thấy:

1. Đầu mục báo cáo phải ra `I, II, III, IV, V` — trước đây ra `IIII`, `IIIII`.
2. Mục "TÌNH HÌNH TRANG THIẾT BỊ" phải có bảng chi tiết theo đơn vị.
3. Phép đối chiếu số dòng bảng trong `tests/test_yeu_cau_that.py`
   (`_so_dong_dang_le_co`) — viết xong nhưng chưa kiểm chứng.

Với (3), cách kiểm chứng là **cố ý làm hỏng rồi xem test có đỏ không**: sửa
`app/documents/pptx_builder.py` dòng `rows = spec.table.rows` thành
`rows = spec.table.rows[:8]`, chạy `pytest -m live`. Không đỏ nghĩa là phép đối
chiếu đó vô dụng, phải viết lại.

---

## 1. Ba việc CHẶN, không sửa bằng code được

### 1.1 Mã trạng thái trang bị
`Asm_Assets.Status` có đúng một giá trị `0` trên toàn bộ 98 dòng, và enum nằm
trong mã nguồn ERP chứ không có bảng tra trong CSDL. Slide và báo cáo vì thế in
`Trạng thái 0`.

Cần: xin đội ERP bảng tra, rồi khai vào `.env`:

```
ERP_ASSET_STATUS_LABELS=0=Đang dùng,1=Hỏng,2=Chờ thanh lý
ERP_ASSET_STATUS_GOOD=0
```

Chưa khai thì hệ thống **cố ý** bỏ hai chỉ tiêu "tình trạng tốt"/"cần xử lý" thay
vì đoán. Đừng khai bừa cho đẹp — cả 98 dòng đang là `0`, khai xong sẽ thành
"100% tốt", một con số sai đi thẳng vào báo cáo trình ký.

### 1.2 Chưa có tool "thông tin nhân viên"
Hỏi "báo cáo thông tin nhân viên" hiện trả về **quân số theo đơn vị**, không có
tên, chức vụ, ngày vào làm của từng người. `Hrm_EmployeeProfile` có sẵn
`FullName`, `WorkPositionId`, `HireDate` và `Dms_WorkPosition` đã nằm trong
`ALLOWED_TABLES` — thiếu đúng một tool phơi ra.

Làm thì theo khuôn `get_personnel_statistics` trong `app/tools/data.py`, và nhớ:
danh sách nhân sự là dữ liệu cá nhân, cân nhắc ai được xem trước khi mở.

### 1.3 Chất lượng dữ liệu ERP
- 66/98 trang bị chưa gán `WorkDepartmentId` (đầu phiên là 90/98 — đang được điền
  dần). Hệ thống đã đếm đủ và cảnh báo, nhưng không chia được về đơn vị.
- Thông tin vị trí nằm trong `Description` dạng văn xuôi ("24 màn ở tầng 4",
  "1 máy ở VP Đăk Lawk"). Không nối được với phòng ban, và **đừng** viết code
  đoán — đó là bịa ra liên kết không có thật.
- Tenant 78 có 52 nhân sự, 10 phòng ban, **0 trang bị**.

---

## 2. Ba việc nhỏ còn tồn

1. **Slide đánh giá của deck nhân sự vẫn nhắc "0/9 đơn vị gửi báo cáo".**
   `bao_cao` không nằm trong phạm vi khoanh vùng của `scope_from_request`. Cần
   quyết: coi nó là bối cảnh chung của mọi báo cáo kỳ (giữ nguyên) hay tách nốt.
2. **Câu nhắc cả hai mảng** (`"trang bị cấp cho nhân viên"`) rơi về phán đoán của
   LLM. Cố ý không đè vì ý định thật sự mơ hồ. Nếu muốn quy tắc cứng thì phải
   định nghĩa quy tắc đó trước.
3. **`MAX_SLIDES = 8`** trong `app/agents/nodes/presentation.py` lệch với prompt
   ghi "4-6 slide".

---

## 3. Giới hạn theo thiết kế (không phải lỗi)

- **Workflow 3 đưa toàn bộ dữ liệu đơn vị cho LLM ở mọi mục**, nên phạm vi đối
  chiếu số cũng rộng bằng chừng đó. Workflow 4 và 5 thu hẹp theo từng mục nên
  chặt hơn. Muốn workflow 3 chặt bằng thì phải thu hẹp cả payload gửi LLM — đổi
  thiết kế, không phải vá lỗi.
- **`_normalize_focus` là lưới an toàn cho lúc model đổi hành vi.** Prompt hiện đã
  công bố enum nên model tự viết đúng, và tầng `live` không ép model "trôi" được.
  Nó chỉ có test đơn vị ở `tests/test_workflow5.py`.
- **Van chắn số không bắt được số nhỏ trùng ngẫu nhiên** — xem đầu file
  `app/documents/verify.py`. Văn bản vẫn cần người duyệt trước khi phát hành.

---

## 4. Bẫy vận hành

- **Server không chạy `--reload`.** Sửa code xong phải khởi động lại tay, nếu
  không sẽ gặp đúng cảnh "code mới mà báo cáo vẫn ra số cũ". Sửa `.env` thì
  **bắt buộc** khởi động lại vì `get_settings()` có `@lru_cache`.

  ```bash
  PID=$(ss -ltnp | grep ':8081' | grep -oP 'pid=\K[0-9]+') && kill $PID
  cd backend && .venv/bin/uvicorn app.main:app --port 8081 --host 127.0.0.1
  ```

- **`TRUST_IDENTITY_HEADERS=true` là lỗ hổng có chủ ý.** Ai gọi được API cũng tự
  xưng tenant bất kỳ qua `X-Tenant-Id`. Chấp nhận được trong mạng nội bộ. Khi
  giao diện có đăng nhập: viết phần giải mã JWT trong `_from_token()`
  (`app/core/context.py`) rồi đặt cờ này thành `false`. Không phải sửa gì ở tầng
  truy vấn.

- **File đầu ra cũ không mang dấu tenant thì không tải được nữa** (cố ý — chúng
  chứa số liệu của thuê bao không xác định). Xoá `backend/data/output/*` không có
  hậu tố `__t<N>` là được.

---

## 5. Điều đáng lo nhất

Trong phiên này, **mỗi câu hỏi mới của người dùng đều lòi ra một lỗi mới** — không
lần nào tìm ra trước. Ba lỗi nặng nhất (`focus` nhận văn xuôi, bảng 33 dòng bị cắt
còn 8, kỳ đối chiếu trùng chính nó) đều lọt qua 300+ test cũ, vì test cũ **mớm sẵn
tham số đúng** rồi mới kiểm hàm.

`tests/test_yeu_cau_that.py` được viết để bịt khoảng đó, nhưng khi thử tái tạo 4
lỗi cũ thì **nó chỉ bắt được 2**. Nên đừng coi "419 test xanh" là bằng chứng phần
tạo slide và báo cáo đã dùng được cho văn bản trình ký. Chưa.

Cách tăng độ tin: mỗi lần tìm ra lỗi mới, **thêm câu hỏi gây ra nó vào `CORPUS`**
trước khi sửa, và kiểm rằng test đỏ trước rồi mới xanh sau.

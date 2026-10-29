# Review: trích xuất dàn ý văn bản - parser văn phạm (mới) so với rule-based (đang dùng)

Ngày: 07/10/2026. Thực nghiệm chạy trên máy chủ `abc@100.105.11.124`, thư mục
`~/loi_workspace/layout_testing/`. Mã nguồn backend không bị sửa; mọi thứ nằm trong script riêng.

## 1. Bối cảnh

Nhánh `rule_check` của workflow 2 cần một dàn ý (Phần > Chương > Mục > Điều > Khoản > Điểm) để kiểm
cấp tiêu đề, mục rỗng, trùng tên, đánh số. Dàn ý hiện do `build_outline`
([app/documents/outline.py](app/documents/outline.py)) dựng. Trên văn bản thường nó chạy ổn, nhưng
trên văn bản **sửa đổi, bổ sung** (nghị định sửa nhiều nghị định khác) nó không phân biệt được Điều của
chính văn bản với Điều nằm trong phần trích dẫn - đây cũng là lý do tiêu chí đánh số liên tục đang tắt
([config/rules/chung.yaml](config/rules/chung.yaml)).

## 2. Hai phương pháp

| | Rule-based (đang dùng) | Parser văn phạm (mới) |
|---|---|---|
| File | `app/documents/outline.py` (`parse_marker`, `build_outline`) | `tests/layout_experiment/grammar_parser.py` |
| Đầu vào | Lớp chữ ẩn do `qwen_ocr.py` chèn vào PDF, đọc qua `parse_document` | Cùng kết quả OCR đó, đọc thẳng từ `test_results/layout_grammar/ocr/<doc>/pages.json` |
| Cách quyết định | Từng dòng độc lập: nhận dạng ký hiệu đầu dòng, gán cấp theo kiểu ký hiệu | Beam search (beam 64) trên **cả văn bản**, chọn chuỗi nhãn có tổng chi phí thấp nhất |
| Luật | Pattern ký hiệu + ngưỡng độ dài tiêu đề | Văn phạm theo `nguyentac.md`: thứ bậc Phần > Chương > Mục > Điều > Khoản > Điểm; dãy số tăng liền; bảng chữ cái tiếng Việt (không f, j, w, z); "như sau:" mở phạm vi trích dẫn, đơn vị kế tiếp của văn bản đóng phạm vi |
| Văn bản sửa đổi | Không có khái niệm trích dẫn | Có: nội dung trích dẫn được lồng dưới lệnh sửa đổi (ý tưởng "quoted structure" của Akoma Ntoso, cách làm của các parser luật như Laws.Africa) |
| LLM | Không | Không dùng cho phân tích cấu trúc (chỉ dùng cho OCR) |

Các luật chính của parser văn phạm:

- Dòng mở bằng ngoặc kép luôn là nội dung trích dẫn (luật cứng).
- Bước nhảy số: liền mạch chi phí 0; hậu tố cùng số (4a sau 4) chi phí 0; nhảy 2-4 đơn vị (OCR làm rơi
  dòng) phạt 1,5 cho mỗi bước thiếu; còn lại không hợp lệ.
- Trong một Điều sửa đổi, Khoản/Điểm của chính văn bản phải có động từ sửa đổi ("sửa đổi", "bổ sung",
  "bãi bỏ"...); không có thì nhiều khả năng là nội dung trích dẫn.
- "Điều 4b." xuất hiện khi đang trong phạm vi trích dẫn bị phạt thêm 3 (thường là điều được chèn, mất
  ngoặc kép do OCR).
- Dòng mà hai giả thuyết tốt nhất gần ngang điểm được đánh dấu `ambiguous` - chỗ dành cho LLM dự phòng
  (chưa bật).

## 3. Dữ liệu và cách chấm

| Tài liệu | Loại | Số dòng sau OCR |
|---|---|---|
| `12.pdf` | Nghị định sửa đổi nhiều nghị định, 88 trang, có trích dẫn lồng nhiều tầng | 3128 |
| `test02.pdf` | Trích đoạn văn bản sửa đổi, 5 trang | 186 |
| `test03.pdf` | Luật thường (Chương I-II, Điều 1-13), không có trích dẫn | 181 |
| `test04.pdf` | Công văn trả lời của Cục Thuế TP Hà Nội, 3 trang, trích dẫn nhiều thông tư | 88 |
| `test06.pdf` | Nghị định về tuyển dụng công chức, 10 trang (scan), Chương > Mục > Điều > Khoản > Điểm | 374 |

Cả hai phương pháp đọc **cùng một kết quả OCR**: `qwen_ocr.py` OCR bằng Qwen3.6-35B-A3B-FP8 (vLLM đã
triển khai), rồi chèn lớp chữ ẩn vào PDF tại `test_results/layout_grammar/data/pdf/<doc>.pdf` cho rule-based đọc. Chênh lệch
giữa hai phương pháp vì vậy đến từ cách phân tích, không phải từ đầu vào.

| Tài liệu | Số trang | Dòng OCR | Thời gian OCR |
|---|---|---|---|
| 12.pdf | 88 | 3446 | 272 giây |
| test02 | 5 | 204 | 32 giây |
| test03 | 5 | 201 | 330 giây (có trang phải chạy lại) |
| test04 | 3 | 96 | 23 giây (trang 2 chạy lại 2 lần) |
| test06 | 10 | 414 | 53 giây (6 trang phải chạy lại) |

**Không có ground truth.** Đánh giá là kiểm tính hợp lệ cấu trúc theo `nguyentac.md` bằng
`check_rules.py`, cùng một bộ tiêu chí cho mọi phương pháp:

- **Điều của văn bản tìm được**: so với dãy Điều 1..N liền mạch đọc từ lớp chữ PDF.
- **Vi phạm thứ bậc**: đơn vị nhỏ chứa đơn vị lớn hơn hoặc ngang cấp, trừ khi cha kết thúc "như sau:".
- **Lồng sửa đổi hợp lệ**: số lần nội dung trích dẫn được đặt đúng dưới lệnh sửa đổi.
- **Nhiễu**: tiêu đề không phải đơn vị cấu trúc nào.

## 4. Kết quả

### 12.pdf (văn bản sửa đổi, khó nhất)

| Chỉ số | Rule-based | **Văn phạm** |
|---|---|---|
| Số tiêu đề | 397 | 1512 |
| Điều của văn bản tìm được | 4/6 (thiếu Điều 2, 5) | **6/6** |
| Vi phạm thứ bậc | 0 | **0** |
| Lồng sửa đổi hợp lệ | 0 | **191** |
| Điều trích dẫn tách khỏi Điều của văn bản | 0 (không phân biệt) | **52/52** |
| Tiêu đề nhiễu | 5 | **0** |
| Thời gian phân tích (không tính OCR) | - | 3,4 giây |

Rule-based đặt toàn bộ 15 dòng bắt đầu bằng "Điều" ở cấp 1, gồm cả Điều 33-38 và Điều 4b là điều trích
dẫn bị mất ngoặc kép, nên dàn ý phẳng và sai nghĩa. 5 tiêu đề nhiễu là các câu bắt đầu bằng "Điều 26
Nghị định này..." (tham chiếu trong câu, không phải tiêu đề). Hai Điều 2 và 5 bị bỏ sót.

Parser văn phạm nhận đủ Điều 1-6, đặt 191 khối trích dẫn dưới đúng lệnh "... như sau:", và không có
tiêu đề nhiễu nào.

### test02.pdf (trích đoạn văn bản sửa đổi)

| Chỉ số | Rule-based | **Văn phạm** |
|---|---|---|
| Số tiêu đề | 21 | 61 |
| Điều của văn bản tìm được | 1/1 | 1/1 |
| Vi phạm thứ bậc | 0 | 0 |
| Lồng sửa đổi hợp lệ | 0 | **17** |

Cả hai tìm đúng Điều 1. Rule-based dừng ở 21 tiêu đề và không thấy cấu trúc bên trong các khoản sửa đổi;
parser văn phạm dựng thêm 15 đơn vị trích dẫn và 17 quan hệ lồng.

### test03.pdf (luật thường, không có trích dẫn)

| Chỉ số | Rule-based | **Văn phạm** |
|---|---|---|
| Số tiêu đề | 20 | 86 |
| Chương | 2/2 | 2/2 |
| Điều tìm được | 10/13 (thiếu Điều 7, 8, 13) | **13/13** |
| Khoản / Điểm | 7 / 1 | 51 / 20 |
| Vi phạm thứ bậc | 0 | 0 |
| Dòng mơ hồ | - | 0 |

Rule-based bỏ sót Điều 7, 8, 13 vì tên điều dài hơn ngưỡng 100 ký tự nên không được coi là tiêu đề.
Parser văn phạm không phụ thuộc độ dài, nhận theo ký hiệu và dãy số.

### test04.pdf (công văn, chạy ngày 07/10/2026)

| Chỉ số | Rule-based | Văn phạm |
|---|---|---|
| Số tiêu đề | 0 | 0 |
| Nhãn từng dòng | - | 88/88 dòng là thân bài |
| Dòng mơ hồ | - | 0 |

Cả hai phương pháp đều trả về dàn ý rỗng. Kết quả này đúng theo `nguyentac.md` nhưng không có ích:

- Công văn không có Điều, Khoản, Điểm của chính nó. Các dòng "1.", "a) b) c)", "2.18.", "1.1."... đều nằm
  trong phần trích dẫn thông tư (mở bằng ngoặc kép, hoặc nằm sau "- Căn cứ ... như sau:").
- Parser văn phạm chỉ mở phạm vi trích dẫn sau một Khoản/Điểm **của văn bản** kết thúc "như sau:", và chỉ
  nhận Khoản khi đã có Điều. Công văn không có Điều nào nên mọi dòng thành thân bài.
- Rule-based cũng không nhận "a) b) c)". Nhiều khả năng do các dòng này là câu dài, vượt ngưỡng độ dài
  tiêu đề; nguyên nhân này chưa kiểm riêng.

Bố cục hữu ích cho công văn là bố cục thể thức theo Nghị định 30: tên cơ quan, số và ký hiệu, trích yếu
("V/v vốn hóa chi phí lãi vay"), Kính gửi, các căn cứ ("- Căn cứ...", "+ Tại Điều 2..."), ý kiến trả lời,
Nơi nhận, khối chữ ký. Cả hai phương pháp đều chưa mô hình hoá bố cục này. Parser văn phạm cũng chưa nhận
đánh số nhiều cấp kiểu "2.18." và "1.1.".

### test06.pdf (nghị định có Mục, chạy ngày 07/10/2026)

| Chỉ số | Rule-based | **Văn phạm** |
|---|---|---|
| Số tiêu đề | 58 | 153 |
| Chương / Mục | 2/2 / 5/5 | 2/2 / 5/5 |
| Điều tìm được | 15/16 (thiếu Điều 1) | **16/16** |
| Khoản / Điểm | 17 / 19 | 48 / 82 |
| Vi phạm thứ bậc | **4** | **0** |
| Dòng mơ hồ | - | 0 |

Rule-based có 4 vi phạm thứ bậc, ví dụ "Điểm (L4) chứa Mục (L5)": Mục 1 xuất hiện sau Điều, Khoản, Điểm
của Chương I nên bị gán cấp 5 (cấp theo thứ tự xuất hiện của kiểu ký hiệu, không theo nghĩa). Điều 1 bị
sót vì PDF gộp dòng tên điều với câu nội dung thành một khối kết thúc bằng dấu chấm. Tên Chương chỉ còn
"Chương I", mất phần "QUY ĐỊNH CHUNG" nằm ở dòng sau.

Parser văn phạm dựng đúng Chương > Mục > Điều > Khoản > Điểm. Điểm còn chưa đẹp: Chương I không có Mục nên
Điều 1-2 nằm ở cấp 3, bỏ trống cấp 2 (cấp gán cố định theo loại đơn vị).

### Ví dụ trực quan

Trích từ `test_results/layout_grammar/compare/<doc>/outline_{rule_based,grammar}.md`. `Lx` là cấp trong
dàn ý, `tr.` là trang; nội dung dòng cắt ngắn; dòng `# ...` là ghi chú thêm khi viết review.

**Ví dụ 1 - 12.pdf, lệnh sửa đổi và nội dung trích dẫn (trang 2-3).** Rule-based thấy khoản "2. Xử phạt
vi phạm hành chính..." của Điều 4a được chèn và coi nó là khoản 2 của chính Điều 1; nội dung trích dẫn
còn lại biến mất. Parser văn phạm đặt từng khối trích dẫn dưới đúng lệnh "... như sau:".

```text
Rule-based                                         Parser văn phạm
L1 Điều 1. Sửa đổi, bổ sung ... NĐ 71/2019         L1 Điều 1. Sửa đổi, bổ sung ... NĐ 71/2019
  L2 1. Sửa đổi, bổ sung điểm a khoản 2 Điều 2       L2 1. Sửa đổi, bổ sung điểm a khoản 2 Điều 2 như sau:
  L2 2. Bổ sung điểm m ... Điều 3 như sau:             L3 "a) Tổ chức kinh tế được thành lập ...
  L2 2. Xử phạt vi phạm hành chính ... nhiều lần     L2 2. Bổ sung điểm m ... Điều 3 như sau:
     # khoản 2 của Điều 4a trích dẫn,                  L3 "m) Buộc nộp lại Giấy chứng nhận ...
     # bị hiểu là khoản 2 của Điều 1                 L2 3. Bổ sung Điều 4a và Điều 4b ... như sau:
     # "3. Bổ sung Điều 4a ..." bị mất                 L3 "Điều 4a. Quy định về vi phạm hành chính ...
  L2 4. Sửa đổi ... một số khoản của Điều 5              L4 1. Hành vi vi phạm đã kết thúc ...
                                                         L4 2. Xử phạt ... nhiều lần
                                                           L5 a) Cá nhân, tổ chức vi phạm ...
                                                           L5 b) Cá nhân, tổ chức vi phạm ...
                                                       L3 Điều 4b. Thi hành các hình thức xử phạt ...
                                                   L2 4. Sửa đổi ... một số khoản của Điều 5 như sau:
```

**Ví dụ 2 - 12.pdf, thay cả một Chương (trang 47).** Khoản 38 thay toàn bộ Chương III của nghị định cũ.
Rule-based đưa Điều 33-38 trích dẫn lên ngang hàng với Điều 1-6 của văn bản, nên dàn ý có "Điều 1, Điều
33, ..., Điều 38, Điều 3". Parser văn phạm giữ chúng trong phạm vi trích dẫn.

```text
Rule-based                                         Parser văn phạm
L1 Điều 33. Thẩm quyền lập biên bản ...            L2 38. Sửa đổi, bổ sung Chương III như sau:
L1 Điều 34. Thẩm quyền xử phạt của Chủ tịch UBND     L3 "Chương III
L1 Điều 35. Thẩm quyền xử phạt ... của Thanh tra       L4 Điều 33. Thẩm quyền lập biên bản ...
L1 Điều 26 Nghị định này trong phạm vi quản lý:          L5 1. Người có thẩm quyền xử phạt ...
   # câu tham chiếu, bị nhận là tiêu đề                  L5 2. Người thuộc lực lượng quân đội ...
L1 Điều 36. ...                                        L4 Điều 34. Thẩm quyền xử phạt của Chủ tịch UBND
L1 Điều 3. Sửa đổi, bổ sung ... NĐ 98/2020                L5 1. Chủ tịch Ủy ban nhân dân cấp xã ...
```

**Ví dụ 3 - test03, tên điều dài (trang 3-4).** Rule-based bỏ Điều 7 và Điều 8 vì tên dài hơn 100 ký tự,
nên khoản "4. Ban hành văn bản ... thay thế" của Điều 8 bị gắn nhầm vào Điều 6.

```text
Rule-based                                         Parser văn phạm
L2 Điều 6. Phản biện xã hội, tham vấn, góp ý ...   L2 Điều 6. Phản biện xã hội, tham vấn, góp ý ...
  L3 4. Ban hành văn bản QPPL thay thế ...         L2 Điều 7. Ngôn ngữ, thể thức, kỹ thuật trình bày ...
     # thực ra là khoản 4 của Điều 8               L2 Điều 8. Sửa đổi, bổ sung, thay thế, bãi bỏ ...
L2 Điều 9. Gửi, lưu trữ văn bản QPPL                 L3 1. ... 3. (khoản 1-3)
                                                     L3 4. Ban hành văn bản QPPL thay thế ...
                                                       L4 a) Thay đổi cơ bản chính sách ...
                                                       L4 b) Sửa đổi, bổ sung ... quá một phần hai
                                                     L3 5. ... 6. (khoản 5-6)
                                                   L2 Điều 9. Gửi, lưu trữ văn bản QPPL
```

**Ví dụ 4 - test06, cấp của Mục (trang 1-2).** Rule-based gán cấp theo thứ tự gặp kiểu ký hiệu: Mục
xuất hiện sau Điều, Khoản, Điểm nên thành cấp 5, nằm dưới một Điểm. Parser văn phạm gán cấp theo loại
đơn vị.

```text
Rule-based                                         Parser văn phạm
L1 Chương I                                        L1 Chương I QUY ĐỊNH CHUNG
  L2 Điều 2. Đối tượng áp dụng                         L3 Điều 1. Phạm vi điều chỉnh
     # Điều 1 bị gộp với câu nội dung, mất             L3 Điều 2. Đối tượng áp dụng
    L3 2. Cơ quan quản lý công chức, bao gồm:            L4 2. Cơ quan quản lý công chức, bao gồm:
      L4 a) Văn phòng Trung ương Đảng ...                  L5 a) ... g) (7 điểm)
      L4 e) Tổ chức do Chính phủ ...               L1 Chương II TUYỂN DỤNG CÔNG CHỨC
L1 Chương II                                         L2 Mục 1. CĂN CỨ, THẨM QUYỀN, HỘI ĐỒNG TUYỂN DỤNG
        L5 Mục 1. CĂN CỨ, THẨM QUYỀN, ...                L3 Điều 3. Căn cứ tuyển dụng công chức
  L2 Điều 3. Căn cứ tuyển dụng công chức
```

**Ví dụ 5 - lỗi khi OCR mất cả trang (12.pdf, trang 3-5).** Trang 4 không có dòng OCR nào (Qwen trả về
toàn dòng không hợp lệ, chạy lại vẫn hỏng), nên sau "b) Bổ sung khoản 4a ... như sau:" ở cuối trang 3 là
thẳng "đ) Sửa đổi, bổ sung khoản 8 như sau:" ở trang 5. Bước nhảy b) sang đ) vẫn trong mức cho phép, nhưng
phương án "nội dung trích dẫn" rẻ hơn, nên đ) bị lồng xuống cấp 4 dưới b), thay vì là điểm cấp 3 của khoản
4. Rule-based cũng mất toàn bộ nội dung trang 4. Lỗi gốc ở OCR; parser chỉ khuếch đại nó.

```text
Parser văn phạm (hiện tại)                         Đúng ra
L2 4. Sửa đổi ... một số khoản của Điều 5          L2 4. Sửa đổi ... một số khoản của Điều 5
  L3 a) Sửa đổi khoản 3 như sau:                     L3 a) Sửa đổi khoản 3 như sau:
  L3 b) Bổ sung khoản 4a vào sau khoản 4 ...         L3 b) Bổ sung khoản 4a vào sau khoản 4 ...
    L4 đ) Sửa đổi, bổ sung khoản 8 như sau:          L3 đ) Sửa đổi, bổ sung khoản 8 như sau:
    L4 "8. Biện pháp khắc phục hậu quả:                L4 "8. Biện pháp khắc phục hậu quả:
```

## 5. Nhận xét

**Điểm mạnh của parser văn phạm**

- Đúng hơn rõ rệt ở chỗ rule-based yếu nhất: văn bản sửa đổi (6/6 so với 4/6 Điều, 191 so với 0 quan
  hệ lồng) và tên điều dài (13/13 so với 10/13).
- Không làm hỏng văn bản thường: test03 không có vi phạm, không có dòng mơ hồ.
- Tất định, nhanh (3,4 giây cho 3128 dòng), không gọi LLM ở bước phân tích, nên kết quả lặp lại được và
  không tốn GPU.
- Mỗi quyết định có chi phí giải thích được; dòng mơ hồ được liệt kê riêng thay vì đoán âm thầm.
- Vì đã biết dòng nào là trích dẫn, có thể bật lại tiêu chí đánh số liên tục mà không báo oan kiểu
  "Điều 1 nhảy sang Điều 33".

**Hạn chế còn lại**

- **Một Điều sai trên 12.pdf**: danh sách Điều của văn bản có 7 mục, thừa "Điều 4b. Thi hành các hình thức
  xử phạt..." - thực chất là điều trích dẫn. Chỉ số 6/6 không phản ánh lỗi này vì `check_rules.py` chỉ
  đếm Điều không hậu tố.
- **Dòng mơ hồ nhiều trên văn bản sửa đổi**: 316/3128 dòng ở 12.pdf, 18/186 ở test02. Phần lớn là lệnh
  sửa đổi ("a) Sửa đổi khoản 3 như sau:") mà nhãn thắng vẫn đúng, chênh lệch chỉ 0,5; nhưng có dòng
  chênh 0 (vd. "b) Bãi bỏ khoản 3;" bị gán QUOTED). Đây là chỗ nên thêm LLM dự phòng.
- **"Cùng đơn vị khác cấp"** trên 12.pdf (Điều ở cấp 1, 3, 4) là do điều trích dẫn mất dấu ngoặc kép khi
  OCR nên bộ chấm không nhận ra; về cấu trúc thì chúng đúng là nằm sâu dưới lệnh sửa đổi.
- **OCR mất trang**: 12.pdf có 3/88 trang (4, 41, 74) không có dòng chữ nào sau khi đã chạy lại. Cả hai
  phương pháp đều mất nội dung các trang này; với parser văn phạm, dãy số bị đứt còn có thể làm lồng sai
  các dòng ngay sau đó (ví dụ 5).
- **Chỉ áp dụng cho văn bản quy phạm** (luật, nghị định, thông tư). Với công văn (test04) parser trả về
  dàn ý rỗng, giống rule-based; cần thêm một văn phạm riêng cho thể thức công văn.
- **Chưa có ground truth**: các chỉ số trên là tính hợp lệ theo `nguyentac.md`, không phải độ chính xác
  so với nhãn người gán. Bộ dữ liệu mới có 5 tài liệu.
- **OCR là phần tốn thời gian nhất** (23-330 giây cho mỗi tài liệu, so với dưới 4 giây cho phân tích).
  Với PDF đã có lớp chữ thì nên thử parser văn phạm trên chính lớp chữ đó để bỏ bước OCR.


## 6. Vị trí file (trong `backend/`)

| Nội dung | Đường dẫn |
|---|---|
| Script (OCR, parser văn phạm, rule-based, bộ chấm) | [tests/layout_experiment/](tests/layout_experiment/), cách chạy ở README trong đó |
| Quy tắc chấm | [tests/layout_experiment/nguyentac.md](tests/layout_experiment/nguyentac.md) |
| PDF đầu vào | [tests/fixtures/layout_pdf/](tests/fixtures/layout_pdf/) (12, test02, test03, test04, test06) |
| Dàn ý từng phương pháp | `test_results/layout_grammar/compare/<doc>/outline_{rule_based,grammar}.md` |
| Kết quả chấm | `test_results/layout_grammar/compare/<doc>/rules_check.{txt,json}` |
| Nhãn từng dòng, thống kê, dòng mơ hồ | `test_results/layout_grammar/grammar/<doc>/{lines.json,outline.json,stats.json}` |
| OCR đầu vào | `test_results/layout_grammar/ocr/<doc>/pages.json`, PDF có lớp chữ ở `test_results/layout_grammar/data/pdf/` |
| Log chạy test04 | `test_results/layout_grammar/logs/test04.log` |

Bản gốc vẫn còn trên máy chủ ở `~/loi_workspace/layout_testing/` (OCR cần vLLM chạy ở đó).

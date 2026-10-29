# Thực nghiệm trích xuất dàn ý: rule-based và parser văn phạm

Script chạy tay, không phải test pytest (tên file không bắt đầu bằng `test_`). Kết quả so sánh nằm ở
[REVIEW-TRICH-XUAT-LAYOUT.md](../../REVIEW-TRICH-XUAT-LAYOUT.md).

| File | Việc |
|---|---|
| `qwen_ocr.py` | OCR PDF bằng Qwen trên vLLM (`--endpoint`, mặc định `http://127.0.0.1:15000/v1`), chèn lớp chữ ẩn vào PDF |
| `grammar_parser.py` | Parser văn phạm (mới), đọc kết quả OCR |
| `compare_methods.py` | Dựng dàn ý rule-based bằng `app.documents.outline.build_outline`, so mô tả với parser văn phạm |
| `check_rules.py` | Chấm cả hai theo `nguyentac.md` |
| `nguyentac.md` | Quy tắc bố cục văn bản dùng để chấm |

## Dữ liệu

PDF đầu vào nằm ở `tests/fixtures/layout_pdf/`. Cả 5 tệp đều tải từ trên mạng
PDF", **không có lớp chữ** (mỗi trang là ảnh), nên phải OCR trước. Mỗi trang có dòng rác của trình duyệt
(ngày giờ in, `about:blank`, số trang `n/N`); `grammar_parser.py` lọc bỏ các dòng này.

| Tệp | Loại văn bản | Trang | Dòng OCR | Dòng sau lọc rác | Ghi chú |
|---|---|---|---|---|---|
| `12.pdf` | Nghị định sửa đổi, bổ sung nhiều nghị định (lĩnh vực Công Thương) | 88 | 3446 | 3128 | Trích dẫn lồng nhiều tầng, có thay cả Chương III; trang 4, 41, 74 OCR hỏng (0 dòng) |
| `test02.pdf` | Trích đoạn văn bản sửa đổi (Điều 1 của nghị định trên) | 5 | 204 | 186 | |
| `test03.pdf` | Luật thường: Chương I-II, Điều 1-13 | 5 | 201 | 181 | Không có trích dẫn; nhiều tên điều dài hơn 100 ký tự |
| `test04.pdf` | Công văn trả lời của Cục Thuế TP Hà Nội | 3 | 96 | 88 | Không có Điều/Khoản của chính văn bản, chỉ trích dẫn thông tư |
| `test06.pdf` | Nghị định về tuyển dụng công chức | 10 | 414 | 374 | Đủ Chương > Mục > Điều > Khoản > Điểm |

Không có ground truth: kết quả được chấm theo tính hợp lệ cấu trúc trong `nguyentac.md`, không phải so với
nhãn người gán.

Kết quả nằm ở `test_results/layout_grammar/` (đổi bằng biến môi trường `LAYOUT_WORK`):

| Thư mục | Nội dung |
|---|---|
| `ocr/<doc>/` | `pages.json` (từng dòng kèm bbox), `ocr_stats.json` (trang chạy lại, trang hỏng, token, thời gian) |
| `data/pdf/<doc>.pdf` | PDF gốc đã chèn lớp chữ ẩn từ OCR - đầu vào của rule-based |
| `grammar/<doc>/` | `lines.json` (nhãn từng dòng), `outline.json`, `outline_grammar.md`, `stats.json` (gồm dòng mơ hồ) |
| `compare/<doc>/` | `outline_rule_based.md`, `outline_grammar.md`, `comparison.{txt,json}`, `rules_check.{txt,json}` |
| `logs/` | Log các lần chạy trên server |

Chạy từ thư mục `backend/`. Trên Windows đặt `PYTHONIOENCODING=utf-8` để in được tiếng Việt.

```bash
# OCR cần vLLM đang chạy; bỏ qua nếu test_results/layout_grammar/ocr/<doc> đã có
uv run python tests/layout_experiment/qwen_ocr.py --pdf tests/fixtures/layout_pdf/test04.pdf

uv run python tests/layout_experiment/compare_methods.py --doc test04   # chạy trước: tạo compare/<doc>
uv run python tests/layout_experiment/grammar_parser.py --doc test04
uv run python tests/layout_experiment/check_rules.py --doc test04
```

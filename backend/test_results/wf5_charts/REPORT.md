# WF5: biểu đồ thống nhất bằng matplotlib + seaborn - kiểm thử 10 mẫu (12/10/2026)

## Thay đổi

Trước: hai đường dựng slide của workflow 5 vẽ biểu đồ bằng hai công cụ khác nhau.

| Đường | Trước | Sau |
|---|---|---|
| Presenton dựng thẳng (mặc định) | renderer của Presenton tự vẽ phần tử `chart`; .pptx xuất ra là ảnh chụp, màu theo theme, ẩn bớt nhãn trục khi nhiều danh mục | PNG do `app.documents.charts.chart_for` vẽ, tải lên `POST /api/v1/ppt/images/upload`, thay ô `chart` của layout bằng ô `image` cùng vị trí/kích thước |
| Đường lùi `pptx_builder` | biểu đồ chỉ tiêu WF4 (matplotlib) gắn vào slide `summary` - nhưng `_render_summary` **không vẽ** `spec.chart`, nên thực tế không có biểu đồ nào; truy vấn động thì không có biểu đồ | slide `chart` cho từng bộ dữ liệu, cùng hàm `chart_for`, cùng bộ dữ liệu `slide_deck.build_datasets` |

Chuẩn chung (`app/documents/charts.py`): seaborn `whitegrid` + `paper` lo phần trình bày, cột vẽ bằng matplotlib
theo vị trí (`sns.barplot` gộp/lấy trung bình danh mục trùng tên), font DejaVu Sans (máy nào cũng có, đủ
tiếng Việt), bảng màu `slide_deck.PALETTE` (cũng là màu `graph_*` của theme Presenton), số kiểu Việt Nam
(1.234 / 31,3), nhãn số trên từng cột, trục chỉ vạch nguyên khi là số đếm, ảnh vẽ đúng tỷ lệ ô của layout.
Biểu đồ WF4 (`compare_bar_chart`, `status_bar_chart`, `trend_line_chart`) dùng cùng chuẩn.

Phụ thuộc mới: `seaborn>=0.13.2` (pandas, matplotlib đã có).

## Kiểm thử 10 mẫu

`eval_charts.py` + `run_eval.sh`, chạy trên server, bản sao mã nguồn ở `~/loi_workspace/wf5_chart_test`
(không đụng backend production; seaborn cài vào thư mục riêng `pydeps`). 10 câu hỏi của `report_pipeline`,
số liệu SQL thật từ ERP (login chỉ đọc), đưa vào backend đúng dạng `data["queries"]` của Text-to-SQL.
Template `momentum`, Presenton `127.0.0.1:5002`. vLLM không chạy lúc thử nên `PRESENTON_SUMMARY=false`
(chữ nhận xét do code viết - không ảnh hưởng biểu đồ).

Mỗi câu dựng cả hai đường và đo:

- **deck_check**: deck Presenton đọc lại có đúng ảnh biểu đồ đã vẽ ở mọi slide biểu đồ, không còn ô `chart`;
- **ảnh trong .pptx**: mỗi slide biểu đồ của file xuất ra có ảnh;
- **verify_node**: đối chiếu mọi con số trong file .pptx với dữ liệu gốc (bước có sẵn của backend);
- **số trên biểu đồ vs SQL**: tra từng danh mục theo NHÃN trong các dòng SQL gốc - độc lập với code dựng biểu đồ;
- **đường lùi cùng hàm vẽ**: ảnh trong file đường lùi trùng byte với `chart_for` của cùng bộ dữ liệu;
- xem bằng mắt PNG của toàn bộ 86 slide Presenton và 17 biểu đồ.

### Kết quả lượt cuối (r2)

| Câu | Bộ dữ liệu | Dòng SQL | Presenton: slide / deck_check / ảnh trên slide / đối chiếu số / giây | Đường lùi: slide / ảnh cùng hàm / đối chiếu số | Sai số biểu đồ vs SQL |
|---|---|---|---|---|---|
| Q01 | 2 | 9, 6 | 9 / đạt / 2/2 / passed / 14,2 | 6 / 2/2 / passed | 0 |
| Q02 | 1 | 9 | 9 / đạt / 1/1 / passed / 14,3 | 4 / 1/1 / passed | 0 |
| Q03 | 2 | 3, 12 | 9 / đạt / 2/2 / passed / 15,1 | 6 / 2/2 / passed | 0 |
| Q04 | 2 | 9, 8 | 10 / đạt / 2/2 / passed / 16,3 | 7 / 2/2 / passed | 0 |
| Q05 | 2 | 2, 3 | 8 / đạt / 2/2 / passed / 13,8 | 5 / 2/2 / passed | 0 |
| Q06 | 2 | 12, 12 | 11 / đạt / 2/2 / passed / 17,8 | 7 / 2/2 / passed | 0 |
| Q07 | 1 | 12 | 7 / đạt / 1/1 / passed / 13,7 | 4 / 1/1 / passed | 0 |
| Q08 | 2 | 5, 5 | 9 / đạt / 2/2 / passed / 15,5 | 5 / 2/2 / passed | 0 |
| Q09 | 1 | 4 | 6 / đạt / 1/1 / passed / 12,7 | 3 / 1/1 / passed | 0 |
| Q10 | 2 | 2, 4 | 8 / đạt / 2/2 / passed / 13,7 | 5 / 2/2 / passed | 0 |

10/10 câu đạt ở cả hai đường; 17/17 biểu đồ đúng số SQL; 17/17 slide biểu đồ của file Presenton có ảnh;
17/17 ảnh đường lùi trùng hàm vẽ. Thời gian đường Presenton 12,7-17,8 s/deck (vẽ + tải ảnh 0,2 s, phần
lớn là bước export của Presenton).

### Lỗi tìm ra khi thử và đã sửa

1. **Đường lùi báo số lạ** (lượt pilot): chú thích biểu đồ "Tổng số nhân sự là 14 người, trên 9 phòng ban" là
   số code tính, nhưng `_render_local` không khai `so_cua_code` -> `verify_node` cảnh báo nhầm. Đã khai.
2. **Chuỗi vẽ sau bị cắt mép** (lượt r1, xem bằng mắt - kiểm số không bắt được vì dữ liệu đúng, chỉ hình sai):
   Q04 "Cộng dồn", Q06 "Số lượng thiết bị". Nguyên nhân: giới hạn trục đặt sau chuỗi đầu tiên làm tắt tự co
   giãn. `compare_bar_chart` của WF4 cùng lỗi. Đã sửa (`_headroom` gọi một lần sau mọi chuỗi) + test hồi quy.
3. **Trục kỳ bị xếp theo giá trị** (r1): Q04 "Nhân sự theo tháng" ra T9, T8, T5... - mất xu hướng. Nay cột nhóm
   là năm/tháng/quý/kỳ thì giữ thứ tự truy vấn, cột đứng, không gộp "khác"; nhận xét cao/thấp nhất vẫn theo giá trị.
4. **Vạch trục 0,5 người** (pilot): số đếm nay chỉ vạch nguyên.
5. Biểu đồ nhiều chuỗi: không ghi nhãn "0" (Q02 có hàng chục nhãn 0 chồng nhau); ô thấp nhiều danh mục thì
   chữ nhãn nhỏ lại, ngắt dòng rộng hơn.

### Còn lại (ngoài phạm vi biểu đồ)

- Một số nhãn cột/tiêu đề slide còn tên trường (`chung_loai`, `mahocvan`, `cuoi_thang_8`, `dang_cap_phat`):
  do bộ thử đặt nhãn bằng từ điển `nhan_tu_ten`, không có Qwen. Luồng thật gọi `nhan_cot` có Qwen đặt nhãn.
- Q07 có "Còn lại" = -1 (Router, Switch: cấp phát nhiều hơn tổng) - dữ liệu ERP, biểu đồ vẽ đúng số âm.
- Ảnh biểu đồ tải lên Presenton không bị xoá khi xoá deck (Presenton không có liên kết ảnh-deck); mỗi
  deck thêm 1-2 PNG ~20-70 KB.
- Chưa thử với vLLM bật (chữ nhận xét Qwen) - không đổi phần biểu đồ, nhưng nên chạy một lượt khi
  triển khai.
- Test toàn bộ backend (Windows): 830 đạt; 3 failed + 24 error ở `test_erp_discovery`/`test_conversation_store`/
  `test_erp_groups` và segfault ở `test_converter` - cùng kết quả trên HEAD gốc (lỗi mã hoá Windows, pymupdf),
  không do thay đổi này.

## Sửa 3 lỗi chữ tiếng Việt / tên trường / file không mở được trên Windows (mẫu `output/ERROR.pptx`)

ERROR.pptx do bản production cũ (8434523) sinh ra; mỗi lỗi có một nguyên nhân riêng:

| Lỗi | Nguyên nhân (đã kiểm chứng) | Sửa |
|---|---|---|
| #1 tên trường CSDL trên slide ("soluong", "thangnam", "loaichuyendong") | Nhãn cột chỉ do `gan_nhan_cot` đặt ở bước lấy số liệu; dữ liệu tới đường khác, hoặc nhãn lỗi, thì `query_result.table` lùi về `c.label or c.name` = tên trường | `nhan_cot.lam_sach_ket_qua`: chốt chặn không gọi LLM, chạy ở mọi cửa ra (Word, slide Presenton, slide tự dựng) - nhãn không dấu/tên trường -> từ điển -> "Cột n"; từ điển thêm từ ghép qua "_" ("tinh_trang" -> "Tình trạng"), token số ("cuoi_thang_8" -> "Cuối tháng 8"); bỏ "Tổng tổng" |
| #2 biểu đồ/bảng lỗi chữ ("T?n cu?i k?", "R?i kh?i") | Text-to-SQL viết chuỗi literal thiếu `N''`; DB collation `SQL_Latin1_General_CP1_CI_AS` ép về varchar CP1252: ồ, ố, ờ, ỏ thành `?` (chạy thật: `'Tồn cuối kỳ'` -> `T?n cu?i k?`). Tệ hơn: `LIKE '%đ%'` thiếu N trả 0 dòng - **số liệu sai không báo** | `erp_sql_policy._chuoi_unicode`: mọi chuỗi literal có ký tự ngoài ASCII thành `N'...'` trước khi chạy (kiểm trên SQL Server thật: cả hai trường hợp ra đúng). Thêm NFC cho mọi chữ từ dữ liệu (Unikey "Unicode tổ hợp"/macOS cho chữ NFD, dấu tách khỏi chữ trên biểu đồ) |
| #3 PowerPoint trên Windows không hiển thị/đòi sửa file | Presenton ghi icon/nền/đường kẻ thành SVG thuần: `<a:blip>` không có `r:embed`, chỉ có `svgBlip` trong extLst (ERROR.pptx: 18 ảnh như vậy). Chuẩn Office cần ảnh raster ở blip | `pptx_svg.them_png_du_phong`: vẽ SVG thành PNG (PyMuPDF) gắn vào `r:embed`, giữ SVG cho bản đọc được; chạy sau mỗi lần tải .pptx từ Presenton (0,6 s/file) |

Thêm cảnh báo trên file thành phẩm (`verify_node`): `lost_diacritics` ("T?n", ký tự U+FFFD) và
`field_name` - nếu còn lọt thì kết quả nói rõ slide nào.

Biểu đồ: chú thích nhiều chuỗi đo bề ngang thật, tràn thì xuống hàng; nhãn số co theo độ dày cột, cột
quá mỏng thì bỏ nhãn (bảng chi tiết ngay sau có đủ số).

### Kết quả lượt r5 (sau sửa) so với r2 (trước sửa)

| Chỉ số trên 20 file .pptx (10 Presenton + 10 tự dựng), 1.484 đoạn chữ | r2 | r5 |
|---|---|---|
| Đoạn chứa tên cột gốc của chính truy vấn | 41 | 0 |
| Đoạn dạng tên trường (`la_ten_truong`) | 49 | 0 |
| Nhãn dự phòng "Cột n" | - | 0 |
| Chữ mất dấu / NFD | 0 / 0 | 0 / 0 |
| Ảnh SVG không có PNG dự phòng (file Presenton) | chưa đo | 0 |
| Câu đạt (deck, ảnh biểu đồ, đối chiếu số) | 10/10 | 10/10 |
| Biểu đồ khớp SQL | 17/17 | 17/17 |

`scan_text.py` quét chữ, `check_nprefix.py` kiểm `N''` trên SQL Server (`CHECK=nprefix ./run_eval.sh`).
Test mới: `tests/test_chu_viet.py`.

## Chạy lại

```bash
# trên server, sau khi chép backend/app + test_results/wf5_charts vào ~/loi_workspace/wf5_chart_test/backend
WORK=~/loi_workspace/wf5_chart_test ~/loi_workspace/wf5_chart_test/backend/test_results/wf5_charts/run_eval.sh --out ../runs/rN
python3 backend/test_results/wf5_charts/summarize.py runs/rN      # trong ~/loi_workspace/wf5_chart_test
```

Kết quả: `~/loi_workspace/wf5_chart_test/runs/r2/<Qxx>/` (data.json, chart_bang_N.png, presenton.pptx,
local.pptx, presenton_slide_NN.png, result.json) và `runs/r2/results.json`.

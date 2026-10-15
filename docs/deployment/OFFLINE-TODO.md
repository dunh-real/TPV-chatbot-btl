# Checklist chuẩn bị bàn giao offline

Hướng dẫn và mẫu service: [OFFLINE-SYSTEMD.md](OFFLINE-SYSTEMD.md).
Các mục chưa đánh dấu là việc cần thực hiện, không phải tính năng đã hoàn tất.

## P0 — Chốt trước khi đóng gói

- [ ] Xác nhận Linux/version, CPU architecture, kernel, GPU/VRAM, RAM và SSD của khách.
- [ ] Xác nhận Docker được giữ cho hạ tầng hay phải bỏ hoàn toàn.
- [ ] Chốt commit source, Python, vLLM, Torch/CUDA, driver và model revision đã thử.
- [ ] Chốt model dùng chung chat/ERP/OCR hay tách model OCR; thử parser tool-calling.
- [ ] Chốt SQL Server trong LAN, DB account chỉ đọc, CA/certificate và chính sách tenant.
- [ ] Xác nhận tài khoản/vai trò khách; bỏ mapping demo và ngoại lệ `64:153` nếu không được yêu cầu.
- [ ] Chốt xác thực người dùng thật; không đưa bộ chọn tài khoản/header demo vào production như cơ chế đăng nhập.
- [ ] Xác nhận MinIO có được dùng trong bản tích hợp thật; code hiện lưu file local.

## P0 — Tải và đóng gói trên máy có Internet

- [ ] Gói OS + dependency, Python/venv, ODBC 18, driver GPU và công cụ vận hành.
- [ ] Hai wheelhouse độc lập backend/vLLM; thử cài mới với `--no-index` và `pip check`.
- [ ] Model Qwen đầy đủ, Vietnamese_Embedding, Vietnamese_Reranker và sparse_linear.pt.
- [ ] Model OCR riêng nếu có; kiểm tra tokenizer/processor và tất cả shard.
- [ ] Font, mẫu DOCX/PPTX, LibreOffice nếu đọc `.doc`, assets của Presenton nếu dùng.
- [ ] Image Qdrant/Redis/Presenton/MinIO theo version/digest nếu giữ Docker.
- [ ] Source backend + frontend, cấu hình mẫu, license của phần mềm/model/font đi kèm.
- [ ] Manifest và SHA256 của toàn bộ bundle; không kèm secrets hoặc dữ liệu demo nhạy cảm.

## P0 — Cấu hình và công việc triển khai còn thiếu

- [ ] Tạo script build bundle lặp lại được: export lock, tải wheel/model/image, tạo manifest/hash.
- [ ] Đưa hai unit mẫu thành file `.service` thật theo đường dẫn/máy khách.
- [ ] Viết installer offline: kiểm tra điều kiện, user/quyền thư mục, cài gói/venv, cài unit.
- [ ] Tạo cấu hình hạ tầng riêng: publish Redis loopback, volumes, restart policy và readiness.
- [ ] Nếu bỏ Docker: đóng gói service native Qdrant/Redis/MinIO; đánh giá riêng Presenton.
- [ ] Bổ sung kiểm tra readiness model/hạ tầng có timeout; `After=` không đủ.
- [ ] Rà soát mọi URL/CDN/telemetry; thay bằng LAN hoặc local assets.
- [ ] Tắt auto-download; xác nhận cache sparse có refs/blobs đúng và user service đọc được.
- [ ] Chốt context/concurrency/GPU allocation; schema đầy đủ 91 bảng có thể vượt mẫu 32K token.
- [ ] Chốt nơi lưu app DB/upload/output và quy trình chuyển dữ liệu từ container cũ.

## P0 — Nghiệm thu trước khi mang sang khách

- [ ] Cài trên máy sạch tương đương máy khách, chỉ dùng bundle.
- [ ] Chặn Internet thật và reboot; không cần pip/HF/Docker pull lúc chạy.
- [ ] Chat, discovery/repair, quyền nhóm và tenant đạt kiểm tra.
- [ ] Ingest/RAG, sparse retrieval, rerank, OCR, Word và PowerPoint chạy đủ.
- [ ] Font/biểu đồ/tiếng Việt đúng trên máy người dùng; `.doc` chuyển được nếu hỗ trợ.
- [ ] Kiểm tra tải đồng thời, hết VRAM, timeout và service tự phục hồi.
- [ ] Ghi version, kết quả test, thời gian khởi động và mức RAM/VRAM/SSD thực tế.

## P1 — Vận hành lâu dài

- [ ] Backup và thử restore app DB, file local, Qdrant snapshot, MinIO, Presenton, Redis nếu cần.
- [ ] Log rotation, giới hạn dung lượng journal/cache/output, cảnh báo đầy đĩa và lỗi GPU.
- [ ] Firewall LAN, TLS/certificate nội bộ, phân quyền đọc secrets và tài khoản vận hành.
- [ ] Tài liệu cập nhật offline và rollback source + môi trường + dữ liệu tương thích.
- [ ] Bàn giao người phụ trách, lệnh systemctl/journalctl, danh sách cổng và cách xử lý sự cố.

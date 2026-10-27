# Bản chạy thử trên server

- Server: `abc@100.105.11.124`.
- Dự án: `/home/abc/yuu_workspace/chatbot/TPV-chatbot-btl`.
- Backend: `127.0.0.1:8082`, chạy trong tmux `tpv-discovery-test`.
- Giao diện trên Mac: http://127.0.0.1:8082/ui/ (đã mở SSH chuyển tiếp cổng).
- Swagger: http://127.0.0.1:8082/docs.
- Log: `backend/data/server.log`.
- Kết quả thử API agent: `backend/data/deploy_smoke_agent.json` trên server.

Bản thử dùng `.venv`, SQLite và hai collection Qdrant riêng. Qwen dùng endpoint
đang có `http://127.0.0.1:15000/v1`. Warm-up tắt; embedding/reranker chạy CPU khi
cần. Các dịch vụ đang có trên cổng 8000, 8001 và 8080 được giữ nguyên.

DB ERP là `bqp_private_ai_assistant`, truy cập qua `127.0.0.1:14333` trên server.
Cổng này hiện đã có tunnel hoạt động tới SQL Server trên Mac. Giữ Mac và tunnel
DB hoạt động khi thử truy vấn ERP. Không chép mật khẩu vào tài liệu này.

Mở lại chuyển tiếp giao diện nếu mất kết nối, chạy trên Mac:

```bash
ssh -N -L 127.0.0.1:8082:127.0.0.1:8082 abc@100.105.11.124
```

Nếu tunnel DB ngắt và cổng server 14333 đã trống, chạy trên Mac:

```bash
ssh -N -o ExitOnForwardFailure=yes -R 127.0.0.1:14333:127.0.0.1:14333 abc@100.105.11.124
```

Xem log trên server:

```bash
tail -f /home/abc/yuu_workspace/chatbot/TPV-chatbot-btl/backend/data/server.log
```

Khởi động lại riêng bản thử, chạy trên server:

```bash
tmux kill-session -t tpv-discovery-test
tmux new-session -d -s tpv-discovery-test -c /home/abc/yuu_workspace/chatbot/TPV-chatbot-btl/backend '.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8082 >data/server.log 2>&1'
```

Đã xác nhận: 273 kiểm thử đạt trên server; health `ok`; UI HTTP 200; API agent
thực sự gọi `query_erp`, Qwen sinh SQL và executor chạy thành công trên ERP.
Trong giao diện, chọn tài khoản demo có quyền (chẳng hạn Admin), rồi nhập câu hỏi
thống kê. Chưa đánh giá độ chính xác Qwen trên toàn bộ bộ câu hỏi nghiệp vụ.

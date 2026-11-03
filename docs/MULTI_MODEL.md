# Chọn model chat trên yuu/chatbot

## Phạm vi

- `GET /api/agent/models`: hai model được quản trị cấu hình, không nhận endpoint tùy ý từ client.
- `POST /api/agent/chat` và `/api/agent/chat/stream`: thêm `model` bằng ID từ danh sách. Bỏ trống giữ cấu hình Agent.
- Giao diện: chọn model ở thanh trên; lựa chọn chỉ gửi trong request chat Agent.
- Qwen3.6-35B FP8: context 32768; Qwen3.5-4B: context 8192; khi chọn rõ model, tối đa 2048 token trả lời, tắt thinking.
- Giữ system prompt, quyền ERP và giới hạn phân vùng; context riêng từng request, không đổi cấu hình toàn cục.

## Không chuyển sang model phụ trong bản này

| Phần | Hành vi |
|---|---|
| OCR / tách chữ PDF scan | Giữ endpoint OCR hiện có; model 4B chưa được kiểm thử OCR qua luồng này |
| Sinh SQL / ERP discovery | Giữ endpoint Text2SQL hiện có; chọn 4B không đổi model sinh SQL |
| Embedding và reranker | Model riêng, không liên quan lựa chọn chat |
| Presenton dựng/xuất PPTX | Dịch vụ riêng; không thay bằng model 4B |
| API gọi trực tiếp QA, tóm tắt, soạn/tổng hợp báo cáo, slide, phân luồng | Chưa thêm trường chọn model; vẫn theo cấu hình Agent của API đó |
| POST /api/tasks và chạy lại tác vụ | Chưa lưu lựa chọn model chat vào payload tác vụ để chạy lại |

## Giới hạn cần lưu ý

- Các bước dùng `get_llm()` bên trong lượt chat Agent sẽ dùng model đã chọn; điều đó không có nghĩa toàn bộ workflow đã được nghiệm thu với 4B.
- Báo cáo dài, nhiều file, slide nhiều trang và hội thoại dài chưa kiểm chứng chất lượng/độ ổn định với 4B. Context 8K hoặc đầu ra 2K có thể không đủ.
- Kiểm tra context hiện dùng ngân sách bảo thủ theo byte UTF-8; tài liệu tiếng Việt có thể bị từ chối trước giới hạn token thực của model. Không tự cắt nội dung.
- Danh sách model lấy từ cấu hình; việc có trong danh sách không bảo đảm endpoint luôn sẵn sàng. Không tự chuyển sang model khác khi lỗi.
- Hai vLLM dùng chung GPU; chưa benchmark tải đồng thời hoặc cam kết tốc độ.
- Bản tích hợp không thay đổi cấu hình bộ nhớ/service của vLLM.

## Kiểm thử

- Test cách ly hai lựa chọn đồng thời, phục hồi context, giữ prompt/quyền và endpoint nghiệp vụ.
- Smoke test sinh câu trả lời thật trên hai endpoint; kiểm tra hồi quy streaming và tích hợp admin.

# Slide Generation API

Tạo file PowerPoint từ tài liệu Markdown bằng pipeline:

`Markdown → tóm tắt → DeckSpec → RenderPlan → PPTX`

## Chạy dịch vụ

```powershell
cd src/services/presentation/renderer
npm.cmd install
npm.cmd run build
cd ../../../..

uv run uvicorn main:app --reload
```

Biến môi trường tối thiểu:

```env
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:latest
```

## Tạo slide

`POST /api/v1/presentations/generate`

```powershell
curl.exe -X POST "http://localhost:8000/api/v1/presentations/generate" `
  -F "file=@data/De-xuat-tai-cau-truc-trai-nghiem-nguoi-dung.md" `
  -F "audience=ban lãnh đạo và đội phát triển sản phẩm" `
  -F "purpose=đề xuất tái cấu trúc trải nghiệm người dùng" `
  -F "slide_min=5" `
  -F "slide_max=8" `
  -F "min_visual_slides=2" `
  --output presentation.pptx
```

Các field tùy chọn: `audience`, `purpose`, `language`, `slide_min`, `slide_max`,
`min_visual_slides`, `model_name`. File đầu vào tối đa 5 MB và phải dùng UTF-8.

Response là file `.pptx`; header `X-Slide-Count` chứa số slide đã tạo.
Swagger UI: `http://localhost:8000/docs`.

> Endpoint chạy đồng bộ. Tài liệu dài có thể mất vài phút ở lần đầu; kết quả tóm tắt
> được cache trong `.cache/presentation_summaries`.

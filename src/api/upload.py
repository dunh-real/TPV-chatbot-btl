"""
Upload Endpoint
Nhận file PDF, trả response ngay, xử lý OCR ở background
"""

from fastapi import APIRouter, UploadFile, Form, HTTPException, BackgroundTasks
from pathlib import Path
import uuid
import time
import traceback

from src.core.config import settings, constants
from src.models.schemas import ErrorResponse

router = APIRouter()

def process_file_background(temp_path: Path, tenant_id: str, role_list: list, document_id: str):
    """Background task: OCR -> chunking -> embedding -> qdrant"""
    try:
        print(f"[BG] Bắt đầu xử lý file: {temp_path.name} (doc_id: {document_id})")
        start = time.time()
        
        from src.core.upload import ProcessFileInput
        processor = ProcessFileInput()
        result = processor.process_file_upload(
            src_file = temp_path,
            tenant_id = tenant_id,
            accessed_role_list = role_list,
            document_id = document_id,
        )

        # result expected: markdown_doc, processing_time, minio_object, presigned_url
        if isinstance(result, tuple):
            markdown_doc = result[0]
            processing_time = result[1]
            minio_object = result[2] if len(result) > 2 else None
            presigned_url = result[3] if len(result) > 3 else None
        else:
            markdown_doc = result
            processing_time = None
            minio_object = None
            presigned_url = None

        print(f"[BG] Hoàn thành: {temp_path.name} trong {time.time() - start:.1f}s")
        if minio_object:
            print(f"[BG] Markdown uploaded to MinIO: {minio_object}")
            if presigned_url:
                print(f"[BG] Presigned URL: {presigned_url}")
    
    except Exception as e:
        print(f"[BG] Lỗi xử lý file {temp_path.name}: {e}")
        traceback.print_exc()
    
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except PermissionError:
            pass

@router.post("/upload")
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile,
    tenant_id: str = Form(...),
    accessed_role_list: str = Form(...),
):
    """
    Upload file PDF:
    1. Validate file (PDF, max 50MB)
    2. Lưu file tạm
    3. Trả response ngay cho client
    4. Xử lý OCR + chunking + embedding ở background
    """
    
    # --- validate ---
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code = 400, detail = "Chỉ chấp nhận file PDF")
    
    content = await file.read()
    max_bytes = constants.MAX_FILE_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code = 400, detail = f"File vượt quá {constants.MAX_FILE_SIZE_MB}MB")
    
    try:
        role_list = [int(r.strip()) for r in accessed_role_list.split(",")]
    except ValueError:
        raise HTTPException(status_code = 400, detail = "accessed_role_list phải là danh sách số nguyên")
    
    # --- lưu file tạm ---
    document_id = str(uuid.uuid4())
    raw_dir = Path(settings.data_raw_path)
    raw_dir.mkdir(parents = True, exist_ok = True)
    temp_path = raw_dir / f"{document_id}_{file.filename}"
    
    try:
        with open(temp_path, "wb") as f:
            f.write(content)
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Không thể lưu file: {e}")
    
    # --- đẩy vào background, trả response ngay ---
    background_tasks.add_task(process_file_background, temp_path, tenant_id, role_list, document_id)
    
    return {
        "success": True,
        "message": "File đã được nhận, đang xử lý ở background",
        "document_id": document_id,
        "filename": file.filename,
    }
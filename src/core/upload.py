import sys
import os
from pathlib import Path
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

"""
Hệ thống upload tài liệu:
- Input: File tài liệu đầu vào (PDF)
- Output: Data Point được lưu trữ trên Qdrant

Luồng hoạt động:
1. Người dùng upload tài liệu. API trả về các tham số: tenant_id, accessed_role_list, src_file
2. OCR tài liệu:
Input File (PDF) -> OCR Model -> Output File (MD)
3. Chia docs thành các chunk. Kết hợp Struct + Semantic Chunking:
Output File (MD) -> Text -> Chunking -> List Chunks
4. Chuyển các chunks từ dạng văn bản sang embedding:
List Chunks -> Embedding Model -> Dense Vector (1024 Dimension) + Sparse Vector (Any Dimension)
5. Insert vào Qdrant DB
"""

PATH_INPUT_FILE = "./data/raw_dir"
PATH_OUTPUT_FILE = "./data/md_dir"

# lazy-loaded clients - tránh load model nặng khi import module
_db_client = None
_ocr_client = None
_chunking_client = None

def _get_db_client():
    global _db_client
    if _db_client is None:
        from src.services.qdrant_service import VectorStoreService
        _db_client = VectorStoreService()
    return _db_client

def _get_ocr_client():
    global _ocr_client
    if _ocr_client is None:
        from src.services.ocr_service import OCRService
        _ocr_client = OCRService()
    return _ocr_client

def _get_chunking_client():
    global _chunking_client
    if _chunking_client is None:
        from src.services.chunking_service import ChunkingService
        _chunking_client = ChunkingService()
    return _chunking_client


class ProcessFileInput():
    def __init__(self):
        pass
    
    def process_file_upload(self, src_file, tenant_id, accessed_role_list):
        first_time = time.time()
        
        db_client = _get_db_client()
        ocr_client = _get_ocr_client()
        chunking_client = _get_chunking_client()
        
        # 1. input data (pdf) -> ocr model -> output data (md)
        ocr_client.processing_data(src_file)
        md_output = Path(PATH_OUTPUT_FILE) / (src_file.stem + ".md")
        with open(md_output, "r", encoding = 'utf-8') as f:
            markdown_doc = f.read()
        
        # 2. output data (md) -> chunking -> list chunks
        chunks = chunking_client.process_hybrid_splitting(markdown_doc, tenant_id, src_file, accessed_role_list)
        
        # 3. list chunks -> embedding -> dense vector + sparse vector -> insert to qdrant db
        db_client.add_chunks(chunks)
        db_client.optimize_indexing()
        
        end_time = time.time() - first_time
        
        # return markdown text for backend server and time processing
        return markdown_doc, end_time
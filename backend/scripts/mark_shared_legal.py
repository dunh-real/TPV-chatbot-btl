#!/usr/bin/env python
"""Xác minh bytes S3 với ID cũ, sao lưu payload rồi đánh dấu kho luật; mặc định dry-run."""
import argparse, hashlib, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qdrant_client import QdrantClient, models
from sqlalchemy import create_engine, text
from app.core.config import get_settings
from app.services.object_storage import ObjectStorage
from app.services.shared_legal import object_info
from app.services.storage import safe_name

FIELDS = ['knowledge_scope','legal_index_status','storage_bucket','storage_object_key',
          'checksum_sha256','original_filename','file_size_bytes']


def run(args):
    cfg = get_settings()
    source = json.loads(Path(args.inventory).read_text())
    if source['summary']['bucket'] != cfg.minio_bucket or source['summary']['collection'] != cfg.qdrant_collection:
        raise ValueError('Inventory không khớp bucket/collection')
    # Không đưa bất cứ tài liệu đã đăng ký ERP nào vào kho dùng chung.
    engine = create_engine(cfg.ai_database_url.replace('mssql+aioodbc:', 'mssql+pyodbc:'), hide_parameters=True)
    with engine.connect() as conn:
        erp_ids = {x[0] for x in conn.execute(text('SELECT AiServerDocId FROM AI_KnowledgeDocuments WHERE AiServerDocId IS NOT NULL'))}
    engine.dispose()
    q = QdrantClient(url=cfg.qdrant_url, api_key=cfg.qdrant_api_key, timeout=90)
    storage = ObjectStorage()
    root = Path(args.output); root.mkdir(parents=True, exist_ok=True)
    manifest = root/'verified.jsonl'
    verified = {}
    if manifest.exists():
        for line in manifest.read_text().splitlines():
            row=json.loads(line)
            if row.get('status')=='marked': verified[row['key']]=row
    totals = dict(marked=0, verified=0, skipped=0, errors=0)
    documents = {d['doc_id']:d for d in source['qdrant_documents']}
    rows = [f for f in source['files'] if f['status'] in {'matched_filename','matched_sanitized_filename','matched_object_key'}]
    rows = rows[args.worker_index::args.workers]
    if args.limit: rows=rows[:args.limit]
    with manifest.open('a') as log:
        for f in rows:
            key=f['key']; doc_id=f['matched_doc_ids'][0]
            row=dict(key=key,doc_id=doc_id)
            try:
                if doc_id in erp_ids: raise ValueError('Doc ID đã thuộc ERP')
                # So phiên bản object trước khi dùng checkpoint.
                stat=object_info(storage.client,cfg.minio_bucket,key)
                previous=verified.get(key)
                if previous and previous.get('etag')==stat.etag and previous.get('bytes')==stat.size:
                    totals['skipped']+=1;continue
                raw=hashlib.sha256(); names={f['name'],safe_name(f['name'])}
                # source thực tế trong chỉ mục có thể đã bị chuẩn hóa tên.
                doc=documents.get(doc_id)
                if doc: names.update(doc['sources'])
                legacy=[hashlib.sha256(n.encode()+b'|') for n in names]
                response=storage.client.get_object(cfg.minio_bucket,key)
                try:
                    for block in response.stream(1024*1024):
                        raw.update(block)
                        for digest in legacy:digest.update(block)
                finally:response.close();response.release_conn()
                if doc_id not in {raw.hexdigest()[:16],*(h.hexdigest()[:16] for h in legacy)}:
                    raise ValueError('Bytes S3 không khớp doc_id cũ')
                if object_info(storage.client,cfg.minio_bucket,key).etag != stat.etag:
                    raise ValueError('File nguồn thay đổi trong lúc xác minh')
                query=models.Filter(must=[models.FieldCondition(key='doc_id',match=models.MatchValue(value=doc_id))])
                count=q.count(cfg.qdrant_collection,count_filter=query,exact=True).count
                if not count or doc and count!=doc['points']:raise ValueError('Số chunk thay đổi; cần kiểm kê lại')
                payload=dict(knowledge_scope='shared_legal',legal_index_status='ready',storage_bucket=cfg.minio_bucket,
                    storage_object_key=key,checksum_sha256=raw.hexdigest(),original_filename=f['name'],file_size_bytes=stat.size)
                if args.apply:
                    backup=root/(doc_id+'.jsonl')
                    # Ghi mới toàn bộ backup trước lần sửa đầu; không ghi đè backup cũ.
                    if not backup.exists():
                        tmp=backup.with_suffix('.tmp');offset=None
                        with tmp.open('w') as dest:
                            while True:
                                pts,offset=q.scroll(cfg.qdrant_collection,scroll_filter=query,offset=offset,limit=1024,
                                    with_payload=FIELDS+['document_id'],with_vectors=False)
                                for p in pts:
                                    if p.payload.get('document_id') is not None:raise ValueError('Chunk thuộc ERP')
                                    dest.write(json.dumps({'id':str(p.id),'payload':p.payload})+'\n')
                                if offset is None:break
                            dest.flush()
                            import os
                            os.fsync(dest.fileno())
                        tmp.replace(backup)
                    q.set_payload(cfg.qdrant_collection,payload,points=query,wait=True)
                    totals['marked']+=1;row['status']='marked'
                else:totals['verified']+=1;row['status']='verified'
                row.update(etag=stat.etag,bytes=stat.size,checksum_sha256=raw.hexdigest(),points=count)
            except Exception as exc:
                totals['errors']+=1;row.update(status='error',error=type(exc).__name__+': '+str(exc)[:160])
            log.write(json.dumps(row,ensure_ascii=False)+'\n');log.flush()
            print(json.dumps(row,ensure_ascii=False),flush=True)
    q.close();print(json.dumps({'summary':totals}),flush=True)
    return bool(totals['errors'])

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--inventory',required=True)
    p.add_argument('--output',required=True);p.add_argument('--apply',action='store_true');p.add_argument('--limit',type=int)
    p.add_argument('--workers',type=int,default=1);p.add_argument('--worker-index',type=int,default=0)
    args=p.parse_args()
    if args.workers<1 or not 0<=args.worker_index<args.workers:p.error('worker không hợp lệ')
    raise SystemExit(run(args))

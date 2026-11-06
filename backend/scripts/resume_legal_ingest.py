#!/usr/bin/env python
"""Phục hồi file pháp luật thiếu từ inventory; dry-run mặc định, không tạo bản ghi ERP."""
import argparse, asyncio, hashlib, json, re, sys, tempfile, unicodedata
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from qdrant_client import models
from app.core.config import get_settings
from app.rag.ingestion import IngestionPipeline
from app.rag.vectorstore import get_vector_store
from app.services.object_storage import ObjectStorage
from app.services.shared_legal import object_info
from app.services.storage import safe_name


class LegalPipeline(IngestionPipeline):
    async def ingest_text(self,*args,**kwargs):
        # Chỉ CLI quản trị dùng đường này; không đổi decorator của upload thông thường.
        return await IngestionPipeline.ingest_text.__wrapped__(self,*args,**kwargs)


async def run(args):
    cfg=get_settings();inventory=json.loads(Path(args.inventory).read_text())
    if inventory['summary']['bucket']!=cfg.minio_bucket or inventory['summary']['collection']!=cfg.qdrant_collection:
        raise ValueError('Inventory không khớp cấu hình')
    rows=[f for f in inventory['files'] if f['status']=='no_match' and f['bytes']>0
          and f['extension'] in {'.pdf','.doc','.docx'}]
    if args.limit:rows=rows[:args.limit]
    if not args.apply:
        print(json.dumps({'dry_run':True,'files':len(rows),'objects':[f['key'] for f in rows]},ensure_ascii=False));return
    from sqlalchemy import create_engine, text
    engine=create_engine(cfg.ai_database_url.replace('mssql+aioodbc:','mssql+pyodbc:'),hide_parameters=True)
    with engine.connect() as conn:
        erp_ids={r[0] for r in conn.execute(text('SELECT AiServerDocId FROM AI_KnowledgeDocuments WHERE AiServerDocId IS NOT NULL'))}
    engine.dispose()
    storage=ObjectStorage();store=get_vector_store();pipe=LegalPipeline(store=store)
    checkpoint=Path(args.checkpoint);checkpoint.parent.mkdir(parents=True,exist_ok=True)
    done={}; previous={}
    if checkpoint.exists():
        for line in checkpoint.read_text().splitlines():
            row=json.loads(line)
            previous[row['key']]=row
            if row.get('status')=='completed':done[row['key']]=row
    with checkpoint.open('a') as log:
        for f in rows:
            key=f['key'];row={'key':key}
            try:
                stat=await asyncio.to_thread(object_info,storage.client,cfg.minio_bucket,key)
                old=done.get(key)
                if old and old['etag']==stat.etag:
                    count=await store.client.count(store.collection,
                        count_filter=models.Filter(must=[models.FieldCondition(key='doc_id',match=models.MatchValue(value=old['doc_id'])),
                        models.FieldCondition(key='legal_index_status',match=models.MatchValue(value='ready'))]),exact=True)
                    if count.count==old['points']:continue
                    raise ValueError('Checkpoint có nhưng chỉ mục không còn đủ; cần đối soát')
                with tempfile.TemporaryDirectory(prefix='tpv-legal-') as folder:
                    filename=safe_name(f['name']);path=Path(folder)/filename
                    metadata=await asyncio.to_thread(storage.download,cfg.minio_bucket,key,path,f['name'])
                    if (await asyncio.to_thread(object_info,storage.client,cfg.minio_bucket,key)).etag!=stat.etag:
                        raise ValueError('File nguồn thay đổi khi tải')
                    names={filename,f['name']}
                    for form in ('NFC','NFD'):
                        name=re.sub(r'[^A-Za-z0-9_.\-]','_',unicodedata.normalize(form,f['name'])).strip('._')
                        suffix=Path(name).suffix
                        if len(name)>180:name=name[:180-len(suffix)]+suffix
                        names.add(name)
                    hashes={name:hashlib.sha256(name.encode()+b'|') for name in names};raw=hashlib.sha256()
                    with path.open('rb') as content:
                        while block:=content.read(1024*1024):
                            raw.update(block)
                            for digest in hashes.values():digest.update(block)
                    doc_id=hashes[filename].hexdigest()[:16];row.update(doc_id=doc_id,etag=stat.etag,checksum_sha256=raw.hexdigest())
                    # Không đoán một chỉ mục cũ có chunk là đã hoàn tất, cũng không tự xóa để nạp lại.
                    for candidate in {doc_id,raw.hexdigest()[:16],*(h.hexdigest()[:16] for h in hashes.values())}:
                        if candidate in erp_ids:raise ValueError('ID đã thuộc ERP; không nạp vào kho luật chung')
                        if await store.document_summary(candidate):
                            prior=previous.get(key,{})
                            if candidate!=doc_id or prior.get('doc_id')!=doc_id or prior.get('checksum_sha256')!=row['checksum_sha256']:
                                raise ValueError('Tìm thấy chỉ mục cũ; cần đối soát riêng, không tạo trùng')
                            building=models.Filter(must=[models.FieldCondition(key='doc_id',match=models.MatchValue(value=doc_id)),
                                models.FieldCondition(key='knowledge_scope',match=models.MatchValue(value='shared_legal')),
                                models.FieldCondition(key='legal_index_status',match=models.MatchValue(value='building'))])
                            partial=await store.client.count(store.collection,count_filter=building,exact=True)
                            existing=await store.document_summary(doc_id)
                            if partial.count!=existing['chunk_count']:
                                raise ValueError('Chỉ mục không phải phần nạp dở của checkpoint này')
                    metadata.update(knowledge_scope='shared_legal',legal_index_status='building')
                    row['status']='started';log.write(json.dumps(row)+'\n');log.flush()
                    result=await pipe.ingest_file(path,doc_id=doc_id,metadata=metadata,source=filename)
                    query=models.Filter(must=[models.FieldCondition(key='doc_id',match=models.MatchValue(value=doc_id))])
                    count=await store.client.count(store.collection,count_filter=query,exact=True)
                    if not result.chunk_count or count.count!=result.chunk_count:
                        raise ValueError('Chưa ghi đủ chunk; không mở truy hồi')
                    await store.client.set_payload(store.collection,{'legal_index_status':'ready'},points=query,wait=True)
                    row.update(status='completed',points=count.count)
            except Exception as exc:row.update(status='error',error=type(exc).__name__+': '+str(exc)[:160])
            log.write(json.dumps(row,ensure_ascii=False)+'\n');log.flush()
            print(json.dumps(row,ensure_ascii=False),flush=True)
    await store.close()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--inventory',required=True)
    p.add_argument('--checkpoint',required=True);p.add_argument('--limit',type=int);p.add_argument('--apply',action='store_true')
    asyncio.run(run(p.parse_args()))

"""Kiểm tra schema AI và ghi thử trong transaction luôn rollback; không chạy DDL."""
import argparse
import asyncio
import json
import sys
import uuid
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import Principal, use_principal
from app.db.ai_repository import AIRepository
from app.db.ai_session import dispose, get_engine, tables
from app.db.erp_auth import ErpAuthRepository
from app.services.ai_outputs import save_product


async def check(args):
    mapped = await tables()
    report = {"tables": list(mapped), "ddl_executed": False, "write_mode": "rollback" if args.write_rollback else "none"}
    async with get_engine().connect() as conn:
        for table in mapped.values():
            await conn.execute(select(table).limit(0))
        await conn.rollback()
        if args.write_rollback:
            if args.user_id is None:
                raise ValueError("Ghi thử cần --user-id của tài khoản ERP thực")
            async with AsyncSession(bind=conn) as session:
                access = await ErpAuthRepository(session).find_user(user_id=args.user_id)
            await conn.rollback()
            if access is None:
                raise ValueError("Không tìm thấy tài khoản ERP")
            principal = Principal(access.tenant_id, str(access.user_id), "default", access)
            marker = "test-" + uuid.uuid4().hex
            transaction = await conn.begin()
            try:
                with use_principal(principal):
                    repo = AIRepository(conn, mapped)
                    document_id = await repo.begin_document(marker, "Kiểm thử ánh xạ", {})
                    await repo.patch("KnowledgeDocuments", document_id, IndexStatus="Indexed", ChunkCount=1)
                    assert (await repo.document(marker))["Id"] == document_id
                    assert marker in await repo.document_ids()
                    table = repo.table("KnowledgeDocuments")
                    source = dict((await repo.rows("KnowledgeDocuments", table.c.Id == document_id))[0])
                    source.pop("Id")
                    source["DocumentNumber"] += "-dup"
                    duplicate_id = await repo.add("KnowledgeDocuments", **source)
                    assert marker not in await repo.document_ids()
                    assert await repo.document(marker) is None
                    assert (await repo.document(f"sql:{document_id}"))["Id"] == document_id
                    await repo.patch("KnowledgeDocuments", duplicate_id, IsDeleted=True)
                    await repo.append_turns(marker, [("user", "Câu hỏi thử"), ("assistant", "Câu trả lời thử")])
                    assert len(await repo.history(marker, 20)) == 2
                    template_id = await repo.add("ReportTemplate", Code=marker, Name="Mẫu kiểm thử", IsActive=True,
                        IsReference=False, Status="Published", TemplateType="Report", CurrentVersionNo=1)
                    await repo.add("TemplateVersions", TemplateId=template_id, VersionNo=1, FileName="test.docx",
                        FileUrl="s3://test/test.docx", FileSizeBytes=0, Status="Published", FieldsJson="{}", StructureJson="{}")
                    found = next(t for t in await repo.templates() if t["Id"] == template_id)
                    assert (await repo.template_version(found))["VersionNo"] == 1
                    for kind in ("Review", "Draft", "Aggregate", "Slide"):
                        name, product_id = await save_product(repo, kind,
                            {"request": "Kiểm thử", "file_path": "test.txt"},
                            {"template_id": template_id, "template_version": 1, "all_refs": [{"doc_id": marker}]},
                            "s3://test/output.docx")
                        assert product_id
                    task_id = await repo.add("Tasks", UserId=repo.user_id, TaskType="Draft", Status="Processing",
                        ProgressPercentage=0, NotifyOnFinish=False,
                        InputJson=json.dumps({"inputs": {"so_ky_hieu": marker+"/BC"}}))
                    await repo.lock("number:"+marker, shared=True)
                    assert await repo.number_exists(marker+"/BC")
                    assert not await repo.number_exists(marker+"/BC", exclude_task=task_id)
                    await repo.patch("Tasks", task_id, Status="Completed", ProgressPercentage=100)
                    # Danh tính khác không đọc được hội thoại/tài liệu vừa tạo.
                    other = replace(access, user_id=access.user_id + 10000000, is_static_admin=False)
                    with use_principal(replace(principal, user_id=str(other.user_id), access=other)):
                        private = AIRepository(conn, mapped)
                        assert await private.document(marker) is None
                        assert await private.history(marker, 20) == []
                    with use_principal(replace(principal, tenant_id=(access.tenant_id or 0)+1)):
                        try:
                            AIRepository(conn, mapped)
                        except PermissionError:
                            pass
                        else:
                            raise AssertionError("Không chặn tenant sai")
                    await repo.clear_history(marker)
                    assert await repo.history(marker, 20) == []
            finally:
                await transaction.rollback()
            docs = mapped["AI_KnowledgeDocuments"]
            assert not (await conn.execute(select(docs.c.Id).where(docs.c.AiServerDocId == marker))).first()
            report["checks"] = ["document_index", "published_template_version", "chat_history", "four_output_types",
                "source_references", "task_status", "owner_isolation", "tenant_mismatch", "duplicate_index_ids",
                "sql_metadata_alias", "soft_delete", "rollback_verified"]
            report["checks"].append("mssql_document_number_with_current_task_exclusion")
    report["status"] = "passed"
    return report


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-rollback", action="store_true")
    parser.add_argument("--user-id", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = await check(args)
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n")
        print(rendered)
    finally:
        await dispose()


if __name__ == "__main__":
    asyncio.run(main())

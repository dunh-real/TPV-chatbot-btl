"""Kiểm thử hàng đợi UC37 trên MSSQL; tạo bản ghi riêng và luôn dọn sau kiểm thử."""
import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select

from app.core.context import Principal, use_principal
from app.db import ai_session
from app.db.ai_repository import dumps
from app.db.erp_session import dispose_erp_engine
from app.services import access, tasks, task_queue


async def check(user_id):
    account = await access.resolve_access(str(user_id))
    if account is None:
        raise ValueError("Không tìm thấy tài khoản ERP")
    marker, ids = "uc37-probe-" + uuid.uuid4().hex, []
    mapped = await ai_session.tables()
    with use_principal(Principal(account.tenant_id, str(account.user_id), "default", account)):
        try:
            async with ai_session.repository() as repo:
                tasks.require_access(repo)
                task_id = await repo.add("Tasks", UserId=repo.user_id, TaskType="Slide",
                    AgentCode="run_presentation_workflow", Status="Queued", ProgressPercentage=0,
                    NotifyOnFinish=True, InputJson=dumps({"request": marker,
                        "_uc37": {"managed": True, "origin": "probe"}}))
                ids.append(task_id)
            claims = await asyncio.gather(task_queue.claim(task_id), task_queue.claim(task_id))
            assert sum(row is not None for row in claims) == 1, "Hai worker nhận cùng tác vụ"
            claimed = next(row for row in claims if row is not None)
            async with ai_session.repository() as repo:
                data = tasks.payload(claimed)
                data["_uc37"]["lease_until"] = "2000-01-01T00:00:00"
                await repo.patch("Tasks", task_id, InputJson=dumps(data))
            await task_queue.recover(task_id)
            async with ai_session.repository() as repo:
                assert (await tasks.detail(repo, task_id))["status"] == "Failed"
                assert any(n["task_id"] == task_id for n in await tasks.notifications(repo))
                await tasks.mark_read(repo, task_id)
                assert next(n for n in await tasks.notifications(repo) if n["task_id"] == task_id)["is_read"]
                new_id = await task_queue.retry(repo, task_id)
                ids.append(new_id)
                retry_row = await tasks.get_task(repo, new_id)
                data = tasks.payload(retry_row)
                data["_uc37"]["origin"] = "probe"  # Worker ứng dụng không được lấy bản kiểm thử.
                await repo.patch("Tasks", new_id, InputJson=dumps(data))
                assert await task_queue.retry(repo, task_id) == new_id
            try:
                async with ai_session.repository() as repo:
                    await task_queue.fence(repo, task_id, tasks.metadata(claimed)["lease_token"])
            except task_queue.LeaseLost:
                pass
            else:
                raise AssertionError("Tiến trình cũ vẫn ghi được kết quả")
            async with ai_session.repository() as repo:
                await tasks.delete_output(repo, task_id)
                assert (await tasks.detail(repo, task_id))["output_deleted"]
            return {"status": "passed", "database": "MSSQL", "ddl_executed": False,
                "checks": ["concurrent_claim", "expired_lease_recovery", "stale_writer_blocked",
                    "notifications_and_read_state", "retry_idempotence", "soft_delete_and_audit"],
                "synthetic_records_cleaned": True}
        finally:
            if ids:
                async with ai_session.get_engine().begin() as conn:
                    logs, table = mapped["AI_ExecutionLogs"], mapped["AI_Tasks"]
                    await conn.execute(delete(logs).where(logs.c.TaskId.in_(ids)))
                    # Xóa lần chạy lại trước để tôn trọng FK RetryOfTaskId.
                    for task_id in reversed(ids):
                        await conn.execute(delete(table).where(table.c.Id == task_id))
                    assert not (await conn.execute(select(table.c.Id).where(table.c.Id.in_(ids)))).first()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = json.dumps(await check(args.user_id), ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(report + "\n")
        print(report)
    finally:
        await ai_session.dispose()
        await dispose_erp_engine()


if __name__ == "__main__":
    asyncio.run(main())

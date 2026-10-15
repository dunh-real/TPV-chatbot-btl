"""Lưu mỗi lượt discovery thành Markdown riêng; không lưu dòng dữ liệu kết quả."""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import sqlglot

logger = logging.getLogger(__name__)


def save_sql_log(directory, *, query_id, question, principal, policy, schema_version,
                 outcome, executions, semantic_version=""):
    if not directory:
        return
    temporary = None
    try:
        now = datetime.now(timezone.utc)
        folder = Path(directory)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = folder / f"{now:%Y%m%dT%H%M%S%fZ}_{query_id}.md"

        def block(value, language="text"):
            value = str(value or "(không có)")
            if language == "sql":
                try:
                    value = sqlglot.transpile(value, read="tsql", write="tsql", pretty=True)[0]
                except Exception:
                    pass
            # Fence dài hơn mọi dấu backtick trong dữ liệu.
            fence = "`" * max(3, max((len(x) for x in re.findall(r"`+", value)), default=0) + 1)
            return f"{fence}{language}\n{value}\n{fence}"

        lines = ["# Truy vấn ERP", f"- Thời gian UTC: {now.isoformat()}",
                 f"- Query ID: {query_id}", f"- Tenant: {principal.tenant_id}",
                 f"- User ID: {principal.access.user_id}", f"- Nhóm: {policy.group}",
                 f"- Schema: {schema_version}", f"- Policy: {policy.version}",
                 f"- Semantic: {semantic_version}",
                 f"- Trạng thái: {'THÀNH CÔNG' if outcome['exec_ok'] else 'THẤT BẠI'}",
                 f"- Lượt model/tool: {outcome['llm_calls']}/{outcome['tool_calls']}",
                 "\n## Câu hỏi vào engine", block(question),
                 "\n## SQL cuối cùng model sinh", block(outcome.get("sql"), "sql")]
        if outcome.get("error"):
            lines += ["\n## Lỗi", block(outcome["error"])]
        for index, attempt in enumerate(outcome.get("attempts", []), 1):
            lines += [f"\n## Kiểm tra/sửa lỗi {index}",
                      f"- Hợp lệ: {attempt['valid']}; thành công: {attempt['ok']}",
                      block(attempt.get("sql"), "sql")]
            if attempt.get("error"):
                lines += [block(attempt["error"])]
        for index, execution in enumerate(executions, 1):
            lines += [f"\n## Thực thi {index} (bao gồm tra danh mục nếu có)",
                      f"- Thành công: {execution['ok']}",
                      f"- Số dòng: {execution.get('row_count', '(không có)')}",
                      "### SQL đã áp quyền và tenant", block(execution.get("sql"), "sql"),
                      "### Tham số binding", block(json.dumps(execution.get("params", {}), ensure_ascii=False))]
            if execution.get("error"):
                lines += ["### Lỗi thực thi", block(execution["error"])]
        temporary = path.with_suffix(".tmp")
        with open(temporary, "x", encoding="utf-8", opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
            stream.write("\n\n".join(lines) + "\n")
        temporary.replace(path)
        logger.info("ERP SQL log query=%s file=%s", query_id, path)
    except Exception:
        logger.warning("Không ghi được SQL log query=%s", query_id, exc_info=True)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.warning("Không dọn được file SQL log tạm query=%s", query_id)

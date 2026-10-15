"""Pipeline discovery nội bộ: model gọi tool khám phá, sinh SQL, kiểm tra và sửa lỗi."""
from __future__ import annotations

import json
import re
import time

ACCURACY_RULES = """
Resolve source entities, time boundaries, grouping, aggregation units and join cardinality.
COUNT rows differs from SUM quantities. Preserve real zero, NULL and unassigned groups.
Aggregate one-to-many branches separately to avoid multiplying totals. Never invent identifiers.
Return one complete read-only T-SQL SELECT, optionally WITH/CTEs, in one sql code block.
"""


def tool_schemas(catalog):
    tables = {"tables": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8}}
    specs = [
        ("describe_tables", "Inspect authorized columns, types and primary keys.", tables, ["tables"]),
        ("get_relations", "Inspect declared foreign keys between authorized tables.", tables, ["tables"]),
        ("validate_sql", "Validate read-only SQL, permissions and SQL Server binding without executing it.",
         {"sql": {"type": "string"}}, ["sql"]),
    ]
    if catalog.lookup_columns:
        specs.append(("lookup_values", "Look up catalog values ONLY in: " + ", ".join(sorted(catalog.lookup_columns)),
            {"table": {"type": "string"}, "column": {"type": "string"}, "search": {"type": "string"},
             "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, ["table", "column"]))
    return [{"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required,
                           "additionalProperties": False}}} for name, description, properties, required in specs]


def extract_sql(content):
    blocks = re.findall(r"```(?:sql|tsql)?\s*\n(.*?)```", content or "", re.I | re.S)
    if len(blocks) > 1:
        raise ValueError("Model trả nhiều khối SQL")
    return (blocks[0] if blocks else content or "").strip()


class DiscoveryPipeline:
    """Chỉ dùng executor được cấp; pipeline không có đường thực thi SQL trực tiếp."""
    def __init__(self, client, catalog, executor, model, system_prompt, *, max_repairs=2,
                 max_tool_rounds=4, max_tool_calls=12, max_tokens=8192, timeout=180):
        self.client, self.catalog, self.executor = client, catalog, executor
        self.model, self.system_prompt = model, system_prompt
        self.max_repairs, self.max_tool_rounds = max_repairs, max_tool_rounds
        self.max_tool_calls, self.max_tokens, self.timeout = max_tool_calls, max_tokens, timeout
        self.tool_log, self.attempts, self.calls = [], [], 0

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Text-to-SQL hết thời gian xử lý")
        return remaining

    def generate(self, messages):
        tools = tool_schemas(self.catalog)
        for round_no in range(self.max_tool_rounds + 1):
            request = {"model": self.model, "temperature": 0, "messages": list(messages),
                       "max_tokens": self.max_tokens, "timeout": self.remaining(),
                       "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
            allow_tools = round_no < self.max_tool_rounds and len(self.tool_log) < self.max_tool_calls
            if allow_tools:
                request.update(tools=tools, tool_choice="auto", parallel_tool_calls=False)
            self.calls += 1
            choice = self.client.chat.completions.create(**request).choices[0]
            if choice.finish_reason == "length":
                raise ValueError("SQL bị cắt do hết token; không được thực thi")
            message = choice.message
            calls = message.tool_calls or []
            if not calls:
                messages.append({"role": "assistant", "content": message.content or ""})
                return extract_sql(message.content)
            if not allow_tools or len(calls) > min(4, self.max_tool_calls - len(self.tool_log)):
                raise ValueError("Vượt giới hạn gọi tool discovery")
            messages.append({"role": "assistant", "content": message.content,
                             "tool_calls": [call.model_dump() for call in calls]})
            for call in calls:
                self.remaining()
                name = call.function.name
                try:
                    args = json.loads(call.function.arguments)
                    if not isinstance(args, dict):
                        raise ValueError("Tham số tool phải là object")
                    if name == "validate_sql":
                        result = self.executor.validate(**args)
                    elif name in {"describe_tables", "get_relations", "lookup_values"}:
                        result = getattr(self.catalog, name)(**args)
                    else:
                        raise ValueError("Tool không tồn tại")
                except Exception as exc:
                    result = {"ok": False, "error": str(exc)[:400]}
                self.tool_log.append({"name": name, "ok": not isinstance(result, dict) or result.get("ok", True)})
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps(result, ensure_ascii=False, default=str)})
        raise ValueError("Không có SQL cuối cùng trong giới hạn discovery")

    def run(self, question):
        self.tool_log, self.attempts, self.calls = [], [], 0
        self.deadline = time.monotonic() + self.timeout
        messages = [{"role": "system", "content": self.system_prompt + ACCURACY_RULES},
                    {"role": "user", "content": question}]
        sql, result = "", {"ok": False}
        try:
            sql = self.generate(messages)
            for attempt in range(self.max_repairs + 1):
                self.remaining()
                validation = self.executor.validate(sql)
                self.remaining()
                result = self.executor.execute(sql) if validation["ok"] else validation
                self.attempts.append({"attempt": attempt, "valid": validation["ok"], "ok": result["ok"]})
                if result["ok"] or attempt == self.max_repairs:
                    break
                messages.append({"role": "user", "content": json.dumps({
                    "task": "Repair failed SQL without changing the original question. Return complete SQL.",
                    "question": question, "sql": sql, "error": result.get("error")}, ensure_ascii=False)})
                sql = self.generate(messages)
        except Exception as exc:
            result = {"ok": False, "error": str(exc)[:500],
                      "error_type": "timeout" if isinstance(exc, TimeoutError) else "generation_error"}
        return {"exec_ok": result["ok"], "result": result, "error_type": result.get("error_type"),
                "error": result.get("error"), "sql": sql, "llm_calls": self.calls,
                "tool_calls": len(self.tool_log), "attempts": self.attempts}

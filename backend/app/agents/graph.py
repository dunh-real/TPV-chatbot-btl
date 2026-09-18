"""Lắp workflow 1 (hỏi đáp / tra cứu) thành đồ thị LangGraph.

    rewrite_query -> retrieve -┬-> build_context -> generate -> END
                               └-> no_context ----------------> END
"""

from __future__ import annotations

import logging
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from langgraph.graph import END, StateGraph

from app.agents.nodes.qa import (
    build_context_node,
    generate_node,
    no_context_node,
    retrieve_node,
    rewrite_query_node,
)
from app.agents.nodes.document import (
    assemble_node,
    classify_node,
    llm_review_node,
    parse_node,
    rule_check_node,
    tasks_node,
)
from app.agents.nodes.drafting import (
    export_node,
    extract_params_node,
    fetch_data_node,
    register_node,
    render_sections_node,
    search_regulations_node,
    select_template_node,
    validate_node,
)
from app.agents.nodes.report import (
    charts_node,
    export_node as agg_export_node,
    extract_params_node as agg_extract_params_node,
    gather_data_node,
    reconcile_node,
    register_node as agg_register_node,
    render_node as agg_render_node,
    validate_node as agg_validate_node,
)
from app.agents.nodes.presentation import (
    content_node,
    outline_node,
    render_node as ppt_render_node,
    validate_node as ppt_validate_node,
)
from app.agents.router import INTENTS, MIN_CONFIDENCE, classify_intent
from app.agents.state import (
    AgentState,
    AggregateState,
    DocumentState,
    DraftState,
    PresentationState,
    QAState,
)
from app.services.conversation import get_memory
from app.tools.base import ToolError

logger = logging.getLogger(__name__)


def route_after_retrieve(state: QAState) -> Literal["build_context", "no_context"]:
    """Không có chunk nào vượt ngưỡng rerank -> không để LLM tự do suy diễn."""
    return "build_context" if state.get("chunks") else "no_context"


def build_qa_graph():
    graph = StateGraph(QAState)
    graph.add_node("rewrite_query", rewrite_query_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("build_context", build_context_node)
    graph.add_node("generate", generate_node)
    graph.add_node("no_context", no_context_node)

    graph.set_entry_point("rewrite_query")
    graph.add_edge("rewrite_query", "retrieve")
    graph.add_conditional_edges(
        "retrieve",
        route_after_retrieve,
        {"build_context": "build_context", "no_context": "no_context"},
    )
    graph.add_edge("build_context", "generate")
    graph.add_edge("generate", END)
    graph.add_edge("no_context", END)
    return graph.compile()


@lru_cache
def get_qa_graph():
    return build_qa_graph()


async def build_initial_state(
    question: str,
    conversation_id: str | None = None,
    doc_ids: list[str] | None = None,
    sources: list[str] | None = None,
    doc_types: list[str] | None = None,
    top_n: int | None = None,
    use_rerank: bool = True,
    load_history: bool = True,
) -> QAState:
    conversation_id = conversation_id or str(uuid.uuid4())
    history = await get_memory().get_history(conversation_id) if load_history else []
    state: QAState = {
        "question": question,
        "conversation_id": conversation_id,
        "history": history,
        "doc_ids": doc_ids or [],
        "sources": sources or [],
        "doc_types": doc_types or [],
        "use_rerank": use_rerank,
        "trace": {},
    }
    if top_n:
        state["top_n"] = top_n
    return state


# --------------------------------------------------------------------------- #
# Workflow 2: xử lý văn bản
# --------------------------------------------------------------------------- #
def build_document_graph():
    """Ba nhánh phân tích chạy song song sau khi parse xong.

    Chúng độc lập nhau (rule đọc định dạng, ba nhánh LLM đọc text) nên không có
    lý do gì phải chờ nhau; mỗi node ghi vào một khoá riêng của state.
    """
    graph = StateGraph(DocumentState)
    graph.add_node("parse", parse_node)
    graph.add_node("rule_check", rule_check_node)
    graph.add_node("llm_review", llm_review_node)
    graph.add_node("classify", classify_node)
    graph.add_node("tasks", tasks_node)
    graph.add_node("assemble", assemble_node)

    graph.set_entry_point("parse")
    for node in ("rule_check", "llm_review", "classify", "tasks"):
        graph.add_edge("parse", node)
        graph.add_edge(node, "assemble")
    graph.add_edge("assemble", END)
    return graph.compile()


@lru_cache
def get_document_graph():
    return build_document_graph()


async def run_document_workflow(
    file_path: str,
    file_name: str = "",
    noi_gui: str = "",
    departments: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    state: DocumentState = {
        "file_path": file_path,
        "file_name": file_name or file_path,
        "noi_gui": noi_gui,
        "departments": departments or [],
        "trace": {},
    }
    result = await get_document_graph().ainvoke(state)
    return result.get("result", {})


# --------------------------------------------------------------------------- #
# Workflow 3: soạn văn bản theo mẫu
# --------------------------------------------------------------------------- #
MAX_DRAFT_RETRIES = 2


def route_after_params(state: DraftState) -> Literal["select_template", "__end__"]:
    """Thiếu tham số bắt buộc thì dừng để hỏi lại, không soạn nhầm đơn vị."""
    return "__end__" if state.get("missing_input") else "select_template"


def route_after_template(state: DraftState) -> Literal["fetch_data", "__end__"]:
    return "__end__" if (state.get("missing_input") or state.get("error")) else "fetch_data"


def route_after_validate(state: DraftState) -> Literal["render_sections", "export"]:
    """Số liệu không truy được về CSDL thì cho viết lại, tối đa vài lần.

    Chỉ lặp với lỗi nội dung; lỗi thể thức là lỗi của code sinh DOCX, LLM viết lại
    bao nhiêu lần cũng không sửa được.
    """
    failed = state.get("validation", {}).get("status") == "failed"
    if failed and state.get("retry_count", 0) < MAX_DRAFT_RETRIES:
        return "render_sections"
    return "export"


async def _count_retry(state: DraftState) -> dict[str, Any]:
    return {"retry_count": state.get("retry_count", 0) + 1}


def build_draft_graph():
    graph = StateGraph(DraftState)
    graph.add_node("extract_params", extract_params_node)
    graph.add_node("select_template", select_template_node)
    graph.add_node("fetch_data", fetch_data_node)
    graph.add_node("search_regulations", search_regulations_node)
    graph.add_node("render_sections", render_sections_node)
    graph.add_node("count_retry", _count_retry)
    graph.add_node("validate", validate_node)
    graph.add_node("export", export_node)
    graph.add_node("register", register_node)

    graph.set_entry_point("extract_params")
    graph.add_conditional_edges("extract_params", route_after_params,
                                {"select_template": "select_template", "__end__": END})
    graph.add_conditional_edges("select_template", route_after_template,
                                {"fetch_data": "fetch_data", "__end__": END})
    # Số liệu và quy định độc lập nhau -> chạy song song.
    graph.add_edge("fetch_data", "search_regulations")
    graph.add_edge("search_regulations", "render_sections")
    graph.add_edge("render_sections", "validate")
    graph.add_conditional_edges("validate", route_after_validate,
                                {"render_sections": "count_retry", "export": "export"})
    graph.add_edge("count_retry", "render_sections")
    graph.add_edge("export", "register")
    graph.add_edge("register", END)
    return graph.compile()


@lru_cache
def get_draft_graph():
    return build_draft_graph()


async def run_draft_workflow(
    request: str,
    ma_don_vi: str | None = None,
    inputs: dict[str, Any] | None = None,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    state: DraftState = {
        "request": request,
        "ma_don_vi": ma_don_vi or "",
        "history": history or [],
        "inputs": inputs or {},
        "retry_count": 0,
    }
    result = await get_draft_graph().ainvoke(state)
    return {
        "request": request,
        "params": result.get("params", {}),
        "ma_don_vi": result.get("ma_don_vi", ""),
        "template": result.get("template", {}).get("ma_template", ""),
        "template_name": result.get("template", {}).get("ten_bao_cao", ""),
        "sections": result.get("sections", []),
        "validation": result.get("validation", {}),
        "output_path": result.get("output_path", ""),
        "registered_as": result.get("registered_as", ""),
        "regulations": result.get("regulations", []),
        "assumptions": result.get("assumptions", []),
        "data_notes": result.get("data_notes", []),
        "missing_input": result.get("missing_input", []),
        "retry_count": result.get("retry_count", 0),
        "error": result.get("error", ""),
    }


# --------------------------------------------------------------------------- #
# Workflow 4: tổng hợp báo cáo
# --------------------------------------------------------------------------- #
def route_after_gather(state: AggregateState) -> Literal["reconcile", "__end__"]:
    return "__end__" if state.get("error") else "reconcile"


def build_aggregate_graph():
    """Đối chiếu file và vẽ biểu đồ độc lập nhau nên chạy song song."""
    graph = StateGraph(AggregateState)
    graph.add_node("extract_params", agg_extract_params_node)
    graph.add_node("gather_data", gather_data_node)
    graph.add_node("reconcile", reconcile_node)
    graph.add_node("charts", charts_node)
    graph.add_node("render", agg_render_node)
    graph.add_node("validate", agg_validate_node)
    graph.add_node("export", agg_export_node)
    graph.add_node("register", agg_register_node)

    graph.set_entry_point("extract_params")
    graph.add_edge("extract_params", "gather_data")
    graph.add_conditional_edges("gather_data", route_after_gather,
                                {"reconcile": "reconcile", "__end__": END})
    graph.add_edge("gather_data", "charts")
    graph.add_edge("reconcile", "render")
    graph.add_edge("charts", "render")
    graph.add_edge("render", "validate")
    graph.add_edge("validate", "export")
    graph.add_edge("export", "register")
    graph.add_edge("register", END)
    return graph.compile()


@lru_cache
def get_aggregate_graph():
    return build_aggregate_graph()


async def run_aggregate_workflow(
    request: str,
    inputs: dict[str, Any] | None = None,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    state: AggregateState = {"request": request, "history": history or [],
                             "inputs": inputs or {}}
    result = await get_aggregate_graph().ainvoke(state)

    # Biểu đồ là bytes - trả đường dẫn file thay vì nhồi vào JSON response.
    sections = [
        {k: v for k, v in section.items() if k != "image"} | {"has_chart": bool(section.get("image"))}
        for section in result.get("sections", [])
    ]
    return {
        "request": request,
        "params": result.get("params", {}),
        "data": result.get("data", {}),
        "sections": sections,
        "reconciliation": result.get("reconciliation", []),
        "has_discrepancy": result.get("has_discrepancy", False),
        "validation": result.get("validation", {}),
        "output_path": result.get("output_path", ""),
        "registered_as": result.get("registered_as", ""),
        "assumptions": result.get("assumptions", []),
        "error": result.get("error", ""),
    }


# --------------------------------------------------------------------------- #
# Workflow 5: tạo bộ slide
# --------------------------------------------------------------------------- #
def build_presentation_graph():
    """Tái dùng phần lấy số liệu và vẽ biểu đồ của workflow 4.

    LLM chỉ sinh dàn ý và chữ; việc dựng file .pptx do code làm ở node cuối.
    """
    graph = StateGraph(PresentationState)
    graph.add_node("extract_params", agg_extract_params_node)
    graph.add_node("gather_data", gather_data_node)
    graph.add_node("charts", charts_node)
    graph.add_node("outline", outline_node)
    graph.add_node("content", content_node)
    graph.add_node("validate", ppt_validate_node)
    graph.add_node("render", ppt_render_node)

    graph.set_entry_point("extract_params")
    graph.add_edge("extract_params", "gather_data")
    graph.add_conditional_edges("gather_data", route_after_gather,
                                {"reconcile": "outline", "__end__": END})
    graph.add_edge("gather_data", "charts")
    graph.add_edge("outline", "content")
    graph.add_edge("charts", "content")
    graph.add_edge("content", "validate")
    graph.add_edge("validate", "render")
    graph.add_edge("render", END)
    return graph.compile()


@lru_cache
def get_presentation_graph():
    return build_presentation_graph()


async def run_presentation_workflow(
    request: str,
    inputs: dict[str, Any] | None = None,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    state: PresentationState = {"request": request, "history": history or [],
                                "inputs": inputs or {}}
    result = await get_presentation_graph().ainvoke(state)

    slides = [
        {k: v for k, v in slide.items() if k not in ("source_data",)}
        for slide in result.get("slides", [])
    ]
    return {
        "request": request,
        "params": result.get("params", {}),
        "outline": result.get("outline", {}),
        "slides": slides,
        "validation": result.get("validation", {}),
        "output_path": result.get("output_path", ""),
        "slide_count": result.get("slide_count", 0),
        "removed_bullets": result.get("removed_bullets", 0),
        "assumptions": result.get("assumptions", []),
        "error": result.get("error", ""),
    }


# --------------------------------------------------------------------------- #
# Agent tổng: một câu yêu cầu -> đúng một workflow
#
#                          START
#                            │
#                      intent_router
#          ┌────────┬─────────┼─────────┬──────────┬─────────┐
#         qa     document   draft     report   presentation  clarify
#          │        │         │          │          │         │
#     search_docs  parser  template+SQL data_tool  data_tool   │
#          │        │         │          │          │         │
#         LLM      LLM       LLM        LLM        LLM        │
#          └────────┴─────────┴────┬─────┴──────────┴─────────┘
#                               finalize
#                                  │
#                                 END
#
# Nhánh `clarify` tồn tại vì đoán bừa tốn kém hơn hỏi lại: soạn nhầm loại báo cáo
# thì người dùng phải đọc hết mới phát hiện, còn một câu hỏi lại chỉ mất 5 giây.
# --------------------------------------------------------------------------- #
async def intent_router_node(state: AgentState) -> dict[str, Any]:
    result = await classify_intent(
        request=state["request"],
        has_file=bool(state.get("file_id")),
        history=state.get("history"),
    )
    return {"intent": result.intent, "routing": result.as_dict()}


def route_after_intent(
    state: AgentState,
) -> Literal["qa", "document", "draft", "report", "presentation", "agent", "clarify"]:
    routing = state.get("routing", {})
    # Chỉ hỏi lại khi model vừa có câu hỏi vừa KHÔNG chắc ý định. Model nói chuyện
    # dài dòng hay kèm sẵn một câu hỏi lịch sự; cứ thấy `clarify` là dừng thì agent
    # không bao giờ làm việc gì. Thiếu đầu vào cụ thể đã có workflow tự hỏi.
    if routing.get("clarify") and routing.get("confidence", 0.0) < MIN_CONFIDENCE:
        return "clarify"
    intent = state.get("intent", "qa")
    return intent if intent in INTENTS else "qa"  # type: ignore[return-value]


async def qa_branch(state: AgentState) -> dict[str, Any]:
    qa_state = await build_initial_state(
        question=state["request"],
        conversation_id=state.get("conversation_id"),
        load_history=False,
    )
    qa_state["history"] = state.get("history", [])
    result = await run_qa(qa_state)
    return {
        "answer": result.get("answer", ""),
        "citations": result.get("used_citations", []),
        "result": {"standalone_query": result.get("standalone_query", ""),
                   "query_variants": result.get("query_variants", [])},
        "trace": result.get("trace", {}),
        "error": result.get("error", ""),
    }


async def document_branch(state: AgentState) -> dict[str, Any]:
    from app.tools.document import analyze_document

    try:
        result = await analyze_document(state["file_id"])
    except ToolError as exc:
        return {"answer": f"Không xử lý được văn bản: {exc}", "error": str(exc)}
    return {"answer": _summarize_document(result), "result": result,
            # Nguồn của phần tóm tắt; nguồn của từng nhiệm vụ nằm trong result["tasks"].
            "refs": result.get("summary_refs", []), "error": result.get("error", "")}


async def draft_branch(state: AgentState) -> dict[str, Any]:
    result = await run_draft_workflow(
        request=state["request"],
        ma_don_vi=state.get("ma_don_vi"),
        inputs=state.get("inputs", {}),
        history=state.get("history"),
    )
    return {"answer": _summarize_draft(result), "result": result,
            "missing_input": result.get("missing_input", []),
            "error": result.get("error", "")}


async def report_branch(state: AgentState) -> dict[str, Any]:
    result = await run_aggregate_workflow(state["request"], inputs=state.get("inputs", {}),
                                          history=state.get("history"))
    return {"answer": _summarize_report(result), "result": result,
            "error": result.get("error", "")}


async def presentation_branch(state: AgentState) -> dict[str, Any]:
    result = await run_presentation_workflow(state["request"], inputs=state.get("inputs", {}),
                                             history=state.get("history"))
    return {"answer": _summarize_presentation(result), "result": result,
            "error": result.get("error", "")}


async def agent_branch(state: AgentState) -> dict[str, Any]:
    """Nhánh duy nhất để MODEL tự chọn công cụ, thay vì code chọn hộ."""
    from app.agents.nodes.toolloop import run_tool_loop

    result = await run_tool_loop(state["request"], history=state.get("history"))
    return {"answer": _summarize_agent(result), "result": result,
            "refs": result.get("refs", []), "error": result.get("error", "")}


async def clarify_node(state: AgentState) -> dict[str, Any]:
    """Yêu cầu mơ hồ -> hỏi lại, không chạy workflow nào cả."""
    return {"answer": state.get("routing", {}).get("clarify")
            or "Bạn có thể nói rõ hơn mong muốn của mình không?",
            "result": {}}


# ----------------------------- dựng câu trả lời ---------------------------- #
# Mọi câu dưới đây do CODE dựng từ kết quả workflow. Không gọi LLM: đây là thông
# báo trạng thái, để model diễn đạt lại thì nó có cơ hội nói sai điều đã xảy ra.
VALIDATION_VI = {
    "passed": "số liệu khớp với dữ liệu gốc",
    "warning": "có cảnh báo cần xem lại",
    "failed": "có số liệu không truy được về dữ liệu gốc nên chưa xuất file",
    "skipped": "chưa kiểm tra số liệu",
}


def _validation_note(result: dict[str, Any]) -> str:
    status = result.get("validation", {}).get("status", "")
    return VALIDATION_VI.get(status, "")


# Tên tiếng Việt của từng tiêu chí, để dòng "Đạt:" đọc được như tiếng người.
RULE_VI = {
    "format.font": "phông chữ", "format.size_pt": "cỡ chữ",
    "format.page_mm": "khổ giấy", "format.margin_mm": "lề trang",
    "format.alignment": "canh đều hai bên", "format.line_spacing": "giãn dòng",
    "format.paragraph_spacing_pt": "khoảng cách đoạn",
    "component_format.quoc_hieu": "trình bày quốc hiệu",
    "component_format.tieu_ngu": "trình bày tiêu ngữ",
}


def _format_section(result: dict[str, Any]) -> list[str]:
    """Mục 1: thể thức. Gộp lỗi cùng loại để không đổ ra một bức tường chữ."""
    rule_check = result.get("rule_check", {})
    totals = result.get("totals", {})
    errors, warnings = totals.get("errors", 0), totals.get("warnings", 0)

    if rule_check.get("status") == "skipped":
        return [f"1. THỂ THỨC: chưa kiểm được. {rule_check.get('reason', '')}".strip()]

    lines = ["1. THỂ THỨC: đạt, không phát hiện lỗi."
             if not errors and not warnings
             else f"1. THỂ THỨC: {errors} lỗi, {warnings} cảnh báo cần sửa."]

    grouped: dict[str, list[dict[str, Any]]] = {}
    for f in rule_check.get("findings", []):
        grouped.setdefault(f["rule"], []).append(f)

    # Lỗi trước, cảnh báo sau: thứ phải sửa đứng trên thứ nên xem lại.
    for rule in sorted(grouped, key=lambda r: grouped[r][0]["severity"] != "error"):
        items = grouped[rule]
        first = items[0]
        chi_tiet = ""
        if first.get("actual"):
            chi_tiet = f" (đang là {first['actual']}"
            chi_tiet += f", cần {first['expected']})" if first.get("expected") else ")"
        o_dau = f" — {len(items)} chỗ" if len(items) > 1 else (
            f" — {first['block_id']}" if first.get("block_id") else "")
        lines.append(f"   • {first['message']}{chi_tiet}{o_dau}")

    if (ten := [RULE_VI[r] for r in rule_check.get("passed", []) if r in RULE_VI]):
        lines.append(f"   Đạt: {', '.join(ten)}.")
    if (skipped := rule_check.get("skipped")):
        lines.append(f"   Chưa kiểm được: {'; '.join(skipped)}.")
    return lines


def _assignment_section(result: dict[str, Any]) -> list[str]:
    """Mục 3: mỗi nơi nhận một dòng, kèm việc và hạn của riêng nơi đó."""
    tasks = result.get("tasks") or []
    if not tasks:
        return ["3. GỢI Ý PHÂN CÔNG: văn bản không nêu rõ nơi nhận hay đầu việc nào."]

    lines = [f"3. GỢI Ý PHÂN CÔNG ({len(tasks)} nơi nhận):"]
    for item in tasks:
        ten = item.get("department_name") or item.get("department") or "(không rõ)"
        # Nơi nhận ngoài danh mục không có mã để chuyển tự động - nói rõ ra.
        ngoai = "" if item.get("in_catalog", True) else " [ngoài danh mục phòng ban]"
        lines.append(f"   • {ten}{ngoai}: {item.get('task', '')}")
        if (can := item.get("data_needed")):
            lines.append(f"     Cần chuẩn bị: {', '.join(can)}.")
        if (han := item.get("deadline")):
            lines.append(f"     Hạn: {han}.")
    return lines


def _summarize_document(result: dict[str, Any]) -> str:
    """Ba mục: soát thể thức -> tóm tắt nội dung -> gợi ý phân công.

    Toàn bộ do CODE dựng từ kết quả workflow, không gọi thêm LLM: đây là thông báo
    trạng thái, để model diễn đạt lại thì nó có cơ hội nói sai điều đã xảy ra.
    """
    if result.get("error"):
        return f"Không đọc được văn bản: {result['error']}"

    lines = _format_section(result)

    lines.append("")
    lines.append(f"2. NỘI DUNG: {result.get('summary') or 'chưa tóm tắt được.'}")
    if (deadline := result.get("deadline")):
        lines.append(f"   Hạn chung của văn bản: {deadline}.")

    lines.append("")
    lines += _assignment_section(result)
    return "\n".join(lines)


def _summarize_draft(result: dict[str, Any]) -> str:
    if (missing := result.get("missing_input")):
        if "ma_don_vi" in missing:
            return "Bạn muốn soạn báo cáo cho đơn vị nào? Vui lòng nêu rõ mã đơn vị."
        if "ma_template" in missing:
            return "Chưa xác định được mẫu báo cáo phù hợp. Bạn muốn dùng mẫu nào?"
        return f"Còn thiếu thông tin: {', '.join(missing)}."
    if result.get("error"):
        return f"Không soạn được văn bản: {result['error']}"

    parts = [f"Đã soạn {result.get('template_name') or 'văn bản'}."]
    if (note := _validation_note(result)):
        parts.append(f"Kiểm tra: {note}.")
    if not result.get("output_path"):
        parts.append("Chưa xuất file, vui lòng xem lại phần số liệu.")
    for item in result.get("data_notes", []) + result.get("assumptions", []):
        parts.append(f"Lưu ý: {item}.")
    return " ".join(parts)


def _summarize_report(result: dict[str, Any]) -> str:
    if result.get("error"):
        return f"Không tổng hợp được báo cáo: {result['error']}"

    params = result.get("params", {})
    parts = [f"Đã tổng hợp báo cáo kỳ {params.get('ky', '')}."]

    reporting = result.get("data", {}).get("reporting", {})
    if reporting:
        parts.append(f"{reporting.get('units_reported', 0)}/"
                     f"{reporting.get('units_total', 0)} đơn vị đã gửi báo cáo.")
        if (missing := reporting.get("missing")):
            parts.append("Chưa gửi: " + ", ".join(m["ten_don_vi"] for m in missing) + ".")
    if result.get("has_discrepancy"):
        parts.append("Phát hiện chênh lệch giữa báo cáo đơn vị và số liệu kiểm kê.")
    if (note := _validation_note(result)):
        parts.append(f"Kiểm tra: {note}.")
    for item in result.get("assumptions", []):
        parts.append(f"Lưu ý: {item}.")
    return " ".join(parts)


def _summarize_agent(result: dict[str, Any]) -> str:
    """Câu trả lời của vòng lặp, kèm cảnh báo khi nó dừng bất thường."""
    if (pending := result.get("pending_approval")):
        return (f"Tôi cần tạo file bằng công cụ {pending['tool']} để trả lời yêu cầu này. "
                f"Bạn duyệt thì tôi thực hiện.")

    answer = result.get("answer") or ""
    reason = result.get("stop_reason", "")
    if reason == "chạm trần số bước":
        return (answer or "") + " (Chưa tra xong: đã chạm trần số bước tra cứu.)"
    if reason == "quá nhiều lỗi công cụ":
        return (answer or "Tôi chưa tra được thông tin này.") + \
               " (Công cụ báo lỗi nhiều lần, vui lòng nêu rõ kỳ và đơn vị.)"
    if result.get("validation", {}).get("status") == "failed":
        return answer + " (Cảnh báo: có con số chưa đối chiếu được với dữ liệu gốc.)"
    return answer or "Tôi chưa tra được thông tin này."


def _summarize_presentation(result: dict[str, Any]) -> str:
    if result.get("error"):
        return f"Không tạo được bộ slide: {result['error']}"

    parts = [f"Đã tạo bộ slide {result.get('slide_count', 0)} trang."]
    if (removed := result.get("removed_bullets")):
        parts.append(f"Đã bỏ {removed} gạch đầu dòng có số liệu không đối chiếu được.")
    for item in result.get("assumptions", []):
        parts.append(f"Lưu ý: {item}.")
    return " ".join(parts)


SUFFIX_KIND = {".docx": "docx", ".pptx": "pptx", ".pdf": "pdf"}


async def finalize_node(state: AgentState) -> dict[str, Any]:
    """Gom file sinh ra và ghi lượt hội thoại vào bộ nhớ."""
    from app.services import storage

    artifacts: list[dict[str, Any]] = []
    output_path = state.get("result", {}).get("output_path")
    if output_path:
        path = Path(output_path)
        artifacts.append({
            "file_id": storage.make_file_id("output", path.name),
            "file_name": path.name,
            "kind": SUFFIX_KIND.get(path.suffix.lower(), path.suffix.lstrip(".")),
        })

    # Nhánh hỏi đáp đã tự ghi hội thoại trong `run_qa`.
    if state.get("intent") != "qa":
        await get_memory().append_turns(
            state.get("conversation_id", ""),
            [("user", state["request"]), ("assistant", state.get("answer", ""))],
        )

    return {"artifacts": artifacts}


def build_agent_graph():
    graph = StateGraph(AgentState)
    graph.add_node("intent_router", intent_router_node)
    graph.add_node("qa", qa_branch)
    graph.add_node("document", document_branch)
    graph.add_node("draft", draft_branch)
    graph.add_node("report", report_branch)
    graph.add_node("presentation", presentation_branch)
    graph.add_node("agent", agent_branch)
    graph.add_node("clarify", clarify_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("intent_router")
    branches = ("qa", "document", "draft", "report", "presentation", "agent", "clarify")
    graph.add_conditional_edges("intent_router", route_after_intent,
                                {name: name for name in branches})
    for name in branches:
        graph.add_edge(name, "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()


@lru_cache
def get_agent_graph():
    return build_agent_graph()


async def run_agent(
    request: str,
    conversation_id: str | None = None,
    file_id: str = "",
    ma_don_vi: str = "",
    inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Điểm vào duy nhất của agent: một yêu cầu, một câu trả lời, kèm file nếu có."""
    conversation_id = conversation_id or str(uuid.uuid4())
    state: AgentState = {
        "request": request,
        "conversation_id": conversation_id,
        "history": await get_memory().get_history(conversation_id),
        "file_id": file_id,
        "ma_don_vi": ma_don_vi,
        "inputs": inputs or {},
        "trace": {},
    }
    result = await get_agent_graph().ainvoke(state)
    return {
        "request": request,
        "conversation_id": conversation_id,
        "intent": result.get("intent", ""),
        "routing": result.get("routing", {}),
        "answer": result.get("answer", ""),
        "citations": result.get("citations", []),
        "refs": result.get("refs", []),
        "artifacts": result.get("artifacts", []),
        "missing_input": result.get("missing_input", []),
        "result": result.get("result", {}),
        "trace": result.get("trace", {}),
        "error": result.get("error", ""),
    }


async def run_qa(state: QAState) -> dict[str, Any]:
    """Chạy trọn workflow và ghi lại lượt hội thoại."""
    result = await get_qa_graph().ainvoke(state)

    await get_memory().append_turns(
        state["conversation_id"],
        [("user", state["question"]), ("assistant", result.get("answer", ""))],
    )
    return result


async def prepare_context(state: QAState) -> QAState:
    """Chạy tới trước bước sinh chữ - dùng cho chế độ streaming.

    Giữ đúng thứ tự node của đồ thị nhưng dừng lại để API tự stream câu trả lời.
    """
    merged: QAState = dict(state)  # type: ignore[assignment]
    merged.update(await rewrite_query_node(merged))
    merged.update(await retrieve_node(merged))
    if merged.get("chunks"):
        merged.update(await build_context_node(merged))
    else:
        merged.update(await no_context_node(merged))
    return merged

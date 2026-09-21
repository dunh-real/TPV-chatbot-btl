"""Sơ đồ tư duy: đọc cả tài liệu bằng MAP-REDUCE rồi dựng thành cây chủ đề.

Vì sao không nhét cả tài liệu vào một lời gọi: một công văn dài vượt cửa sổ ngữ
cảnh, mà kể cả khi vừa thì model đọc một lượt vẫn bỏ sót phần giữa. Nên chia hai
pha:

    MAP     mỗi mẻ chunk -> danh sách chủ đề có trong mẻ đó (CHỈ tiêu đề)
    REDUCE  gộp mọi danh sách -> một cây duy nhất, hết trùng lặp, đúng thứ tự

Và nội dung từng mục thì KHÔNG sinh ở đây. Một cây 60 mục mà sinh sẵn nội dung
là 60 lượt LLM cho thứ người dùng phần lớn không mở ra xem. Người dùng bấm vào
mục nào, mục đó mới đi truy hồi và viết - rồi ghi lại vào cây để lần sau khỏi
sinh lại.

Public API:
    generate_skeleton(batches, doc_name, word_count) -> dict
    generate_node_content(node_title, chunks)        -> dict
    build_batches(texts)                             -> list[str]
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import deque
from typing import Any

from app.core.config import get_settings
from app.services.llm import LLMError, get_llm

logger = logging.getLogger(__name__)

# Số mẻ ở pha MAP. Ít quá thì mỗi mẻ lại dài đến mức model đọc lướt; nhiều quá
# thì tốn lượt gọi mà chủ đề bị băm vụn giữa các mẻ.
MAP_TARGET_BATCHES = 6
MAP_MIN_BATCHES = 4
MAP_MAX_BATCHES = 8

_HEADER_RE = re.compile(r"^#{1,3}\s+")


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #
_MAP_SYSTEM_PROMPT = """\
Bạn là chuyên gia phân tích cấu trúc tài liệu. Nhiệm vụ: đọc đoạn nội dung được cung cấp \
và xác định TẤT CẢ các chủ đề, mục, ý chính có trong đoạn đó.

═══ YÊU CẦU BẮT BUỘC ═══

▸ Trả về danh sách các node (chủ đề/mục) mà bạn tìm thấy trong đoạn nội dung.
▸ Mỗi node CHỈ CẦN có:
  - "title": Tiêu đề ngắn gọn, rõ ràng (dưới 80 ký tự)
  - "children": Mảng các node con (nếu có cấu trúc phân cấp trong đoạn)

▸ KHÔNG viết summary, KHÔNG viết key_points, KHÔNG viết nội dung chi tiết.
▸ Chỉ tập trung vào việc XÁC ĐỊNH CẤU TRÚC và TÊN các chủ đề/mục.

▸ SỐ LƯỢNG: Chỉ liệt kê các chủ đề CHÍNH, KHÔNG liệt kê quá chi tiết.
  - Tối đa 5-8 node cấp cao nhất cho mỗi đoạn.
  - Chỉ tạo children khi chủ đề thực sự có phân cấp rõ ràng.
  - Ưu tiên GỘP các ý nhỏ thành 1 node thay vì tách nhỏ.

▸ NGUYÊN TẮC:
  - Phân tích NỘI DUNG và Ý NGHĨA, không chỉ copy heading.
  - Nhóm các ý liên quan lại với nhau thành cấu trúc phân cấp.
  - Giữ nguyên thuật ngữ chuyên ngành, tên riêng từ tài liệu.
  - Title viết bằng CÙNG NGÔN NGỮ với tài liệu gốc.

▸ OUTPUT: Trả về CHỈ JSON hợp lệ theo format:
{
  "nodes": [
    {
      "title": "Chủ đề A",
      "children": [
        {"title": "Mục con A.1", "children": []},
        {"title": "Mục con A.2", "children": []}
      ]
    },
    {
      "title": "Chủ đề B",
      "children": []
    }
  ]
}
Không markdown fences, không giải thích.\
"""

_REDUCE_SYSTEM_PROMPT = """\
Bạn là chuyên gia tổng hợp cấu trúc tài liệu. Nhiệm vụ: nhận các danh sách node \
từ nhiều phần khác nhau của cùng một tài liệu, rồi TỔNG HỢP thành MỘT cây sơ đồ tư duy \
hoàn chỉnh, logic, không trùng lặp.

═══ YÊU CẦU BẮT BUỘC ═══

▸ ĐỘ CHI TIẾT: Cây sơ đồ phải BAO PHỦ TOÀN BỘ chủ đề từ tất cả các phần.
  KHÔNG được bỏ sót chủ đề nào.

▸ XỬ LÝ TRÙNG LẶP: Nếu nhiều phần có chủ đề giống/tương tự → GỘP lại thành 1 node,
  merge children lại.

▸ ĐỘ SÂU: Tối thiểu 2 cấp, tối đa 5 cấp.
  - Cấp 1 (root): Toàn bộ tài liệu
  - Cấp 2: Các chủ đề/chương lớn
  - Cấp 3+: Các mục con chi tiết

▸ SỐ LƯỢNG NODE: Giữ NGẮN GỌN, chỉ các chủ đề chính:
  - Tài liệu ngắn (<2000 từ): 10-20 node tổng
  - Tài liệu trung bình (2000-10000 từ): 20-30 node tổng
  - Tài liệu dài (>10000 từ): 30-50 node tổng
  KHÔNG tạo quá 60 node. Ưu tiên gộp ý nhỏ, chỉ giữ chủ đề chính.

▸ CẤU TRÚC MỖI NODE — CHỈ CẦN:
  - "id": unique — root dùng "root", con dùng "node_1", "node_1_1", v.v.
  - "title": Tiêu đề ngắn gọn, rõ ràng (dưới 80 ký tự)
  - "children": Mảng node con (mảng rỗng [] nếu là node lá)

▸ KHÔNG viết summary, KHÔNG viết key_points. Chỉ cần id, title, children.

▸ SẮP XẾP: Sắp xếp các node theo trình tự LOGIC của tài liệu gốc,
  không xáo trộn thứ tự.

▸ OUTPUT: Trả về CHỈ JSON hợp lệ — cây root duy nhất. Không markdown fences, không giải thích.\
"""

_NODE_CONTENT_SYSTEM_PROMPT = """\
Bạn là chuyên gia phân tích tài liệu. Nhiệm vụ: dựa trên các đoạn trích từ tài liệu \
được cung cấp, viết nội dung CHI TIẾT cho chủ đề được yêu cầu.

═══ YÊU CẦU ═══

▸ Viết summary CHI TIẾT (150-300 từ): tóm tắt đầy đủ nội dung của chủ đề,
  bao gồm thông tin cụ thể, số liệu, ví dụ nếu có trong tài liệu.
  KHÔNG viết chung chung kiểu "phần này nói về...".

▸ Liệt kê 3-6 key_points: các bullet point cụ thể, chứa thông tin thực tế.

▸ Viết bằng CÙNG NGÔN NGỮ với tài liệu gốc.
▸ Giữ nguyên thuật ngữ chuyên ngành, tên riêng, số liệu.
▸ CHỈ dùng thông tin có trong các đoạn trích. Không suy diễn, không bịa số.

▸ OUTPUT: Trả về CHỈ JSON hợp lệ:
{
  "summary": "...",
  "key_points": ["...", "..."]
}
Không markdown fences, không giải thích.\
"""


# --------------------------------------------------------------------------- #
# Chia mẻ
# --------------------------------------------------------------------------- #
def build_batches(texts: list[str]) -> list[str]:
    """Gom chunk thành 4-8 mẻ, cắt ở ranh giới mục chứ không cắt giữa mục.

    Chunk đã xếp đúng thứ tự trong file gốc. Cắt đều theo số lượng thì một mục
    hay bị chia đôi giữa hai mẻ, và model ở pha MAP sẽ khai nó thành hai chủ đề
    khác nhau - pha REDUCE không phải lúc nào cũng gộp lại được.
    """
    total = len(texts)
    if total == 0:
        return []

    starts = [0] + [i for i in range(1, total) if _HEADER_RE.match(texts[i])]
    sections = [texts[s:e] for s, e in zip(starts, starts[1:] + [total], strict=True)]

    target = max(1, min(MAP_TARGET_BATCHES, MAP_MAX_BATCHES, total))
    target = max(target, min(MAP_MIN_BATCHES, total))

    if len(sections) <= target:
        batches = ["\n\n".join(section) for section in sections]
    else:
        # Dồn các mục liền kề lại cho tới khi mẻ đủ dày, rồi mở mẻ mới.
        avg = total / target
        batches = []
        current: list[str] = []
        count = 0
        for section in sections:
            if current and count + len(section) > avg * 1.3:
                batches.append("\n\n".join(current))
                current, count = [], 0
            current.extend(section)
            count += len(section)
        if current:
            batches.append("\n\n".join(current))

    # Vẫn quá nhiều mẻ: gộp cặp liền kề ngắn nhất cho tới khi đạt trần.
    while len(batches) > MAP_MAX_BATCHES:
        pairs = [(len(batches[i]) + len(batches[i + 1]), i) for i in range(len(batches) - 1)]
        _, index = min(pairs)
        batches[index] = batches[index] + "\n\n" + batches.pop(index + 1)

    return batches


# --------------------------------------------------------------------------- #
# Pha MAP-REDUCE
# --------------------------------------------------------------------------- #
async def generate_skeleton(
    batches: list[str],
    doc_name: str,
    word_count: int = 0,
) -> dict[str, Any]:
    """Dựng khung cây (chỉ tiêu đề, chưa có nội dung) từ các mẻ nội dung."""
    if not batches:
        return {"id": "root", "title": doc_name, "children": []}

    # Mẻ đầu chạy một mình: hỏng ở đây thường là hỏng cấu hình (model sai tên,
    # vLLM chưa lên), và để cả 8 lượt cùng lao vào rồi cùng hỏng thì log đầy lỗi
    # giống nhau mà nguyên nhân vẫn chỉ có một.
    per_batch = [await _map_batch(batches[0], 0)]
    if len(batches) > 1:
        per_batch.extend(
            await asyncio.gather(
                *(_map_batch(batch, i) for i, batch in enumerate(batches[1:], 1))
            )
        )

    found = sum(len(nodes) for nodes in per_batch)
    logger.info("Sơ đồ tư duy %s: pha MAP xong, %d mẻ, %d chủ đề thô",
                doc_name, len(batches), found)

    tree = await _reduce_tree(per_batch, doc_name, word_count)
    tree = _cap_nodes(tree, get_settings().mindmap_max_nodes)
    _assign_ids(tree)
    logger.info("Sơ đồ tư duy %s: dựng xong %d mục", doc_name, count_nodes(tree))
    return tree


async def _map_batch(text: str, index: int) -> list[dict[str, Any]]:
    """MAP: một mẻ -> danh sách chủ đề. Mẻ hỏng trả rỗng, không kéo cả cây theo."""
    cfg = get_settings()
    messages = [
        {"role": "system", "content": _MAP_SYSTEM_PROMPT},
        {"role": "user", "content": f"Đây là phần {index + 1} của tài liệu. "
                                    f"Xác định tất cả chủ đề/mục có trong đoạn này:\n\n{text}"},
    ]
    try:
        parsed = await _chat_json(messages, max_tokens=cfg.mindmap_map_max_tokens, phase="MAP")
    except (LLMError, ValueError) as exc:
        logger.warning("Sơ đồ tư duy: mẻ %d lỗi - %s", index, exc)
        return []

    nodes = parsed.get("nodes")
    if not isinstance(nodes, list):
        logger.warning("Sơ đồ tư duy: mẻ %d trả về 'nodes' không phải danh sách", index)
        return []
    return [n for n in nodes if isinstance(n, dict) and n.get("title")]


async def _reduce_tree(
    per_batch: list[list[dict[str, Any]]],
    doc_name: str,
    word_count: int,
) -> dict[str, Any]:
    """REDUCE: gộp mọi danh sách chủ đề thành một cây."""
    cfg = get_settings()
    parts = [
        f"--- Phần {i + 1} ---\n{json.dumps(nodes, ensure_ascii=False, indent=1)}"
        for i, nodes in enumerate(per_batch)
        if nodes
    ]
    if not parts:
        return {"id": "root", "title": doc_name, "children": []}

    if word_count < 2000:
        scale = f"Tài liệu NGẮN (~{word_count} từ) → tạo 10-20 node tổng, 2-3 cấp."
    elif word_count < 10000:
        scale = f"Tài liệu TRUNG BÌNH (~{word_count} từ) → tạo 20-30 node tổng, 3-4 cấp."
    else:
        scale = f"Tài liệu DÀI (~{word_count} từ) → tạo 30-50 node tổng, 3-5 cấp."

    messages = [
        {"role": "system", "content": _REDUCE_SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Tên tài liệu: {doc_name}\n"
            f"Quy mô: {scale}\n"
            f"Tổng số phần đã phân tích: {len(per_batch)}\n\n"
            f"DANH SÁCH NODE TỪ CÁC PHẦN:\n{'━' * 40}\n"
            + "\n\n".join(parts)
            + f"\n{'━' * 40}\n\n"
            f"Tổng hợp tất cả nodes trên thành MỘT cây sơ đồ tư duy hoàn chỉnh. "
            f"Root node title là '{doc_name}'. Gộp các chủ đề trùng lặp, sắp xếp logic."
        )},
    ]

    try:
        tree = await _chat_json(messages, max_tokens=cfg.mindmap_reduce_max_tokens, phase="REDUCE")
    except (LLMError, ValueError) as exc:
        # Pha MAP đã tốn vài lượt LLM và kết quả của nó vẫn còn nguyên giá trị.
        # Ghép phẳng còn hơn vứt đi rồi bắt người dùng chạy lại từ đầu.
        logger.error("Sơ đồ tư duy %s: pha REDUCE lỗi (%s) - ghép phẳng thay thế",
                     doc_name, exc)
        return _flat_tree(per_batch, doc_name)

    return _validate_tree(tree, doc_name)


# --------------------------------------------------------------------------- #
# Nội dung một mục (sinh khi người dùng bấm vào)
# --------------------------------------------------------------------------- #
async def generate_node_content(node_title: str, chunks: list[str]) -> dict[str, Any]:
    """Viết nội dung cho một mục từ các đoạn trích đã truy hồi được."""
    if not chunks:
        return {
            "summary": f"Không tìm thấy nội dung liên quan đến '{node_title}' trong tài liệu.",
            "key_points": [],
        }

    cfg = get_settings()
    messages = [
        {"role": "system", "content": _NODE_CONTENT_SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Chủ đề cần viết nội dung: {node_title}\n\n"
            f"CÁC ĐOẠN TRÍCH TỪ TÀI LIỆU:\n{'━' * 40}\n"
            + "\n\n---\n\n".join(chunks)
            + f"\n{'━' * 40}\n\n"
            f"Dựa trên các đoạn trích trên, viết nội dung chi tiết cho chủ đề '{node_title}'."
        )},
    ]

    result = await _chat_json(messages, max_tokens=cfg.mindmap_node_max_tokens, phase="nội dung mục")
    points = result.get("key_points")
    return {
        "summary": str(result.get("summary") or ""),
        "key_points": [str(p) for p in points if str(p).strip()] if isinstance(points, list) else [],
    }


# --------------------------------------------------------------------------- #
# Gọi LLM và bóc JSON
# --------------------------------------------------------------------------- #
async def _chat_json(
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    phase: str,
) -> dict[str, Any]:
    """Một lượt LLM trả JSON, có vá lại phần bị cắt vì hết hạn mức token.

    Không dùng `LLMClient.chat_json`: cây ở pha REDUCE dài tới mức chạm trần
    `max_tokens` là chuyện thường, và khi đó JSON về thiếu vài dấu đóng ngoặc.
    Bỏ cả cây đi vì mấy ký tự cuối là đắt - `_repair_truncated_json` đóng nốt
    ngoặc và giữ lại phần đã sinh được.
    """
    cfg = get_settings()
    raw = await get_llm().chat(
        messages,
        max_tokens=max_tokens,
        # Suy luận ăn hạn mức trước khi model kịp viết JSON, mà việc ở đây là
        # liệt kê và sắp xếp chứ không phải lập luận.
        thinking=False,
        timeout=cfg.mindmap_timeout,
        extra={"response_format": {"type": "json_object"}},
    )
    parsed = _extract_json(raw, phase=phase, max_tokens=max_tokens)
    if not isinstance(parsed, dict):
        raise ValueError(f"Pha {phase}: model trả về {type(parsed).__name__}, cần một object JSON")
    return parsed


def _extract_json(text: str, *, phase: str, max_tokens: int) -> Any:
    """Bóc JSON từ phản hồi: markdown fences, chữ thừa hai đầu, JSON bị cắt."""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fence = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except json.JSONDecodeError:
            pass

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidate = text[start:end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            repaired = _repair_json(candidate)
            if repaired is not None:
                return repaired

    if start != -1:
        repaired = _repair_truncated_json(text[start:])
        if repaired is not None:
            logger.warning("Pha %s: JSON bị cắt ở max_tokens=%d, đã vá lại phần đóng ngoặc",
                           phase, max_tokens)
            return repaired

    raise ValueError(f"Pha {phase}: không đọc được JSON từ phản hồi. Đầu phản hồi: {text[:200]}…")


def _repair_json(text: str) -> Any | None:
    """Vá lỗi thường gặp: ký tự điều khiển lọt vào, dấu phẩy thừa trước ngoặc đóng."""
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


def _repair_truncated_json(text: str) -> Any | None:
    """Đóng nốt ngoặc cho JSON bị cắt giữa chừng; trả None nếu vá không nổi."""
    truncated = text.rstrip()

    # Lùi về một điểm "sạch" - kết thúc ở ngoặc đóng - rồi mới đóng phần còn thiếu.
    for _ in range(10):
        truncated = truncated.rstrip()
        if not truncated:
            return None
        last = truncated[-1]
        if last in "}]":
            break
        if last == ",":
            truncated = truncated[:-1]
        elif last == ":":
            # Cắt đúng lúc vừa viết xong tên khoá: bỏ luôn cả khoá đó.
            quote = truncated.rfind('"', 0, len(truncated) - 1)
            if quote <= 0:
                truncated = truncated[:-1]
                continue
            before = truncated[:quote].rstrip()
            truncated = before[:-1] if before.endswith(",") else before
        else:
            cut = max(truncated.rfind("}"), truncated.rfind("]"))
            if cut <= 0:
                return None
            truncated = truncated[:cut + 1]
    else:
        return None

    braces = truncated.count("{") - truncated.count("}")
    brackets = truncated.count("[") - truncated.count("]")
    if braces < 0 or brackets < 0:
        return None

    candidate = re.sub(r",\s*([}\]])", r"\1", truncated + "]" * brackets + "}" * braces)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------- #
# Thao tác trên cây
# --------------------------------------------------------------------------- #
def count_nodes(tree: dict[str, Any]) -> int:
    return 1 + sum(count_nodes(c) for c in tree.get("children", []))


def find_node(tree: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    if tree.get("id") == node_id:
        return tree
    for child in tree.get("children", []):
        found = find_node(child, node_id)
        if found is not None:
            return found
    return None


def strip_for_api(tree: dict[str, Any]) -> dict[str, Any]:
    """Khung cây gửi cho giao diện: bỏ nội dung đã sinh, chỉ giữ cấu trúc.

    Nội dung của mọi mục cộng lại là hàng chục nghìn ký tự cho một thứ mà người
    dùng xem từng mục một. Giao diện xin riêng khi bấm vào mục.
    """
    return {
        "id": tree.get("id", "root"),
        "title": tree.get("title", ""),
        "has_content": bool(tree.get("summary")),
        "children": [strip_for_api(c) for c in tree.get("children", [])],
    }


def _assign_ids(tree: dict[str, Any], prefix: str = "root") -> None:
    """Gán id theo đường đi từ gốc. Id do model đặt không đáng tin là duy nhất."""
    tree["id"] = prefix
    for i, child in enumerate(tree.get("children", []), 1):
        _assign_ids(child, f"node_{i}" if prefix == "root" else f"{prefix}_{i}")


def _cap_nodes(tree: dict[str, Any], max_nodes: int) -> dict[str, Any]:
    """Cắt bớt theo BFS: giữ các mục cấp cao, tỉa dần từ lá xa gốc nhất."""
    if max_nodes <= 0:
        tree["children"] = []
        return tree

    before = count_nodes(tree)
    if before <= max_nodes:
        return tree

    kept: set[int] = set()
    queue: deque[dict[str, Any]] = deque([tree])
    while queue and len(kept) < max_nodes:
        node = queue.popleft()
        kept.add(id(node))
        queue.extend(c for c in node.get("children", []) if isinstance(c, dict))

    _prune(tree, kept)
    logger.info("Sơ đồ tư duy: cắt từ %d xuống %d mục (trần %d)",
                before, count_nodes(tree), max_nodes)
    return tree


def _prune(node: dict[str, Any], kept: set[int]) -> None:
    children = [c for c in node.get("children", []) if isinstance(c, dict) and id(c) in kept]
    for child in children:
        _prune(child, kept)
    node["children"] = children


def _validate_tree(tree: Any, doc_name: str) -> dict[str, Any]:
    """Nắn cây do model trả về về đúng hình dạng trước khi lưu."""
    if not isinstance(tree, dict):
        return {"id": "root", "title": doc_name, "children": []}

    # Model hay bọc thêm một lớp {"root": {...}}.
    inner = tree.get("root")
    if isinstance(inner, dict) and "children" in inner:
        tree = inner

    tree.setdefault("id", "root")
    if not tree.get("title"):
        tree["title"] = doc_name
    _fix_node(tree)
    return tree


def _fix_node(node: dict[str, Any]) -> None:
    if not node.get("title"):
        node["title"] = "Không tiêu đề"
    children = node.get("children")
    if not isinstance(children, list):
        node["children"] = []
        return
    fixed: list[dict[str, Any]] = []
    for child in children:
        if isinstance(child, dict):
            _fix_node(child)
            fixed.append(child)
        elif str(child).strip():
            # Model trả về chuỗi thay vì object: giữ lại tiêu đề chứ không bỏ mục.
            fixed.append({"title": str(child), "children": []})
    node["children"] = fixed


def _flat_tree(per_batch: list[list[dict[str, Any]]], doc_name: str) -> dict[str, Any]:
    """Cây dự phòng khi pha REDUCE hỏng: ghép phẳng chủ đề của mọi mẻ."""
    children = [n for nodes in per_batch for n in nodes if isinstance(n, dict) and n.get("title")]
    tree = {"id": "root", "title": doc_name, "children": children}
    _fix_node(tree)
    return tree

"""Workflow 3 - nhánh NGUỒN LÀ MỘT TÀI LIỆU TẢI LÊN.

Nhánh CSDL (`drafting.py`) lấy số từ ERP: mỗi con số có một trường dữ liệu đứng
sau, nên van chắn số bảo đảm được "số này truy về được nguồn". Nhánh này không
có thứ đó - tài liệu là văn xuôi, số nằm rải trong câu.

Bảo đảm còn lại, nói thẳng để không ai tưởng nhầm là mạnh hơn thực tế:

    số trong báo cáo PHẢI xuất hiện nguyên văn trong tài liệu nguồn.

Tức là chặn được model bịa ra con số mới, nhưng KHÔNG chặn được model lấy đúng
một con số có thật rồi đặt sai chỗ. Đó là lý do prompt cấm cộng/trừ/tính tỷ lệ:
mọi phép tính đều sinh ra số không có trong tài liệu, và nếu nó tình cờ trùng
một số khác trong đó thì van không bắt được.

Bất biến giữ chung với nhánh CSDL: thứ gửi cho model và tập số hợp lệ của van
chắn PHẢI bằng nhau. Ở đây cả hai đều là nguyên văn tài liệu, nên chúng bằng
nhau theo cách dựng - không phải nhờ ai đó nhớ đồng bộ hai chỗ.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.prompts import (
    DRAFT_DOC_OUTLINE_SYSTEM,
    DRAFT_DOC_OUTLINE_USER,
    DRAFT_DOC_SECTION_SYSTEM,
    DRAFT_DOC_SECTION_USER,
)
from app.core.config import get_settings
from app.services.llm import LLMError, get_llm

logger = logging.getLogger(__name__)

# Cắt bớt tài liệu quá dài trước khi đưa vào prompt. Cắt chứ không tóm tắt: tóm
# tắt là đưa thêm một lượt LLM vào giữa nguồn và van chắn, và từ đó không con số
# nào còn truy về được nguyên văn nữa.
MAX_DOC_CHARS = 24_000


def _truncate(text: str, limit: int = MAX_DOC_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


async def read_document_node(state: dict[str, Any]) -> dict[str, Any]:
    """Đọc file đã tải lên thành Markdown, rút sẵn phần đọc được tất định."""
    import anyio

    from app.services import storage

    file_id = (state.get("file_id") or "").strip()
    if not file_id:
        return {"missing_input": ["file_id"],
                "error": "Chưa có tài liệu nào được tải lên để soạn báo cáo"}

    try:
        ref = storage.resolve(file_id)
    except storage.StorageError as exc:
        return {"error": f"Không mở được tài liệu: {exc}"}

    from app.documents.collect import read_unit_report

    report = await anyio.to_thread.run_sync(lambda: read_unit_report(ref.path))

    try:
        from app.rag.converter import get_converter

        markdown = await anyio.to_thread.run_sync(
            lambda: get_converter().convert(ref.path).text
        )
    except Exception as exc:  # noqa: BLE001 - file hỏng là lỗi đầu vào, không phải sự cố
        return {"error": f"Không đọc được nội dung tài liệu {ref.name}: {exc}"}

    if not markdown.strip():
        return {"error": f"Tài liệu {ref.name} không có nội dung đọc được"}

    noi_dung, da_cat = _truncate(markdown)
    notes: list[str] = [
        f"Số liệu lấy từ tài liệu {ref.name}, không đối chiếu với CSDL nghiệp vụ."
    ]
    if da_cat:
        notes.append(
            f"Tài liệu dài {len(markdown):,} ký tự, chỉ {MAX_DOC_CHARS:,} ký tự đầu "
            "được đưa vào báo cáo."
        )
    if report.error:
        # Không có bảng kiểm kê không phải lỗi: tài liệu có thể là công văn thuần
        # chữ. Chỉ ghi lại để người đọc biết vì sao báo cáo không có bảng.
        notes.append(f"Không đọc được bảng số liệu trong tài liệu ({report.error}).")

    return {
        "data": {
            "nguon_so_lieu": "tai_lieu",
            "ten_tai_lieu": ref.name,
            "so_ky_hieu_nguon": report.so_ky_hieu,
            "ngay_van_ban_nguon": report.ngay_van_ban,
            # Nguyên văn tài liệu: vừa là thứ model đọc, vừa là tập số hợp lệ.
            "noi_dung": noi_dung,
        },
        "data_notes": notes,
        "source_document": {
            "file_id": ref.file_id,
            "ten_tai_lieu": ref.name,
            "so_ky_hieu": report.so_ky_hieu,
            "ngay_van_ban": report.ngay_van_ban,
            "co_bang_so_lieu": report.inventory is not None,
            "so_ky_tu": len(markdown),
        },
    }


async def outline_node(state: dict[str, Any]) -> dict[str, Any]:
    """LLM đề xuất các mục, bám nội dung thật của tài liệu.

    Dàn ý được nhét vào chỗ `template` mà các node sau (`validate`, `export`,
    `register`) vốn đã đọc - nhánh này vì thế dùng lại nguyên ba node đó thay vì
    chép lại logic xuất file và ghi sổ.
    """
    if state.get("error") or state.get("missing_input"):
        return {}

    data = state["data"]
    tieu_de_mac_dinh = f"Báo cáo tổng hợp nội dung {data['ten_tai_lieu']}"
    muc: list[dict[str, Any]] = []
    try:
        result = await get_llm().chat_json(
            [
                {"role": "system", "content": DRAFT_DOC_OUTLINE_SYSTEM},
                {"role": "user", "content": DRAFT_DOC_OUTLINE_USER.format(
                    ten_tai_lieu=data["ten_tai_lieu"], noi_dung=data["noi_dung"])},
            ],
            model=get_settings().utility_model, temperature=0.1, max_tokens=800,
            thinking=False,
        )
        tieu_de_mac_dinh = str(result.get("tieu_de") or tieu_de_mac_dinh).strip()
        for i, item in enumerate(result.get("muc") or []):
            if not isinstance(item, dict):
                continue
            tieu_de = str(item.get("tieu_de") or "").strip()
            if not tieu_de:
                continue
            muc.append({
                "id": str(item.get("id") or f"m{i + 1}"),
                "title": tieu_de,
                "type": "llm",
                "narrative": str(item.get("huong_dan") or "").strip(),
            })
    except (LLMError, Exception) as exc:  # noqa: BLE001 - luôn phải có đường lùi
        logger.warning("Không lập được dàn ý từ tài liệu: %s", exc)

    if not muc:
        # Dàn ý hỏng thì vẫn ra được một báo cáo dùng được, thay vì trả lỗi.
        muc = [
            {"id": "noi_dung", "title": "I. NỘI DUNG TÀI LIỆU", "type": "llm",
             "narrative": "Tóm tắt nội dung chính của tài liệu, giữ nguyên các số liệu."},
            {"id": "nhan_xet", "title": "II. NHẬN XÉT", "type": "llm",
             "narrative": "Nêu nhận xét bám sát nội dung đã trình bày ở mục I."},
        ]

    return {
        "template": {
            "ma_template": "BC_TU_TAI_LIEU",
            "ten_bao_cao": tieu_de_mac_dinh,
            # Không có file mẫu: `build_docx` tự dựng bằng code khi đường dẫn rỗng.
            "file_path": "",
            "fields": {"sections": muc, "meta": {}},
        },
        "template_choice": {"confidence": 1.0, "reason": "Dàn ý dựng từ chính tài liệu"},
    }


async def render_node(state: dict[str, Any]) -> dict[str, Any]:
    """Viết từng mục, chỉ được dùng nguyên văn tài liệu làm nguồn."""
    if state.get("error") or state.get("missing_input"):
        return {}

    template = state["template"]
    data = state["data"]
    noi_dung = data["noi_dung"]
    llm = get_llm()
    sections: list[dict[str, Any]] = []

    for spec in template["fields"].get("sections", []):
        paragraphs: list[str] = []
        try:
            result = await llm.chat_json(
                [
                    {"role": "system", "content": DRAFT_DOC_SECTION_SYSTEM},
                    {"role": "user", "content": DRAFT_DOC_SECTION_USER.format(
                        report_title=template["ten_bao_cao"],
                        section_title=spec["title"],
                        narrative=spec.get("narrative", ""),
                        noi_dung=noi_dung)},
                ],
                temperature=0.2, max_tokens=900, thinking=False,
            )
            paragraphs = [str(p).strip() for p in (result.get("paragraphs") or [])
                          if str(p).strip()]
        except (LLMError, Exception) as exc:  # noqa: BLE001
            logger.warning("Không viết được mục %s: %s", spec["id"], exc)

        sections.append({
            "id": spec["id"], "title": spec["title"], "kind": "llm",
            "paragraphs": paragraphs, "table": None,
            "llm_written": paragraphs,
            "llm_written_cited": [],
            "refs": [],
            # Tập số hợp lệ = nguyên văn tài liệu, đúng bằng thứ model vừa đọc.
            "source_data": {"noi_dung": noi_dung},
        })

    return {"sections": sections}

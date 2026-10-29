"""Độ ổn định của bước Qwen đặt nhãn/vai trò: chạy N lần trên cùng dữ liệu, đếm cột cho kết quả khác nhau.
    python label_stability.py ../runs/rX Q02,Q03 3
Đọc data.json của lượt chạy (đã có nhãn), xoá nhãn/vai trò rồi đặt lại N lần."""

import asyncio
import copy
import json
import sys
from pathlib import Path

from app.documents.nhan_cot import gan_nhan_cot


async def main() -> None:
    root, ids, n = Path(sys.argv[1]), sys.argv[2].split(","), int(sys.argv[3])
    tong = khac = 0
    for qid in ids:
        for q in json.loads((root / qid / "data.json").read_text(encoding="utf-8")):
            ket_qua = []
            for _ in range(n):
                r = copy.deepcopy(q)
                for c in r["columns"]:
                    for k in ("label", "unit", "role", "cong_duoc"):
                        c.pop(k, None)
                await gan_nhan_cot(r, "")
                ket_qua.append([(c["label"], c.get("role"), c.get("cong_duoc")) for c in r["columns"]])
            for j, col in enumerate(q["columns"]):
                bien = {tuple(k[j]) for k in ket_qua}
                tong += 1
                if len(bien) > 1:
                    khac += 1
                    print(qid, col["name"], "KHÁC NHAU:", sorted(bien))
    print(json.dumps({"cot": tong, "khong_on_dinh": khac, "so_lan": n}, ensure_ascii=False))


asyncio.run(main())

"""OCR PDF scan bằng Qwen3.6 (vLLM đang chạy trên máy này) -> PDF có lớp chữ ẩn đặt đúng toạ độ dòng.

Qwen trả về từng dòng kèm bbox (toạ độ tương đối 0-1000 như dòng Qwen-VL). Toạ độ y của Qwen trôi tới
±1 dòng nên được "bắt dính" vào dải dòng thật lấy từ điểm ảnh (snap_to_bands; xử lý ảnh, không model khác).
Lớp chữ được chèn
vô hình (render_mode=3) vào đúng vị trí dòng trên trang gốc, nên:
  - rule-based (parse_document + build_outline) và HiPS (page_lines) đọc CHUNG một lớp chữ;
  - khoảng trắng trên/dưới mà HiPS cần được giữ đúng như trên ảnh.

    uv run python tests/layout_experiment/qwen_ocr.py --pdf tests/fixtures/layout_pdf/12.pdf --pages 1,6 --overlay   # thử 2 trang
    uv run python tests/layout_experiment/qwen_ocr.py --pdf tests/fixtures/layout_pdf/12.pdf                        # cả tệp
Ra test_results/layout_grammar/data/pdf/<tên>.pdf, test_results/layout_grammar/ocr/<tên>/{pages.json, ocr_stats.json, overlay_p*.png}
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import random
import time
from pathlib import Path

import httpx
import numpy as np
import pymupdf
from tqdm import tqdm

BACKEND = Path(__file__).resolve().parents[2]
WORK = Path(os.environ.get("LAYOUT_WORK", BACKEND / "test_results" / "layout_grammar"))
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

PROMPT = """Read this scanned page. Return EVERY text line in reading order, top to bottom, with its
bounding box. Coordinates are relative to the image: x and y from 0 to 1000 (0,0 = top-left).
Copy the text exactly as printed, Vietnamese diacritics included; do not translate, summarise or fix it.
One entry per visual line (a wrapped paragraph gives several lines).
Return ONLY JSON: {"lines":[{"text":"...","bbox":[x1,y1,x2,y2]}]}"""

SCHEMA = {"type": "object", "properties": {"lines": {"type": "array", "items": {
    "type": "object", "properties": {"text": {"type": "string"},
                                     "bbox": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4}},
    "required": ["text", "bbox"]}}}, "required": ["lines"]}


RETRY_HINT = (" The first line is only a browser header (date, about:blank); it is NOT the end of the page. "
              "Continue through the whole body text down to the page footer.")

# Thử lần lượt khi trang bị "sập" (model đóng mảng JSON ngay sau dòng tiêu đề trình duyệt):
# (dpi, prompt có nhắc, dùng json_schema)
FALLBACKS = [(200, True, True), (150, True, False), (120, True, True)]


async def ocr_page(http, endpoint, model, png: bytes, sem, hint: bool = False, schema: bool = True) -> dict:
    fmt = ({"type": "json_schema", "json_schema": {"name": "ocr", "schema": SCHEMA}} if schema
           else {"type": "json_object"})
    body = {"model": model, "temperature": 0, "max_tokens": 12000, "chat_template_kwargs": {"enable_thinking": False},
            "response_format": fmt,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
                {"type": "text", "text": PROMPT + (RETRY_HINT if hint else "")}]}]}
    async with sem:
        err = ""
        for attempt in range(5):
            t0 = time.perf_counter()
            try:
                r = await http.post(f"{endpoint}/chat/completions", json=body)
                if r.status_code == 200:
                    d = r.json()
                    c = d["choices"][0]
                    content = c["message"]["content"] or ""
                    lines = json.loads(content[content.find("{"):content.rfind("}") + 1]).get("lines", [])
                    u = d.get("usage", {})
                    return {"ok": True, "lines": lines, "latency_s": round(time.perf_counter() - t0, 2),
                            "in_tokens": u.get("prompt_tokens"), "out_tokens": u.get("completion_tokens"),
                            "finish_reason": c.get("finish_reason")}
                err = f"HTTP {r.status_code}: {r.text[:150]}"
                if r.status_code not in (429, 500, 502, 503, 504):
                    break
            except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
                err = f"{type(exc).__name__}: {exc}"[:200]
            await asyncio.sleep(min(30, 2 ** attempt + random.random()))
        return {"ok": False, "error": err, "lines": []}


def valid_lines(lines: list) -> tuple[list[dict], int]:
    out, bad = [], 0
    for ln in lines:
        b, t = ln.get("bbox"), (ln.get("text") or "").strip()
        if not t or not (isinstance(b, list) and len(b) == 4 and all(isinstance(v, (int, float)) for v in b)):
            bad += 1; continue
        x1, y1, x2, y2 = [max(0, min(1000, v)) for v in b]
        if x2 <= x1 or y2 <= y1:
            bad += 1; continue
        out.append({"text": " ".join(t.split()), "bbox": [x1, y1, x2, y2]})
    return out, bad


def pixel_bands(page, dpi: int) -> list[tuple[float, float]]:
    """Dải dòng chữ thật trên ảnh: hàng điểm ảnh có đủ điểm tối, gom liền nhau. Toạ độ 0-1000."""
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w)
    on = (a < 110).sum(axis=1) > max(3, 0.002 * pix.w)
    bands, start = [], None
    for y, v in enumerate(list(on) + [False]):
        if v and start is None:
            start = y
        elif not v and start is not None:
            if y - start >= 4:
                bands.append((start / pix.h * 1000, y / pix.h * 1000))
            start = None
    return bands


def snap_to_bands(lines: list[dict], bands: list[tuple[float, float]]) -> tuple[list[dict], dict]:
    """Bbox y của Qwen trôi tới ±1 dòng; thứ tự dòng thì đúng. Ghép tuần tự dòng -> dải ảnh bằng quy
    hoạch động (giữ thứ tự, tối thiểu |lệch y|), cho hai dòng chung một dải khi chúng nằm cạnh nhau
    theo chiều ngang (văn bản hai cột ở đầu trang). Lấy y theo dải thật, giữ x của Qwen."""
    if not lines or not bands:
        return lines, {"snapped": 0}
    L, B = len(lines), len(bands)
    yc = [(l["bbox"][1] + l["bbox"][3]) / 2 for l in lines]
    bc = [(b[0] + b[1]) / 2 for b in bands]
    INF = float("inf")
    cost = [[INF] * B for _ in range(L)]
    back = [[-1] * B for _ in range(L)]
    for j in range(B):
        cost[0][j] = abs(yc[0] - bc[j])
    for i in range(1, L):
        side = lines[i]["bbox"][0] >= lines[i - 1]["bbox"][2] - 5     # dòng i nằm bên phải dòng i-1
        best_prev, best_k = INF, -1
        for j in range(B):
            if j > 0 and cost[i - 1][j - 1] < best_prev:              # tiền tố tốt nhất trên các dải < j
                best_prev, best_k = cost[i - 1][j - 1], j - 1
            stay = cost[i - 1][j] + (0 if side else 25)               # chung dải: chỉ rẻ khi đứng cạnh nhau
            if stay <= best_prev:
                cost[i][j], back[i][j] = stay + abs(yc[i] - bc[j]), j
            else:
                cost[i][j], back[i][j] = best_prev + abs(yc[i] - bc[j]), best_k
    j = min(range(B), key=lambda k: cost[L - 1][k])
    assign = [0] * L
    for i in range(L - 1, -1, -1):
        assign[i] = j
        j = back[i][j]
    out, shifts = [], []
    for ln, j in zip(lines, assign):
        y1, y2 = bands[j]
        shifts.append(abs((y1 + y2) / 2 - (ln["bbox"][1] + ln["bbox"][3]) / 2))
        out.append({**ln, "bbox": [ln["bbox"][0], round(y1, 1), ln["bbox"][2], round(y2, 1)], "bbox_qwen": ln["bbox"]})
    return out, {"snapped": L, "bands": B, "mean_shift": round(sum(shifts) / L, 2), "max_shift": round(max(shifts), 2)}


def write_text_layer(page, lines: list[dict]) -> None:
    w, h = page.rect.width, page.rect.height
    for ln in lines:
        x1, y1, x2, y2 = ln["bbox"]
        rect = pymupdf.Rect(x1 * w / 1000, y1 * h / 1000, x2 * w / 1000, y2 * h / 1000)
        size = max(4.0, rect.height * 0.8)
        page.insert_text((rect.x0, rect.y1 - rect.height * 0.2), ln["text"], fontsize=size,
                         fontname="vn", fontfile=FONT, render_mode=3)


async def run(args) -> None:
    src = pymupdf.open(args.pdf)
    pages = list(range(src.page_count)) if not args.pages else [int(p) - 1 for p in args.pages.split(",")]
    model = httpx.get(f"{args.endpoint}/models", timeout=20).json()["data"][0]["id"]
    pngs = {i: src[i].get_pixmap(dpi=args.dpi).tobytes("png") for i in pages}
    sem = asyncio.Semaphore(args.concurrency)
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as http:
        bar = tqdm(total=len(pages), desc="Qwen OCR", unit="trang")
        async def one(i):
            r = await ocr_page(http, args.endpoint, model, pngs[i], sem)
            bar.update(1)
            return i, r
        results = dict(await asyncio.gather(*(one(i) for i in pages)))
        bar.close()

        # Trang "sập": ít hơn một nửa số dải dòng thật -> chạy lại với cấu hình khác, giữ lần nhiều dòng nhất.
        nbands = {i: len(pixel_bands(src[i], args.dpi)) for i in pages}
        def collapsed(i):
            return len(results[i]["lines"]) < 0.5 * max(1, nbands[i])
        for dpi, hint, schema in FALLBACKS:
            todo = [i for i in pages if collapsed(i)]
            if not todo:
                break
            alt_png = {i: src[i].get_pixmap(dpi=dpi).tobytes("png") for i in todo}
            bar = tqdm(total=len(todo), desc=f"chạy lại {dpi}dpi hint={hint} schema={schema}", unit="trang")
            async def redo(i):
                r = await ocr_page(http, args.endpoint, model, alt_png[i], sem, hint=hint, schema=schema)
                bar.update(1)
                return i, r
            for i, r in await asyncio.gather(*(redo(i) for i in todo)):
                r["retry_config"] = {"dpi": dpi, "hint": hint, "schema": schema}
                if len(r["lines"]) > len(results[i]["lines"]):
                    results[i] = r
            bar.close()
    wall = time.perf_counter() - t0

    name = args.pdf.stem
    out_dir = WORK / "ocr" / name
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_out = WORK / "data" / "pdf" / f"{name}.pdf"
    pdf_out.parent.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open()
    per_page = []
    for i in pages:
        r = results[i]
        lines, bad = valid_lines(r["lines"])
        lines, snap = snap_to_bands(lines, pixel_bands(src[i], args.dpi))
        doc.insert_pdf(src, from_page=i, to_page=i)          # giữ ảnh scan gốc làm nền
        write_text_layer(doc[-1], lines)
        per_page.append({"page": i + 1, "ok": r["ok"], "lines": len(lines), "invalid_lines": bad,
                         "latency_s": r.get("latency_s"), "in_tokens": r.get("in_tokens"), "out_tokens": r.get("out_tokens"),
                         "finish_reason": r.get("finish_reason"), "error": r.get("error"), "snap": snap,
                         "retry_config": r.get("retry_config"), "collapsed": len(lines) < 0.5 * max(1, snap.get("bands", 1))})
        r["lines"] = lines
        if args.overlay:
            ov = pymupdf.open(); ov.insert_pdf(src, from_page=i, to_page=i)
            pg = ov[0]
            for ln in lines:
                x1, y1, x2, y2 = ln["bbox"]
                pg.draw_rect(pymupdf.Rect(x1 * pg.rect.width / 1000, y1 * pg.rect.height / 1000,
                                          x2 * pg.rect.width / 1000, y2 * pg.rect.height / 1000), color=(1, 0, 0), width=0.6)
            pg.get_pixmap(dpi=70).save(out_dir / f"overlay_p{i + 1}.png")
    doc.save(pdf_out, garbage=3, deflate=True)
    (out_dir / "pages.json").write_text(json.dumps({str(i + 1): results[i] for i in pages}, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = [p for p in per_page if p["ok"]]
    stats = {"source": str(args.pdf), "model": model, "dpi": args.dpi, "pages": len(pages),
             "failed_pages": [p["page"] for p in per_page if not p["ok"]],
             "truncated_pages": [p["page"] for p in per_page if p["finish_reason"] == "length"],
             "retried_pages": [p["page"] for p in per_page if p["retry_config"]],
             "still_collapsed_pages": [p["page"] for p in per_page if p["collapsed"]],
             "lines": sum(p["lines"] for p in per_page), "invalid_lines": sum(p["invalid_lines"] for p in per_page),
             "in_tokens": sum(p["in_tokens"] or 0 for p in ok), "out_tokens": sum(p["out_tokens"] or 0 for p in ok),
             "wall_s": round(wall, 1), "per_page": per_page}
    (out_dir / "ocr_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in stats.items() if k != "per_page"}, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", type=Path, required=True)
    ap.add_argument("--endpoint", default="http://127.0.0.1:15000/v1")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--pages", default="", help="vd. 1,6 để thử; trống = cả tệp")
    ap.add_argument("--overlay", action="store_true", help="vẽ bbox lên ảnh trang để kiểm tra bằng mắt")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()

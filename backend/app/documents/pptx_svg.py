"""Thêm ảnh PNG dự phòng cho ảnh SVG trong .pptx Presenton xuất ra.

VÌ SAO

Presenton ghi icon, nền, đường kẻ thành ảnh SVG thuần: `<a:blip>` không có `r:embed`, chỉ có
`<asvg:svgBlip r:embed=...>` trong phần mở rộng. Chuẩn Office yêu cầu blip trỏ tới một ảnh raster
và SVG chỉ là bản "nâng cấp" - chính PowerPoint khi chèn SVG luôn ghi kèm PNG. Thiếu PNG thì
PowerPoint trên Windows (bản không đọc SVG, chế độ Protected View, trình xem trước của Explorer)
báo "không hiển thị được ảnh" hoặc đòi sửa file; LibreOffice và Google Slides bỏ trống ảnh.

Vẽ lại SVG thành PNG bằng PyMuPDF (đã có trong backend) rồi gắn vào `r:embed` của blip; SVG vẫn
giữ nguyên cho bản PowerPoint đọc được nó.
"""

from __future__ import annotations

import logging
from io import BytesIO

from pptx import Presentation
from pptx.oxml.ns import qn

logger = logging.getLogger(__name__)

_NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main",
       "asvg": "http://schemas.microsoft.com/office/drawing/2016/SVG/main"}
_EMU_PER_INCH = 914400
_DPI = 150
_MAX_PX = 2400


def _png_tu_svg(svg: bytes, width_emu: int, height_emu: int) -> bytes:
    import pymupdf

    doc = pymupdf.open(stream=svg, filetype="svg")
    page = doc[0]
    target_w = max(1.0, width_emu / _EMU_PER_INCH * _DPI)
    target_h = max(1.0, height_emu / _EMU_PER_INCH * _DPI)
    scale = min(target_w / max(page.rect.width, 1), target_h / max(page.rect.height, 1))
    scale = min(scale, _MAX_PX / max(page.rect.width, page.rect.height, 1))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=True)
    return pix.tobytes("png")


def _sua_part(part) -> int:
    fixed = 0
    for pic in part._element.iter(qn("p:pic")):
        blip = pic.find(".//a:blip", _NS)
        if blip is None or blip.get(qn("r:embed")):
            continue
        svg_blip = blip.find(".//asvg:svgBlip", _NS)
        if svg_blip is None or not (rid := svg_blip.get(qn("r:embed"))):
            continue
        ext = pic.find(".//a:xfrm/a:ext", _NS)
        cx = int(ext.get("cx", 0)) if ext is not None else 0
        cy = int(ext.get("cy", 0)) if ext is not None else 0
        try:
            png = _png_tu_svg(part.related_part(rid).blob, cx or _EMU_PER_INCH, cy or _EMU_PER_INCH)
            _, png_rid = part.get_or_add_image_part(BytesIO(png))
        except Exception as exc:  # noqa: BLE001 - một ảnh trang trí hỏng không đáng làm hỏng cả file
            logger.warning("Không vẽ được PNG dự phòng cho %s: %s", rid, exc)
            continue
        blip.set(qn("r:embed"), png_rid)
        fixed += 1
    return fixed


def them_png_du_phong(content: bytes) -> bytes:
    """Trả về .pptx mà mọi ảnh SVG đều có PNG dự phòng. Không có SVG thuần thì trả nguyên bytes."""
    presentation = Presentation(BytesIO(content))
    parts = [s.part for s in presentation.slides]
    for master in presentation.slide_masters:
        parts.append(master.part)
        parts += [layout.part for layout in master.slide_layouts]
    fixed = sum(_sua_part(p) for p in parts)
    if not fixed:
        return content
    out = BytesIO()
    presentation.save(out)
    logger.info("Đã thêm PNG dự phòng cho %d ảnh SVG", fixed)
    return out.getvalue()

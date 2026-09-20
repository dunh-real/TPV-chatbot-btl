"""Rule engine kiểm tra CẤU TRÚC tài liệu - hoàn toàn tất định, không dùng LLM.

Phân vai rõ ràng giữa hai nhánh của workflow 2:
    rule engine  đo được từ chính tệp - dàn ý, thứ bậc mục, đánh số, độ dài đoạn,
                 phông chữ, cỡ chữ, canh lề, khổ giấy, lề trang
    LLM          chữ nghĩa - chính tả, ngữ pháp, diễn đạt, logic

Hỏi LLM về phông chữ là sai về nguyên tắc: nó chỉ nhận được text, không "nhìn" thấy
định dạng, nên câu trả lời sẽ trôi chảy và bịa. Ngược lại, bắt rule engine phán xét
câu văn cũng vậy.

Ở đây không có chỗ nào biết "văn bản hành chính" là gì: cùng một bộ tiêu chí áp cho
công văn, hợp đồng, biên bản họp hay tài liệu kỹ thuật. Cũng vì thế không có tiêu
chí nào kiểu "phải là Times New Roman" - mỗi nơi một quy định. Thứ đo được mà không
cần biết quy định của ai là sự NHẤT QUÁN: lấy cách trình bày của số đông làm chuẩn
rồi chỉ ra chỗ lệch.

Tiêu chí nằm trong `config/rules/*.yaml` để sửa được mà không đụng code.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Literal

import yaml

from app.documents.outline import LETTERS, Marker, Outline, build_outline
from app.documents.parser import Block, DocumentStructure, normalize_font

logger = logging.getLogger(__name__)

Severity = Literal["error", "warning", "info"]
DEFAULT_RULES = Path(__file__).resolve().parents[2] / "config" / "rules" / "chung.yaml"

# Các tiêu chí chỉ chạy được khi đọc được định dạng - tệp .txt/.md hay PDF scan
# thì báo là chưa kiểm, không báo là đạt.
FORMAT_RULES = ("consistency.font", "consistency.size_pt", "consistency.alignment",
                "consistency.line_spacing", "consistency.paragraph_spacing_pt",
                "page.size_mm", "page.margin_mm")


@dataclass(slots=True)
class RuleFinding:
    rule: str
    severity: Severity
    message: str
    block_id: str | None = None
    actual: str | None = None
    expected: str | None = None
    quote: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule, "severity": self.severity, "message": self.message,
            "block_id": self.block_id, "actual": self.actual,
            "expected": self.expected, "quote": self.quote,
        }


@dataclass(slots=True)
class RuleCheckResult:
    status: Literal["done", "partial", "skipped"]
    findings: list[RuleFinding] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    reason: str = ""
    rule_set: str = ""

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")


def _no_format_reason(source_format: str) -> str:
    """Nói đúng lý do không kiểm tra được, đừng gộp mọi thứ thành "scan"."""
    if source_format == "pdf_scan":
        return ("Tài liệu dạng ảnh/scan nên không đọc được phông chữ, cỡ chữ và lề trang. "
                "Phần tổ chức nội dung vẫn được kiểm, phần trình bày thì không.")
    return ("Tệp dạng văn bản thuần (.txt/.md) không mang thông tin định dạng. "
            "Chỉ kiểm được cách tổ chức nội dung, không kiểm được cách trình bày.")


RULES_DIR = DEFAULT_RULES.parent


def rule_set_path(name: str) -> Path:
    """Tên bộ tiêu chí -> đường dẫn file, chặn mọi thứ trỏ ra ngoài thư mục rules."""
    clean = Path(name or "").name.removesuffix(".yaml").removesuffix(".yml")
    if not clean:
        return DEFAULT_RULES
    candidate = RULES_DIR / f"{clean}.yaml"
    if not candidate.is_file():
        raise FileNotFoundError(f"Không có bộ tiêu chí {clean!r} trong {RULES_DIR}")
    return candidate


def available_rule_sets() -> list[dict[str, str]]:
    """Danh mục bộ tiêu chí đang có - để giao diện cho chọn, không hard-code."""
    items: list[dict[str, str]] = []
    for path in sorted(RULES_DIR.glob("*.yaml")):
        try:
            meta = (load_rules(str(path)) or {}).get("meta", {})
        except Exception as exc:  # noqa: BLE001 - một file hỏng không làm mất cả danh mục
            logger.warning("Bỏ qua bộ tiêu chí hỏng %s: %s", path.name, exc)
            continue
        items.append({
            "id": path.stem,
            "label": str(meta.get("label") or meta.get("name") or path.stem),
            "version": str(meta.get("version", "")),
        })
    return items


@lru_cache
def load_rules(path: str | None = None) -> dict[str, Any]:
    rules_path = Path(path) if path else DEFAULT_RULES
    with rules_path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


# --------------------------------------------------------------------------- #
# Cách in giá trị của từng chiều trình bày, để câu thông báo đọc được như tiếng
# người. `size_pt` là số thực nên 13.0 phải in ra "13pt" chứ không phải "13.0pt".
# --------------------------------------------------------------------------- #
def _as_font(value: Any) -> str:
    return str(value)


def _as_pt(value: Any) -> str:
    return f"{float(value):g}pt"


def _as_ratio(value: Any) -> str:
    return f"{round(float(value), 2):g}"


# (khoá trong YAML, thuộc tính của Block, cách in, cách gom nhóm)
_DIMENSIONS: tuple[tuple[str, str, Callable[[Any], str], Callable[[Any, float], Any]], ...] = (
    ("font", "font", _as_font, lambda v, tol: normalize_font(str(v))),
    ("size_pt", "size_pt", _as_pt, lambda v, tol: round(float(v) / tol) if tol else float(v)),
    ("alignment", "alignment", _as_font, lambda v, tol: str(v).upper()),
    ("line_spacing", "line_spacing", _as_ratio, lambda v, tol: round(float(v) / tol) if tol else float(v)),
    ("paragraph_spacing_pt", "space_after_pt", _as_pt,
     lambda v, tol: round(float(v) / tol) if tol else float(v)),
)


class RuleEngine:
    def __init__(self, rules: dict[str, Any] | None = None, name: str = "") -> None:
        self.rules = rules if rules is not None else load_rules()
        self.name = name or DEFAULT_RULES.stem

    # ------------------------------------------------------------ vào ----- #
    def check(self, structure: DocumentStructure) -> RuleCheckResult:
        """Soát cấu trúc và cách trình bày của một tài liệu bất kỳ."""
        findings: list[RuleFinding] = []
        passed: list[str] = []
        skipped: list[str] = []

        cfg = self.rules.get("structure", {}) or {}
        outline = build_outline(
            structure,
            heading_max_chars=int(cfg.get("heading_max_chars", 100)),
            title_max_chars=int((cfg.get("title") or {}).get("max_chars", 150)),
        )
        if not outline.blocks:
            # Nói đúng lý do: tệp rỗng và tệp scan chưa qua OCR là hai chuyện khác nhau.
            return RuleCheckResult(
                status="skipped", rule_set=self.name,
                reason="Tài liệu dạng ảnh/scan nên chưa đọc được chữ nào để soát."
                if structure.source_format == "pdf_scan"
                else "Không đọc được nội dung nào trong tệp.")

        findings += self._check_title(outline, cfg.get("title"), passed)
        findings += self._check_heading_levels(outline, cfg.get("heading_levels"), passed, skipped)
        findings += self._check_empty_section(outline, cfg.get("empty_section"), passed, skipped)
        findings += self._check_duplicate_heading(
            outline, cfg.get("duplicate_heading"), passed, skipped)
        findings += self._check_numbering(outline, cfg.get("numbering"), passed, skipped)
        findings += self._check_paragraph_length(outline, cfg.get("paragraph_length"), passed)

        if not structure.has_format_info:
            # Chỉ kể những tiêu chí bộ này THỰC SỰ có: bộ tiêu chí bỏ hẳn phần
            # trình bày mà vẫn báo "chưa kiểm được phông chữ" là nói điều không có.
            return RuleCheckResult(
                status="partial", findings=findings, passed=passed,
                skipped=[*skipped, *(r for r in FORMAT_RULES if self._rule_of(r))],
                reason=_no_format_reason(structure.source_format),
                rule_set=self.name,
            )

        findings += self._check_consistency(outline, passed, skipped)
        page = self.rules.get("page", {}) or {}
        findings += self._check_page_size(structure, page.get("size_mm"), passed, skipped)
        findings += self._check_margin(structure, page.get("margin_mm"), passed, skipped)

        return RuleCheckResult(
            status="done" if not skipped else "partial",
            findings=findings, passed=passed, skipped=skipped, rule_set=self.name,
        )

    def _rule_of(self, rule_id: str) -> Any:
        """Cấu hình của một tiêu chí theo mã "phần.khoá"; rỗng nghĩa là bộ này không có."""
        section, _, key = rule_id.partition(".")
        return (self.rules.get(section) or {}).get(key)

    # -------------------------------------------------------- tiêu đề ----- #
    def _check_title(self, outline: Outline, rule, passed) -> list[RuleFinding]:
        if not rule:
            return []
        if outline.title is not None:
            passed.append("structure.title")
            return []
        first = outline.blocks[0]
        return [RuleFinding(
            rule="structure.title",
            severity=rule.get("severity", "warning"),
            message=rule.get("message", "Tài liệu không mở đầu bằng một tiêu đề"),
            block_id=first.id, quote=first.text[:80],
        )]

    # ---------------------------------------------------- thứ bậc mục ----- #
    def _check_heading_levels(self, outline: Outline, rule, passed, skipped) -> list[RuleFinding]:
        """Nhảy cấp: cấp 1 xuống thẳng cấp 3, giữa chừng không có cấp 2.

        Chỉ xét khi tài liệu khai cấp mục bằng style/Markdown. Mục đánh số tay
        được cấp phát cấp theo thứ tự xuất hiện nên không bao giờ nhảy - kiểm ở
        đó chỉ là kiểm chính mình.
        """
        if not rule:
            return []
        if not outline.explicit:
            skipped.append("structure.heading_levels (tài liệu không khai cấp mục bằng style)")
            return []

        findings: list[RuleFinding] = []
        previous = 0
        for heading in outline.headings:
            if previous and heading.level > previous + 1:
                findings.append(RuleFinding(
                    rule="structure.heading_levels",
                    severity=rule.get("severity", "warning"),
                    message=f"{rule.get('message', 'Nhảy cấp tiêu đề')}: "
                            f"từ cấp {previous} xuống thẳng cấp {heading.level}",
                    block_id=heading.block_id,
                    actual=f"cấp {heading.level}", expected=f"cấp {previous + 1}",
                    quote=heading.text[:80],
                ))
            previous = heading.level
        if not findings:
            passed.append("structure.heading_levels")
        return findings

    # ------------------------------------------------------- mục rỗng ----- #
    def _check_empty_section(self, outline: Outline, rule, passed, skipped) -> list[RuleFinding]:
        if not rule:
            return []
        if not outline.headings:
            skipped.append("structure.empty_section (không dò được mục nào)")
            return []

        findings: list[RuleFinding] = []
        headings = outline.headings
        for position, heading in enumerate(headings):
            nxt = headings[position + 1] if position + 1 < len(headings) else None
            end = nxt.index if nxt else len(outline.blocks)
            if outline.blocks[heading.index + 1:end]:
                continue
            # Mục cha đứng ngay trên mục con của nó thì không rỗng, đó là lồng nhau.
            if nxt is not None and nxt.level > heading.level:
                continue
            findings.append(RuleFinding(
                rule="structure.empty_section",
                severity=rule.get("severity", "warning"),
                message=f"{rule.get('message', 'Mục không có nội dung')}: "
                        f"{heading.text[:60]}",
                block_id=heading.block_id, quote=heading.text[:80],
            ))
        if not findings:
            passed.append("structure.empty_section")
        return findings

    # ----------------------------------------------------- trùng tên ------ #
    def _check_duplicate_heading(self, outline: Outline, rule, passed, skipped) -> list[RuleFinding]:
        if not rule:
            return []
        if len(outline.headings) < 2:
            skipped.append("structure.duplicate_heading (tài liệu có ít hơn hai mục)")
            return []

        findings: list[RuleFinding] = []
        seen: dict[str, str] = {}
        for heading in outline.headings:
            # Bỏ phần đánh số rồi mới so: "1. Tổng quan" và "2. Tổng quan" là trùng tên.
            body = heading.text[len(heading.marker):] if heading.marker else heading.text
            key = " ".join(body.split()).lower()
            if not key:
                continue
            if key in seen:
                findings.append(RuleFinding(
                    rule="structure.duplicate_heading",
                    severity=rule.get("severity", "info"),
                    message=f"{rule.get('message', 'Hai mục trùng tên')}: {body.strip()[:60]}",
                    block_id=heading.block_id, actual=heading.text[:60],
                    expected=f"khác với mục ở khối {seen[key]}", quote=heading.text[:80],
                ))
                continue
            seen[key] = heading.block_id
        if not findings:
            passed.append("structure.duplicate_heading")
        return findings

    # -------------------------------------------------------- đánh số ----- #
    @staticmethod
    def _lien_tuc(previous: Marker, current: Marker) -> bool:
        if current.ordinal == previous.ordinal + 1:
            return True
        # a) b) c) d) e): rất nhiều danh sách bỏ qua "đ" của bảng chữ cái tiếng Việt.
        return (current.kind == "letter" and current.ordinal == previous.ordinal + 2
                and LETTERS[previous.ordinal] == "đ")

    def _check_numbering(self, outline: Outline, rule, passed, skipped) -> list[RuleFinding]:
        """Mỗi kiểu đánh số là một dãy riêng: "1." không nối tiếp "a)".

        Gặp lại số 1 thì coi là bắt đầu một danh sách mới chứ không phải lùi số -
        một tài liệu có nhiều danh sách cùng kiểu là chuyện bình thường.
        """
        if not rule:
            return []
        if not outline.markers:
            skipped.append("structure.numbering (tài liệu không đánh số mục)")
            return []

        severity = rule.get("severity", "warning")
        message = rule.get("message", "Đánh số mục không liên tục")
        findings: list[RuleFinding] = []
        last: dict[str, Marker] = {}
        for marker in outline.markers:
            previous = last.get(marker.key)
            last[marker.key] = marker
            if previous is None or marker.ordinal == 1 or self._lien_tuc(previous, marker):
                continue
            lui = marker.ordinal <= previous.ordinal
            findings.append(RuleFinding(
                rule="structure.numbering",
                severity=severity,
                message=f"{message}: sau {previous.marker} lại đến {marker.marker}"
                        if lui else
                        f"{message}: {previous.marker} nhảy sang {marker.marker}",
                block_id=marker.block_id, actual=marker.marker,
                expected=f"số tiếp theo sau {previous.marker}",
                quote=marker.text[:80],
            ))
        if not findings:
            passed.append("structure.numbering")
        return findings

    # ----------------------------------------------------- đoạn quá dài --- #
    def _check_paragraph_length(self, outline: Outline, rule, passed) -> list[RuleFinding]:
        if not rule:
            return []
        max_chars = int(rule.get("max_chars", 1500))
        heading_ids = outline.heading_ids
        findings = [
            RuleFinding(
                rule="structure.paragraph_length",
                severity=rule.get("severity", "info"),
                message=rule.get("message", "Đoạn quá dài, nên tách thành nhiều đoạn"),
                block_id=block.id, actual=f"{len(block.text)} ký tự",
                expected=f"tối đa {max_chars} ký tự", quote=block.text[:80],
            )
            for block in outline.blocks
            if block.kind == "paragraph" and block.id not in heading_ids
            and len(block.text) > max_chars
        ]
        if not findings:
            passed.append("structure.paragraph_length")
        return findings

    # ------------------------------------------------- nhất quán --------- #
    @staticmethod
    def _body_blocks(outline: Outline) -> list[Block]:
        """Đoạn nội dung thật: bỏ tiêu đề và bảng.

        Tiêu đề in đậm cỡ lớn hơn phần thân là đúng chứ không phải lệch, còn bảng
        có cách trình bày riêng. Trộn cả ba vào một phép đo "nhất quán" thì tài
        liệu nào cũng bị báo. Điều kiện in đậm + ngắn là lưới chắn cho tiêu đề mà
        bước dựng dàn ý không nhận ra.
        """
        heading_ids = outline.heading_ids
        return [b for b in outline.blocks
                if b.kind == "paragraph" and b.id not in heading_ids
                and not (b.bold and len(b.text) <= 80)]

    def _check_consistency(self, outline: Outline, passed, skipped) -> list[RuleFinding]:
        cfg = self.rules.get("consistency", {}) or {}
        blocks = self._body_blocks(outline)
        findings: list[RuleFinding] = []
        for key, attr, render, group_of in _DIMENSIONS:
            findings += self._check_one_dimension(
                blocks, cfg, key, attr, render, group_of, passed, skipped)
        return findings

    def _check_one_dimension(self, blocks, cfg, key, attr, render, group_of,
                             passed, skipped) -> list[RuleFinding]:
        rule = cfg.get(key)
        rule_id = f"consistency.{key}"
        if not rule:
            return []

        # Canh lề, giãn dòng, cách đoạn của một dòng ngắn khác phần thân là CỐ Ý:
        # "Số: 42/BC-DV02" canh trái, tên cơ quan canh giữa, dòng ký canh phải. Đo
        # mấy chiều đó thì chỉ nhìn đoạn văn xuôi; phông và cỡ chữ thì không, một
        # dòng ngắn lạc phông vẫn là lạc phông.
        min_chars = int(rule.get("min_chars", cfg.get("min_chars", 40)))
        do_duoc = [b for b in blocks if len(b.text) >= min_chars]

        known = [(b, getattr(b, attr)) for b in do_duoc if getattr(b, attr) is not None]
        if len(known) < int(cfg.get("min_blocks", 4)):
            skipped.append(
                f"{rule_id} (chỉ {len(known)} đoạn đủ dữ liệu, chưa đủ để nói về nhất quán)")
            return []

        tolerance = float(rule.get("tolerance", rule.get("tolerance_pt", 0)) or 0)
        groups: dict[Any, list[tuple[Block, Any]]] = {}
        for block, value in known:
            groups.setdefault(group_of(value, tolerance), []).append((block, value))
        if len(groups) == 1:
            passed.append(rule_id)
            return []

        severity = rule.get("severity", "warning")
        message = rule.get("message", "Trình bày không nhất quán")
        dominant_key = max(groups, key=lambda k: len(groups[k]))
        dominant = groups[dominant_key]
        chuan = render(dominant[0][1])
        lech = [(b, v) for k, group in groups.items() if k != dominant_key for b, v in group]

        # Không nhóm nào đủ đông để làm chuẩn -> không chỉ được "chỗ lệch", chỉ nói
        # được là cả tài liệu không thống nhất.
        if len(dominant) / len(known) < float(cfg.get("min_dominance", 0.6)):
            return [RuleFinding(
                rule=rule_id, severity=severity,
                message=f"{message}: {len(groups)} kiểu khác nhau trong cùng tài liệu",
                actual=self._phan_bo(groups, render), expected="thống nhất một kiểu",
            )]

        # Lệch vài chỗ thì chỉ đúng từng chỗ; lệch quá nhiều thì gộp lại một dòng,
        # đổ ra một bức tường chữ chỉ làm người đọc bỏ qua cả phần này.
        if len(lech) > int(cfg.get("max_findings", 5)):
            return [RuleFinding(
                rule=rule_id, severity=severity,
                message=f"{message}: {len(lech)} đoạn khác với phần còn lại",
                actual=self._phan_bo(groups, render), expected=chuan,
                block_id=lech[0][0].id, quote=lech[0][0].text[:80],
            )]

        return [RuleFinding(
            rule=rule_id, severity=severity, message=message,
            block_id=block.id, actual=render(value), expected=chuan,
            quote=block.text[:80],
        ) for block, value in lech]

    @staticmethod
    def _phan_bo(groups: dict[Any, list[tuple[Block, Any]]], render) -> str:
        """"13pt: 12 đoạn; 11pt: 3 đoạn" - cho người đọc thấy ngay kiểu nào áp đảo."""
        counted = Counter({render(group[0][1]): len(group) for group in groups.values()})
        return "; ".join(f"{value}: {count} đoạn" for value, count in counted.most_common(4))

    # ------------------------------------------------------- khổ giấy ----- #
    def _check_page_size(self, structure, rule, passed, skipped) -> list[RuleFinding]:
        geometry = structure.geometry
        if not rule:
            return []
        if geometry is None:
            skipped.append("page.size_mm (không đọc được khổ giấy)")
            return []

        known: dict[str, list[float]] = rule.get("known") or {}
        tolerance = float(rule.get("tolerance_mm", 3))
        for name, (width, height) in known.items():
            doc = (geometry.width_mm, geometry.height_mm)
            # Khổ ngang cũng là khổ đó, chỉ xoay giấy đi.
            if any(abs(doc[0] - a) <= tolerance and abs(doc[1] - b) <= tolerance
                   for a, b in ((width, height), (height, width))):
                logger.debug("Khổ giấy nhận ra: %s", name)
                passed.append("page.size_mm")
                return []

        return [RuleFinding(
            rule="page.size_mm",
            severity=rule.get("severity", "info"),
            message=rule.get("message", "Khổ giấy không phải khổ chuẩn"),
            actual=f"{geometry.width_mm:.0f}×{geometry.height_mm:.0f}mm",
            expected=", ".join(known) or "khổ chuẩn",
        )]

    # ----------------------------------------------------------- lề ------- #
    def _check_margin(self, structure, rule, passed, skipped) -> list[RuleFinding]:
        geometry = structure.geometry
        if not rule:
            return []
        if geometry is None:
            skipped.append("page.margin_mm (không đọc được lề trang)")
            return []

        sides = {"top": geometry.top_mm, "bottom": geometry.bottom_mm,
                 "left": geometry.left_mm, "right": geometry.right_mm}
        # PDF chỉ suy được lề từ vùng chữ: lề dưới/phải của trang ngắn là vô nghĩa.
        if geometry.measured:
            reliable = set(rule.get("measured_only", ["top", "left"]))
            for side in set(sides) - reliable:
                skipped.append(f"page.margin_mm.{side} (chỉ ước lượng được từ vùng chữ)")
            sides = {k: v for k, v in sides.items() if k in reliable}

        vi = {"top": "trên", "bottom": "dưới", "left": "trái", "right": "phải"}
        minimum, maximum = float(rule.get("min", 0)), float(rule.get("max", 1000))
        # Word lưu lề theo inch rồi quy sang mm, nên lề "1.5cm" đọc về là 14.993mm.
        # Không có dung sai thì lề đặt đúng cũng bị báo sai.
        tolerance = float(rule.get("tolerance_mm", 0.0))
        findings = []
        for side, value in sides.items():
            if minimum - tolerance <= value <= maximum + tolerance:
                continue
            findings.append(RuleFinding(
                rule="page.margin_mm",
                severity=rule.get("severity", "warning"),
                message=f"{rule.get('message', 'Lề trang bất thường')} - lề {vi[side]}"
                        + (" (ước lượng từ vùng chữ)" if geometry.measured else ""),
                actual=f"{value:.1f}mm", expected=f"{minimum:g}-{maximum:g}mm",
            ))
        if not findings:
            passed.append("page.margin_mm")
        return findings


_engines: dict[str, RuleEngine] = {}


def get_rule_engine(rule_set: str = "") -> RuleEngine:
    """Engine theo tên bộ tiêu chí; rỗng = bộ mặc định.

    Giữ một engine cho mỗi bộ vì `load_rules` đã cache, dựng lại chỉ tốn công.
    """
    key = rule_set or DEFAULT_RULES.stem
    if key not in _engines:
        path = rule_set_path(rule_set)
        _engines[key] = RuleEngine(rules=load_rules(str(path)), name=path.stem)
    return _engines[key]

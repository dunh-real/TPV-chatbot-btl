"""Rule engine kiểm tra thể thức - hoàn toàn tất định, không dùng LLM.

Font, cỡ chữ, lề trang, các thành phần bắt buộc đều đo được từ file. Hỏi LLM
những thứ này là sai về nguyên tắc: nó chỉ nhận được text, không "nhìn" thấy
định dạng, nên câu trả lời sẽ trôi chảy và bịa.

Tiêu chí nằm trong `config/rules/*.yaml` để sửa được mà không đụng code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

from app.documents.parser import Block, DocumentStructure, normalize_font
from app.documents.structure import (
    SO_KY_HIEU_RE,
    DocumentComponents,
    body_blocks,
    format_block,
    parse_ngay_ban_hanh,
)

logger = logging.getLogger(__name__)

Severity = Literal["error", "warning", "info"]
DEFAULT_RULES = Path(__file__).resolve().parents[2] / "config" / "rules" / "nd30.yaml"


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

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")


def _no_format_reason(source_format: str) -> str:
    """Nói đúng lý do không kiểm tra được, đừng gộp mọi thứ thành "scan"."""
    if source_format == "pdf_scan":
        return ("Văn bản dạng ảnh/scan nên không đọc được phông chữ, cỡ chữ và lề trang. "
                "Các tiêu chí về trình bày chưa được kiểm tra.")
    return ("Tệp dạng văn bản thuần (.txt/.md) không mang thông tin định dạng. "
            "Chỉ kiểm tra được các thành phần thể thức, không kiểm tra được trình bày.")


@lru_cache
def load_rules(path: str | None = None) -> dict[str, Any]:
    rules_path = Path(path) if path else DEFAULT_RULES
    with rules_path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


class RuleEngine:
    def __init__(self, rules: dict[str, Any] | None = None) -> None:
        self.rules = rules if rules is not None else load_rules()

    def check(
        self, structure: DocumentStructure, components: DocumentComponents
    ) -> RuleCheckResult:
        findings: list[RuleFinding] = []
        passed: list[str] = []
        skipped: list[str] = []

        # Thành phần bắt buộc chỉ cần text -> kiểm tra được với mọi nguồn, kể cả scan.
        findings += self._check_components(components, passed)

        if not structure.has_format_info:
            return RuleCheckResult(
                status="partial",
                findings=findings,
                passed=passed,
                skipped=["format.font", "format.size_pt", "format.page_mm", "format.margin_mm"],
                reason=_no_format_reason(structure.source_format),
            )

        format_rules = self.rules.get("format", {})
        findings += self._check_font(structure, format_rules.get("font"), passed, skipped)
        findings += self._check_size(structure, components, format_rules.get("size_pt"), passed, skipped)
        findings += self._check_page(structure, format_rules.get("page_mm"), passed, skipped)
        findings += self._check_margin(structure, format_rules.get("margin_mm"), passed, skipped)
        body = body_blocks(structure, components)
        findings += self._check_alignment(body, format_rules.get("alignment"), passed, skipped)
        findings += self._check_line_spacing(body, format_rules.get("line_spacing"), passed, skipped)
        findings += self._check_paragraph_spacing(
            body, format_rules.get("paragraph_spacing_pt"), passed, skipped)
        findings += self._check_component_format(
            structure, components, self.rules.get("component_format"), passed, skipped)

        return RuleCheckResult(
            status="done" if not skipped else "partial",
            findings=findings, passed=passed, skipped=skipped,
        )

    # ------------------------------------------------------------- font --- #
    def _check_font(self, structure, rule, passed, skipped) -> list[RuleFinding]:
        if not rule:
            return []
        allowed = {normalize_font(f) for f in rule.get("allowed", [])}
        if not allowed:
            return []

        findings: list[RuleFinding] = []
        unknown = 0
        for block in structure.blocks:
            if block.is_empty:
                continue
            if block.font is None:
                unknown += 1        # thừa kế style mà style không khai báo
                continue
            if normalize_font(block.font) not in allowed:
                findings.append(RuleFinding(
                    rule="format.font",
                    severity=rule.get("severity", "error"),
                    message=rule.get("message", "Phông chữ không đúng quy định"),
                    block_id=block.id, actual=block.font,
                    expected=" hoặc ".join(rule.get("allowed", [])),
                    quote=block.text[:80],
                ))

        if unknown:
            skipped.append(f"format.font ({unknown} khối không khai báo phông)")
        if not findings:
            passed.append("format.font")
        return findings

    # -------------------------------------------------------------- cỡ ---- #
    def _check_size(self, structure, components, rule, passed, skipped) -> list[RuleFinding]:
        if not rule:
            return []
        minimum, maximum = rule.get("min"), rule.get("max")
        # Tiêu đề, quốc hiệu, chữ ký được phép cỡ khác phần nội dung.
        skip_blocks = {
            component.block_id
            for key, component in components.found.items()
            if key in set(rule.get("skip_components", []))
        }

        findings: list[RuleFinding] = []
        unknown = 0
        for block in structure.blocks:
            if block.is_empty or block.id in skip_blocks:
                continue
            if block.size_pt is None:
                unknown += 1
                continue
            if (minimum is not None and block.size_pt < minimum) or (
                maximum is not None and block.size_pt > maximum
            ):
                findings.append(RuleFinding(
                    rule="format.size_pt",
                    severity=rule.get("severity", "error"),
                    message=rule.get("message", "Cỡ chữ không đúng quy định"),
                    block_id=block.id, actual=f"{block.size_pt:g}pt",
                    expected=f"{minimum}-{maximum}pt", quote=block.text[:80],
                ))

        if unknown:
            skipped.append(f"format.size_pt ({unknown} khối không khai báo cỡ chữ)")
        if not findings:
            passed.append("format.size_pt")
        return findings

    # ------------------------------------------------------- khổ giấy ----- #
    def _check_page(self, structure, rule, passed, skipped) -> list[RuleFinding]:
        geometry = structure.geometry
        if not rule or geometry is None:
            if rule:
                skipped.append("format.page_mm (không đọc được khổ giấy)")
            return []

        findings = []
        for axis, value in (("width", geometry.width_mm), ("height", geometry.height_mm)):
            bounds = rule.get(axis)
            if not bounds:
                continue
            if not bounds[0] <= value <= bounds[1]:
                findings.append(RuleFinding(
                    rule=f"format.page_mm.{axis}",
                    severity=rule.get("severity", "warning"),
                    message=rule.get("message", "Khổ giấy không đúng quy định"),
                    actual=f"{value:.1f}mm", expected=f"{bounds[0]}-{bounds[1]}mm",
                ))
        if not findings:
            passed.append("format.page_mm")
        return findings

    # ----------------------------------------------------------- lề ------- #
    def _check_margin(self, structure, rule, passed, skipped) -> list[RuleFinding]:
        geometry = structure.geometry
        if not rule or geometry is None:
            if rule:
                skipped.append("format.margin_mm (không đọc được lề trang)")
            return []

        sides = {"top": geometry.top_mm, "bottom": geometry.bottom_mm,
                 "left": geometry.left_mm, "right": geometry.right_mm}
        # PDF chỉ suy được lề từ vùng chữ: lề dưới/phải của trang ngắn là vô nghĩa.
        if geometry.measured:
            reliable = set(rule.get("measured_only", ["top", "left"]))
            for side in set(sides) - reliable:
                skipped.append(f"format.margin_mm.{side} (chỉ ước lượng được từ vùng chữ)")
            sides = {k: v for k, v in sides.items() if k in reliable}

        vi = {"top": "trên", "bottom": "dưới", "left": "trái", "right": "phải"}
        tolerance = float(rule.get("tolerance_mm", 0.0))
        findings = []
        for side, value in sides.items():
            bounds = rule.get(side)
            if not bounds:
                continue
            if not bounds[0] - tolerance <= value <= bounds[1] + tolerance:
                findings.append(RuleFinding(
                    rule=f"format.margin_mm.{side}",
                    severity=rule.get("severity", "warning"),
                    message=f"Lề {vi[side]} chưa đúng quy định"
                            + (" (ước lượng từ vùng chữ)" if geometry.measured else ""),
                    actual=f"{value:.1f}mm", expected=f"{bounds[0]}-{bounds[1]}mm",
                ))
        if not findings:
            passed.append("format.margin_mm")
        return findings

    # ----------------------------------------- căn lề / giãn dòng --------- #
    @staticmethod
    def _content_blocks(body: list[Block]) -> list[Block]:
        """Lọc ra đoạn văn nội dung thật, bỏ bảng và tiêu đề mục.

        Tiêu đề mục ("I. TÌNH HÌNH...") canh trái và giãn cách rộng hơn là đúng,
        không phải lỗi - nhận diện bằng: in đậm và ngắn. Không có style Heading để
        dựa vào vì văn bản hành chính thường đánh mục bằng tay trên style Normal.
        """
        return [b for b in body
                if b.kind == "paragraph" and not (b.bold and len(b.text) <= 80)]

    def _check_alignment(self, body, rule, passed, skipped) -> list[RuleFinding]:
        if not rule:
            return []
        expected = str(rule.get("body", "JUSTIFY")).upper()
        findings: list[RuleFinding] = []
        unknown = 0
        for block in self._content_blocks(body):
            if block.alignment is None:
                unknown += 1
                continue
            if block.alignment.upper() != expected:
                findings.append(RuleFinding(
                    rule="format.alignment",
                    severity=rule.get("severity", "warning"),
                    message=rule.get("message", "Canh lề đoạn chưa đúng quy định"),
                    block_id=block.id, actual=block.alignment,
                    expected=expected, quote=block.text[:80],
                ))
        if unknown:
            skipped.append(f"format.alignment ({unknown} đoạn kế thừa canh lề từ style)")
        if not findings:
            passed.append("format.alignment")
        return findings

    def _check_line_spacing(self, body, rule, passed, skipped) -> list[RuleFinding]:
        if not rule:
            return []
        single, minimum = rule.get("single", 1.0), rule.get("min", 1.15)
        findings: list[RuleFinding] = []
        unknown = 0
        for block in self._content_blocks(body):
            if block.line_spacing is None:
                unknown += 1
                continue
            # Dòng đơn hợp lệ, từ 1.15 trở lên hợp lệ; ở giữa hai mốc thì không.
            if abs(block.line_spacing - single) < 1e-6 or block.line_spacing >= minimum:
                continue
            findings.append(RuleFinding(
                rule="format.line_spacing",
                severity=rule.get("severity", "warning"),
                message=rule.get("message", "Giãn dòng chưa đúng quy định"),
                # docx lưu giãn dòng theo EMU nên 1.08 đọc về thành 1.07917;
                # làm tròn để câu thông báo không rối mắt người đọc.
                block_id=block.id, actual=f"{round(block.line_spacing, 2):g}",
                expected=f"{single:g} hoặc >= {minimum:g}", quote=block.text[:80],
            ))
        if unknown:
            skipped.append(f"format.line_spacing ({unknown} đoạn không khai báo giãn dòng)")
        if not findings:
            passed.append("format.line_spacing")
        return findings

    def _check_paragraph_spacing(self, body, rule, passed, skipped) -> list[RuleFinding]:
        if not rule:
            return []
        minimum, maximum = rule.get("min"), rule.get("max")
        findings: list[RuleFinding] = []
        unknown = 0
        for block in self._content_blocks(body):
            # Cách đoạn là tổng khoảng hở giữa hai đoạn liền nhau; ở đây xét riêng
            # từng phía vì đoạn kế tiếp có thể có quy tắc khác.
            for phia, value in (("trước", block.space_before_pt),
                                ("sau", block.space_after_pt)):
                if value is None:
                    unknown += 1
                    continue
                if (minimum is not None and value < minimum) or (
                    maximum is not None and value > maximum
                ):
                    findings.append(RuleFinding(
                        rule="format.paragraph_spacing_pt",
                        severity=rule.get("severity", "warning"),
                        message=f"{rule.get('message', 'Khoảng cách đoạn chưa đúng')} "
                                f"(cách đoạn {phia})",
                        block_id=block.id, actual=f"{value:g}pt",
                        expected=f"{minimum}-{maximum}pt", quote=block.text[:80],
                    ))
        if unknown:
            skipped.append(f"format.paragraph_spacing_pt ({unknown} chỗ kế thừa từ style)")
        if not findings:
            passed.append("format.paragraph_spacing_pt")
        return findings

    # ------------------------- định dạng quốc hiệu / tiêu ngữ ------------- #
    def _check_component_format(self, structure, components, rules, passed,
                               skipped) -> list[RuleFinding]:
        """Không chỉ hỏi "có quốc hiệu không" mà "quốc hiệu trình bày đã đúng chưa"."""
        if not rules:
            return []
        findings: list[RuleFinding] = []
        for component_id, rule in rules.items():
            component = components.get(component_id)
            if component is None:      # thiếu hẳn -> đã báo ở required_components
                continue
            label = rule.get("label", component_id)
            block = format_block(structure, component)
            if block is None:
                skipped.append(f"component_format.{component_id} "
                               f"(không lần được định dạng thật trong bảng)")
                continue
            severity = rule.get("severity", "warning")
            sai: list[tuple[str, str, str]] = []   # (mô tả, thực tế, mong đợi)

            if (hoa := rule.get("uppercase")) is not None:
                chu = [c for c in block.text if c.isalpha()]
                is_upper = bool(chu) and all(c.isupper() for c in chu)
                if is_upper is not bool(hoa):
                    sai.append(("kiểu chữ", "in hoa" if is_upper else "in thường",
                                "in hoa" if hoa else "in thường"))
            if rule.get("bold") is not None and block.bold is not bool(rule["bold"]):
                sai.append(("độ đậm", "đậm" if block.bold else "không đậm",
                            "đậm" if rule["bold"] else "không đậm"))
            if (bounds := rule.get("size_pt")) and block.size_pt is not None:
                if not bounds[0] <= block.size_pt <= bounds[1]:
                    sai.append(("cỡ chữ", f"{block.size_pt:g}pt",
                                f"{bounds[0]}-{bounds[1]}pt"))
            if (can := rule.get("alignment")) and block.alignment is not None:
                if block.alignment.upper() != str(can).upper():
                    sai.append(("canh lề", block.alignment, str(can)))

            if not sai:
                passed.append(f"component_format.{component_id}")
                continue
            for mo_ta, thuc_te, mong_doi in sai:
                findings.append(RuleFinding(
                    rule=f"component_format.{component_id}.{mo_ta.replace(' ', '_')}",
                    severity=severity,
                    message=f"{label} sai {mo_ta}",
                    block_id=block.id, actual=thuc_te, expected=mong_doi,
                    quote=block.text[:80],
                ))
        return findings

    # ------------------------------------------- thành phần bắt buộc ------ #
    def _check_components(self, components: DocumentComponents, passed) -> list[RuleFinding]:
        findings: list[RuleFinding] = []
        for spec in self.rules.get("required_components", []):
            component_id = spec["id"]
            label = spec.get("label", component_id)
            severity = spec.get("severity", "warning")

            component = components.get(component_id)
            if component is None:
                findings.append(RuleFinding(
                    rule=f"required.{component_id}", severity=severity,
                    message=f"Thiếu thành phần: {label}",
                ))
                continue

            invalid = self._validate_component(spec.get("validate"), component.value)
            if invalid is not None:
                findings.append(RuleFinding(
                    rule=f"required.{component_id}.format", severity=severity,
                    message=f"{label} chưa đúng dạng: {invalid}",
                    block_id=component.block_id, actual=component.value,
                    quote=component.text[:80],
                ))
            else:
                passed.append(f"required.{component_id}")
        return findings

    @staticmethod
    def _validate_component(validator: str | None, value: str) -> str | None:
        """Trả về mô tả lỗi, hoặc None nếu hợp lệ."""
        if not validator:
            return None
        if validator == "so_ky_hieu":
            if not value or not SO_KY_HIEU_RE.match(value):
                return 'phải theo dạng "105/CV-BGĐ"'
        elif validator == "ngay_ban_hanh":
            parsed = parse_ngay_ban_hanh(value)
            if parsed is None:
                return 'phải theo dạng "ngày 05 tháng 9 năm 2026"'
            day, month, _ = parsed
            if not (1 <= day <= 31 and 1 <= month <= 12):
                return f"ngày tháng không hợp lệ ({day}/{month})"
        return None


_engine: RuleEngine | None = None


def get_rule_engine() -> RuleEngine:
    global _engine
    if _engine is None:
        _engine = RuleEngine()
    return _engine

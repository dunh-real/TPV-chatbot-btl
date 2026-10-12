"""Vẽ biểu đồ cho báo cáo và bộ slide - hoàn toàn bằng code, một chuẩn trình bày chung.

Mọi giá trị trên biểu đồ lấy thẳng từ kết quả truy vấn, không qua LLM. Biểu đồ là
nơi sai sót khó phát hiện nhất: người đọc nhìn cột cao thấp chứ không đọc số, nên
tuyệt đối không để model tham gia vào đây.

Một chuẩn cho mọi đường xuất: workflow 4 (báo cáo Word), workflow 5 dựng qua
Presenton và workflow 5 tự dựng khi Presenton hỏng đều gọi vào đây, nên cùng một
bộ số cho ra cùng một hình - cùng bảng màu, cùng font, cùng cách ghi số kiểu Việt
Nam (1.234 và 31,3). Trước đây Presenton tự vẽ biểu đồ bằng renderer của nó: xuất
.pptx là ảnh chụp, màu theo theme, nhãn trục bị ẩn bớt khi nhiều danh mục, và khác
hẳn hình của đường tự dựng.

seaborn lo phần chuẩn trình bày (lưới, cỡ chữ, khoảng cách); cột vẽ bằng matplotlib
theo vị trí: `sns.barplot` gộp (lấy trung bình) các danh mục trùng tên - hai phòng
ban cùng tên sẽ thành một cột sai số mà không báo gì.
"""

from __future__ import annotations

import io
import logging
import textwrap
from contextlib import contextmanager
from typing import Iterator, Sequence

import matplotlib

matplotlib.use("Agg")           # không cần màn hình
import matplotlib.pyplot as plt  # noqa: E402
import seaborn as sns  # noqa: E402
from matplotlib.ticker import FuncFormatter, MaxNLocator  # noqa: E402

from app.documents.slide_deck import HORIZONTAL_FROM, PALETTE as SERIES_COLORS, Dataset, so  # noqa: E402

logger = logging.getLogger(__name__)

# Màu theo nghĩa cho biểu đồ WF4; chuỗi số liệu dùng SERIES_COLORS - cùng bảng màu đặt cho theme Presenton.
PALETTE = {"current": SERIES_COLORS[0], "previous": "#94A3B8",
           "good": "#16A34A", "warn": "#F59E0B", "bad": "#DC2626"}
TEXT_COLOR = "#1F2937"
MUTED_COLOR = "#64748B"
GRID_COLOR = "#E2E8F0"

DPI = 200
# DejaVu Sans đi kèm matplotlib nên máy nào cũng có, và đủ glyph tiếng Việt. Font hệ thống
# (Arial...) thì máy chủ Linux có thể thiếu: cùng code mà mỗi máy ra một hình.
_RC = {
    "font.family": "DejaVu Sans",
    "text.color": TEXT_COLOR,
    "axes.labelcolor": TEXT_COLOR,
    "axes.edgecolor": GRID_COLOR,
    "grid.color": GRID_COLOR,
    "xtick.color": TEXT_COLOR,
    "ytick.color": TEXT_COLOR,
    "axes.titleweight": "bold",
    "axes.titlesize": 11,
    "axes.labelsize": 9,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "legend.fontsize": 9,
    "font.size": 9,
}


@contextmanager
def _style() -> Iterator[None]:
    """Chuẩn trình bày áp trong phạm vi một hình - không đổi rcParams toàn cục của tiến trình."""
    with sns.axes_style("whitegrid", rc=_RC), sns.plotting_context("paper", rc=_RC):
        yield


def _vn(value: float) -> str:
    """Số trên nhãn cột và trục: 1.234, 31,3 - giống hệt số trong bảng và chữ trên slide."""
    return so(float(value))


def _wrap(label: str, width: int) -> str:
    return "\n".join(textwrap.wrap(str(label), width=width, break_long_words=False)) or str(label)


def _finish(fig, *, transparent: bool = False) -> bytes:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=DPI, transparent=transparent)
    plt.close(fig)
    return buffer.getvalue()


def _label_bars(axes, bars, values: Sequence[float], *, skip_zero: bool = False) -> None:
    """Ghi số ngay trên/cạnh cột: người đọc không phải ước lượng theo chiều cao."""
    labels = ["" if skip_zero and not v else _vn(v) for v in values]
    axes.bar_label(bars, labels=labels, padding=3, fontsize=8.5, color=TEXT_COLOR)


def _headroom(axes, *, horizontal: bool) -> None:
    """Chừa chỗ cho nhãn ở cột dài nhất. Gọi MỘT lần sau khi vẽ xong mọi chuỗi: đặt giới hạn trục
    là tắt tự co giãn, gọi giữa chừng thì cột của chuỗi vẽ sau bị cắt ở mép."""
    axes.relim()
    axes.autoscale_view()
    if horizontal:
        lo, hi = axes.get_xlim()
        axes.set_xlim(lo, hi + (hi - lo) * 0.08)
    else:
        lo, hi = axes.get_ylim()
        axes.set_ylim(lo, hi + (hi - lo) * 0.08)


# --------------------------------------------------------------------------- #
# Biểu đồ của bộ dữ liệu: dùng chung cho slide Presenton và slide tự dựng
# --------------------------------------------------------------------------- #
def dataset_chart(
    categories: Sequence[str],
    series: Sequence[tuple[str, Sequence[float]]],
    *,
    unit: str = "",
    size_px: tuple[float, float] = (1120.0, 560.0),
    horizontal: bool | None = None,
    title: str = "",
    transparent: bool = False,
) -> bytes:
    """Cột (nhóm) cho một bộ dữ liệu. Từ HORIZONTAL_FROM danh mục trở lên vẽ cột ngang để
    nhãn dài (tên phòng ban) không bị xoay hoặc ẩn bớt.

    `size_px` là kích thước ô đặt ảnh trên slide (đơn vị px của slide 1280x720): ảnh vẽ đúng
    tỷ lệ ô nên không bị méo hay thừa viền, và chữ giữ cùng cỡ ở mọi ô.
    """
    categories = [str(c) for c in categories]
    series = [(str(name), [float(v or 0) for v in values]) for name, values in series]
    if not categories or not series:
        raise ValueError("Biểu đồ cần ít nhất một danh mục và một chuỗi số liệu")
    for name, values in series:
        if len(values) != len(categories):
            raise ValueError(f"Chuỗi {name!r} có {len(values)} giá trị cho {len(categories)} danh mục")
    if horizontal is None:
        horizontal = len(categories) >= HORIZONTAL_FROM

    width_in, height_in = size_px[0] / 100, size_px[1] / 100
    n = len(series)
    group = 0.8
    bar = group / n
    # Ô thấp mà nhiều danh mục (9 phòng ban trong ô 334px): nhãn hai dòng chồng lên nhãn kế bên.
    # Chữ nhỏ lại và ngắt dòng rộng hơn để mỗi nhãn nằm trên một dòng khi có thể.
    row_px = size_px[1] * 0.8 / len(categories) if horizontal else 99.0
    label_size = 9.0 if row_px >= 34 else 8.0 if row_px >= 24 else 7.0
    wrap = 24 if row_px >= 34 else 34
    counts = all(float(v).is_integer() for _, values in series for v in values)
    with _style():
        fig, axes = plt.subplots(figsize=(width_in, height_in))
        positions = list(range(len(categories)))
        for k, (name, values) in enumerate(series):
            offset = (k - (n - 1) / 2) * bar
            where = [p + offset for p in positions]
            color = SERIES_COLORS[k % len(SERIES_COLORS)]
            if horizontal:
                bars = axes.barh(where, values, height=bar * 0.92, color=color, label=name)
            else:
                bars = axes.bar(where, values, width=bar * 0.92, color=color, label=name)
            # Nhiều chuỗi: hàng loạt nhãn "0" chen nhau che mất số thật; cột rỗng tự nói là 0.
            _label_bars(axes, bars, values, skip_zero=n > 1)
        _headroom(axes, horizontal=horizontal)

        value_axis = axes.xaxis if horizontal else axes.yaxis
        value_axis.set_major_formatter(FuncFormatter(lambda v, _: _vn(v)))
        if counts:      # số đếm (người, thiết bị): không có vạch 0,5 người
            value_axis.set_major_locator(MaxNLocator(integer=True))
        if horizontal:
            axes.set_yticks(positions, [_wrap(c, wrap) for c in categories], fontsize=label_size)
            axes.invert_yaxis()                      # mục lớn nhất (đầu danh sách) ở trên cùng
            axes.grid(axis="y", visible=False)
            if unit:
                axes.set_xlabel(unit, color=MUTED_COLOR)
        else:
            axes.set_xticks(positions, [_wrap(c, 14) for c in categories])
            axes.grid(axis="x", visible=False)
            if unit:
                axes.set_ylabel(unit, color=MUTED_COLOR)
        axes.set_axisbelow(True)
        sns.despine(ax=axes, left=horizontal, bottom=not horizontal)
        if title:
            axes.set_title(title, loc="left", pad=10)
        if n > 1:
            axes.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=min(n, 4), frameon=False,
                        handlelength=1.2, borderaxespad=0.3)
        fig.tight_layout()
        return _finish(fig, transparent=transparent)


def chart_for(ds: Dataset, size_px: tuple[float, float] = (1090.0, 460.0), *,
              transparent: bool = False) -> bytes:
    """Biểu đồ của một bộ dữ liệu slide - điểm gọi chung của slide Presenton và slide tự dựng."""
    return dataset_chart(ds.categories, ds.series, unit=ds.unit, size_px=size_px,
                         horizontal=ds.chart_type == "horizontal_bar", transparent=transparent)


# --------------------------------------------------------------------------- #
# Biểu đồ chỉ tiêu của workflow 4
# --------------------------------------------------------------------------- #
def compare_bar_chart(
    title: str,
    labels: Sequence[str],
    current: Sequence[float],
    previous: Sequence[float] | None = None,
    current_label: str = "Kỳ này",
    previous_label: str = "Kỳ trước",
) -> bytes:
    """Cột so sánh hai kỳ, có ghi số ngay trên đầu cột."""
    with _style():
        fig, axes = plt.subplots(figsize=(7.2, 3.6))
        positions = list(range(len(labels)))
        width = 0.38 if previous else 0.55

        if previous:
            old = axes.bar([p - width / 2 for p in positions], previous, width,
                           label=previous_label, color=PALETTE["previous"])
            _label_bars(axes, old, previous)
            bars = axes.bar([p + width / 2 for p in positions], current, width,
                            label=current_label, color=PALETTE["current"])
            axes.legend(frameon=False)
        else:
            bars = axes.bar(positions, current, width, color=PALETTE["current"])
        _label_bars(axes, bars, current)
        _headroom(axes, horizontal=False)

        axes.set_xticks(positions, [_wrap(label, 14) for label in labels])
        axes.yaxis.set_major_formatter(FuncFormatter(lambda v, _: _vn(v)))
        axes.grid(axis="x", visible=False)
        axes.set_title(title, pad=10)
        sns.despine(ax=axes)
        fig.tight_layout()
        return _finish(fig)


def status_bar_chart(title: str, labels: Sequence[str], values: Sequence[float]) -> bytes:
    """Cột ngang cho tình trạng thiết bị: tốt (xanh) -> cần xử lý (đỏ)."""
    colors = [PALETTE["good"] if "tốt" in str(label).lower() else
              PALETTE["bad"] if "hỏng" in str(label).lower() else PALETTE["warn"]
              for label in labels]
    with _style():
        fig, axes = plt.subplots(figsize=(7.2, max(2.2, 0.5 * len(labels) + 1.2)))
        positions = list(range(len(labels)))
        bars = axes.barh(positions, list(values), color=colors, height=0.55)
        _label_bars(axes, bars, values)
        _headroom(axes, horizontal=True)

        axes.set_yticks(positions, [_wrap(label, 24) for label in labels])
        axes.invert_yaxis()
        axes.xaxis.set_major_formatter(FuncFormatter(lambda v, _: _vn(v)))
        axes.grid(axis="y", visible=False)
        axes.set_title(title, pad=10)
        sns.despine(ax=axes, left=True)
        fig.tight_layout()
        return _finish(fig)


def trend_line_chart(title: str, periods: Sequence[str], values: Sequence[float]) -> bytes:
    """Đường xu hướng qua nhiều kỳ."""
    with _style():
        fig, axes = plt.subplots(figsize=(7.2, 3.2))
        axes.plot(list(periods), list(values), marker="o", color=PALETTE["current"], linewidth=2)
        for period, value in zip(periods, values, strict=True):
            axes.annotate(_vn(value), (period, value), ha="center", va="bottom",
                          fontsize=8.5, xytext=(0, 6), textcoords="offset points")

        axes.yaxis.set_major_formatter(FuncFormatter(lambda v, _: _vn(v)))
        axes.set_title(title, pad=10)
        sns.despine(ax=axes)
        fig.tight_layout()
        return _finish(fig)

"""Vẽ biểu đồ cho báo cáo tổng hợp - hoàn toàn bằng code.

Mọi giá trị trên biểu đồ lấy thẳng từ kết quả của `app.tools.data`, không qua LLM.
Biểu đồ là nơi sai sót khó phát hiện nhất: người đọc nhìn cột cao thấp chứ không
đọc số, nên tuyệt đối không để model tham gia vào đây.
"""

from __future__ import annotations

import io
import logging
from typing import Sequence

import matplotlib

matplotlib.use("Agg")           # không cần màn hình
import matplotlib.pyplot as plt  # noqa: E402

logger = logging.getLogger(__name__)

# DejaVu Sans là font mặc định của matplotlib và có đủ glyph tiếng Việt.
plt.rcParams["font.family"] = "DejaVu Sans"
plt.rcParams["axes.grid"] = True
plt.rcParams["grid.alpha"] = 0.3

PALETTE = {"current": "#2563eb", "previous": "#94a3b8",
           "good": "#16a34a", "warn": "#f59e0b", "bad": "#dc2626"}


def _finish(fig) -> bytes:
    buffer = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buffer, format="png", dpi=150)
    plt.close(fig)
    return buffer.getvalue()


def compare_bar_chart(
    title: str,
    labels: Sequence[str],
    current: Sequence[float],
    previous: Sequence[float] | None = None,
    current_label: str = "Kỳ này",
    previous_label: str = "Kỳ trước",
) -> bytes:
    """Cột so sánh hai kỳ, có ghi số ngay trên đầu cột."""
    fig, axes = plt.subplots(figsize=(7.2, 3.6))
    positions = range(len(labels))
    width = 0.38 if previous else 0.55

    if previous:
        axes.bar([p - width / 2 for p in positions], previous, width,
                 label=previous_label, color=PALETTE["previous"])
        bars = axes.bar([p + width / 2 for p in positions], current, width,
                        label=current_label, color=PALETTE["current"])
        axes.legend(frameon=False)
    else:
        bars = axes.bar(list(positions), current, width, color=PALETTE["current"])

    # Ghi số lên cột: người đọc không phải ước lượng theo chiều cao.
    for bar in bars:
        axes.annotate(f"{bar.get_height():g}",
                      (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                      ha="center", va="bottom", fontsize=9)

    axes.set_xticks(list(positions))
    axes.set_xticklabels(labels, fontsize=9)
    axes.set_title(title, fontsize=11, pad=10)
    axes.spines[["top", "right"]].set_visible(False)
    return _finish(fig)


def status_bar_chart(title: str, labels: Sequence[str], values: Sequence[float]) -> bytes:
    """Cột ngang cho tình trạng trang bị: tốt (xanh) -> cần xử lý (đỏ)."""
    fig, axes = plt.subplots(figsize=(7.2, max(2.2, 0.5 * len(labels) + 1.2)))
    colors = [PALETTE["good"] if "tốt" in str(label).lower() else
              PALETTE["bad"] if "hỏng" in str(label).lower() else PALETTE["warn"]
              for label in labels]

    bars = axes.barh(list(labels), list(values), color=colors, height=0.55)
    for bar in bars:
        axes.annotate(f"{bar.get_width():g}",
                      (bar.get_width(), bar.get_y() + bar.get_height() / 2),
                      ha="left", va="center", fontsize=9, xytext=(4, 0),
                      textcoords="offset points")

    axes.invert_yaxis()
    axes.set_title(title, fontsize=11, pad=10)
    axes.spines[["top", "right"]].set_visible(False)
    axes.grid(axis="y", visible=False)
    return _finish(fig)


def trend_line_chart(title: str, periods: Sequence[str], values: Sequence[float]) -> bytes:
    """Đường xu hướng qua nhiều kỳ."""
    fig, axes = plt.subplots(figsize=(7.2, 3.2))
    axes.plot(list(periods), list(values), marker="o", color=PALETTE["current"], linewidth=2)
    for period, value in zip(periods, values, strict=True):
        axes.annotate(f"{value:g}", (period, value), ha="center", va="bottom",
                      fontsize=9, xytext=(0, 6), textcoords="offset points")

    axes.set_title(title, fontsize=11, pad=10)
    axes.spines[["top", "right"]].set_visible(False)
    return _finish(fig)

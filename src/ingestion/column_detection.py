"""Page column layout detection for V2 canonical assembly.

Consumes page-points bboxes (the ``bbox`` field of V1 ``elements`` / layout
boxes, top-left origin, 0..page_width/page_height) and decides whether a page
is single or two-column, then produces a deterministic within-page reading
order that downstream Assembly uses to sequence ``elements[]``.

Pure functions, standard library only, no IO.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import statistics
from typing import Sequence


__all__ = [
    "Column",
    "ColumnLayout",
    "detect_columns",
]


# ---------------------------------------------------------------------------
# Tuning thresholds (page-fraction based so they are resolution independent)
# ---------------------------------------------------------------------------

# A bbox wider than this fraction of page width is treated as a full-width
# block (page header / footer / wide table) and excluded from column
# classification. 60% matches the brief and comfortably separates a single
# column of body text (typically <= 80% of page width but centered, see
# below) from genuinely full-width furniture.
FULL_WIDTH_RATIO = 0.60

# Two columns are declared only when a vertical gutter inside the central
# band [0.30, 0.70] * page_width is at least this wide (as a fraction of
# page_width) AND both sides carry enough bboxes. 8% of page width is a
# conservative gutter: CECS758 body text is centered with centers clustering
# near 0.5*W and no internal gap approaching this size, while a real
# two-column page leaves a gutter of ~5-15% of page width.
GUTTER_MIN_RATIO = 0.08

# Central band within which a gutter must fall to count as a column seam.
CENTER_LO = 0.30
CENTER_HI = 0.70

# Minimum number of non-full-width bboxes required on each side to call a
# two-column layout. Below this the page is single-column (a stray margin
# note must not flip the whole page).
MIN_PER_COLUMN = 2

# Below this many non-full-width bboxes we do not even attempt two-column
# detection (not enough evidence).
MIN_FOR_TWO_COLUMN = 4


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Column:
    """A single detected column.

    ``bbox_indices`` are indices into the input ``bboxes`` list, sorted by
    ascending ``y0`` (top edge) so column-internal reading order is top-down.
    """

    index: int
    x_range: tuple[float, float]
    bbox_indices: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class ColumnLayout:
    """Result of :func:`detect_columns`.

    ``reading_order`` is the final within-page reading sequence of input
    bbox indices: single-column -> all by ascending y; two-column ->
    full-width furniture above the columns, then left column top-down, then
    right column top-down, then full-width furniture below the columns.
    """

    column_count: int
    columns: list[Column]
    reading_order: list[int]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_finite_box(box: Sequence[float]) -> bool:
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return False
    for v in box:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return False
    x0, y0, x1, y1 = box
    return 0 <= x0 < x1 and 0 <= y0 < y1


def _center_x(box: Sequence[float]) -> float:
    return (box[0] + box[2]) / 2.0


def _width(box: Sequence[float]) -> float:
    return box[2] - box[0]


def _split_full_width(
    bboxes: list[list[float]], page_width: float
) -> tuple[list[int], list[int]]:
    """Partition bbox indices into (full_width, body) lists."""
    full_width: list[int] = []
    body: list[int] = []
    threshold = FULL_WIDTH_RATIO * page_width
    for i, box in enumerate(bboxes):
        if not _is_finite_box(box):
            continue
        if _width(box) >= threshold:
            full_width.append(i)
        else:
            body.append(i)
    return full_width, body


def _try_two_column(
    body_indices: list[int], bboxes: list[list[float]], page_width: float
) -> tuple[int, float] | None:
    """If a two-column split is warranted, return ``(split_x, gutter)``.

    ``split_x`` is the x coordinate separating left from right columns.
    Returns ``None`` for single-column.
    """
    if len(body_indices) < MIN_FOR_TWO_COLUMN:
        return None

    centers = sorted(_center_x(bboxes[i]) for i in body_indices)

    # Largest gap between consecutive centers that falls inside the central
    # band. Gaps outside [CENTER_LO, CENTER_HI]*W are margins, not seams.
    best_gap = 0.0
    best_split = 0.0
    for left, right in zip(centers, centers[1:]):
        gap = right - left
        mid = (left + right) / 2.0
        if mid < CENTER_LO * page_width or mid > CENTER_HI * page_width:
            continue
        if gap > best_gap:
            best_gap = gap
            best_split = mid

    if best_gap < GUTTER_MIN_RATIO * page_width:
        return None

    # Require enough bboxes on each side of the seam.
    left_count = sum(1 for i in body_indices if _center_x(bboxes[i]) < best_split)
    right_count = len(body_indices) - left_count
    if left_count < MIN_PER_COLUMN or right_count < MIN_PER_COLUMN:
        return None

    return best_split, best_gap


def _sort_by_y(indices: list[int], bboxes: list[list[float]]) -> list[int]:
    return sorted(indices, key=lambda i: (bboxes[i][1], bboxes[i][0]))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_columns(
    bboxes: list[list[float]],
    *,
    page_width: float,
    page_height: float,
) -> ColumnLayout:
    """Classify a page's column structure and emit a reading order.

    Args:
        bboxes: page-points bboxes ``[x0, y0, x1, y1]`` (top-left origin).
        page_width: page width in points.
        page_height: page height in points (accepted for symmetry / future
            use; the current algorithm only needs width).

    Returns:
        :class:`ColumnLayout` with ``column_count`` 1 or 2.
    """
    if not bboxes:
        return ColumnLayout(column_count=1, columns=[], reading_order=[])

    # Drop malformed boxes entirely (they cannot inform layout).
    valid_indices = [i for i, box in enumerate(bboxes) if _is_finite_box(box)]
    if not valid_indices:
        return ColumnLayout(column_count=1, columns=[], reading_order=[])

    full_width_indices, body_indices = _split_full_width(bboxes, page_width)

    split = _try_two_column(body_indices, bboxes, page_width)

    if split is None:
        # Single column: every valid bbox (full-width + body) by ascending y.
        order = _sort_by_y(valid_indices, bboxes)
        column = Column(
            index=0,
            x_range=(0.0, float(page_width)),
            bbox_indices=list(order),
        )
        return ColumnLayout(
            column_count=1,
            columns=[column],
            reading_order=order,
        )

    split_x, _gutter = split
    left = [i for i in body_indices if _center_x(bboxes[i]) < split_x]
    right = [i for i in body_indices if _center_x(bboxes[i]) >= split_x]

    left_sorted = _sort_by_y(left, bboxes)
    right_sorted = _sort_by_y(right, bboxes)

    # Full-width furniture: place items above the body block first, then the
    # column bodies, then items below. "Above" = y0 < min body y0.
    body_min_y = min((bboxes[i][1]) for i in body_indices)
    body_max_y = max((bboxes[i][3]) for i in body_indices)
    top_full = _sort_by_y(
        [i for i in full_width_indices if bboxes[i][1] < body_min_y], bboxes
    )
    bottom_full = _sort_by_y(
        [i for i in full_width_indices if bboxes[i][1] >= body_min_y
         and bboxes[i][3] <= body_max_y + 1e-6],
        bboxes,
    )
    # Anything overlapping the body band but full-width: treat as a band
    # element placed just before the columns (e.g. a section title that
    # spans both columns). Keep deterministic by y.
    band_full = _sort_by_y(
        [i for i in full_width_indices if i not in top_full and i not in bottom_full],
        bboxes,
    )

    left_col = Column(index=0, x_range=(0.0, split_x), bbox_indices=list(left_sorted))
    right_col = Column(
        index=1, x_range=(split_x, float(page_width)), bbox_indices=list(right_sorted)
    )

    reading_order = (
        top_full + band_full + left_sorted + right_sorted + bottom_full
    )

    return ColumnLayout(
        column_count=2,
        columns=[left_col, right_col],
        reading_order=reading_order,
    )

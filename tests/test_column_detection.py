"""Unit tests for :mod:`src.ingestion.column_detection` (plan 1.4)."""

from __future__ import annotations

import unittest

from src.ingestion.column_detection import ColumnLayout, detect_columns


def _box(x0: float, y0: float, x1: float, y1: float) -> list[float]:
    return [float(x0), float(y0), float(x1), float(y1)]


# CECS758-style A4-ish page (points). Single column body text centered with
# no internal gutter.
PAGE_W = 420.0
PAGE_H = 595.0


class SingleColumnTests(unittest.TestCase):
    def test_empty_input(self) -> None:
        layout = detect_columns([], page_width=PAGE_W, page_height=PAGE_H)
        self.assertEqual(layout.column_count, 1)
        self.assertEqual(layout.reading_order, [])
        self.assertEqual(layout.columns, [])

    def test_cecs758_single_column_body(self) -> None:
        # Body text bboxes spanning most of the page width, centers near
        # 0.5*W -> no central gutter.
        bboxes = [
            _box(60, 80, 360, 100),
            _box(60, 105, 360, 125),
            _box(60, 130, 360, 150),
            _box(60, 155, 360, 175),
            _box(60, 180, 360, 200),
        ]
        layout = detect_columns(bboxes, page_width=PAGE_W, page_height=PAGE_H)
        self.assertEqual(layout.column_count, 1)
        # Reading order = ascending y.
        self.assertEqual(layout.reading_order, [0, 1, 2, 3, 4])

    def test_full_width_header_does_not_break_detection(self) -> None:
        # A header spanning >60% page width plus a centered single column.
        bboxes = [
            _box(40, 40, 380, 70),   # full-width header
            _box(70, 100, 350, 120),
            _box(70, 125, 350, 145),
            _box(70, 150, 350, 170),
            _box(70, 175, 350, 195),
            _box(40, 560, 380, 580),  # full-width footer
        ]
        layout = detect_columns(bboxes, page_width=PAGE_W, page_height=PAGE_H)
        self.assertEqual(layout.column_count, 1)
        # All by ascending y, header first, footer last.
        self.assertEqual(layout.reading_order, [0, 1, 2, 3, 4, 5])


class TwoColumnTests(unittest.TestCase):
    def test_two_column_reading_order(self) -> None:
        # Left column two bboxes, right column two bboxes, wide central
        # gutter between x=190 and x=230.
        bboxes = [
            _box(40, 80, 180, 100),    # 0 left top
            _box(40, 120, 180, 140),   # 1 left bottom
            _box(240, 80, 380, 100),   # 2 right top
            _box(240, 120, 380, 140),  # 3 right bottom
        ]
        layout = detect_columns(bboxes, page_width=PAGE_W, page_height=PAGE_H)
        self.assertEqual(layout.column_count, 2)
        # Left column top-down then right column top-down.
        self.assertEqual(layout.reading_order, [0, 1, 2, 3])
        self.assertEqual(len(layout.columns), 2)
        self.assertEqual(layout.columns[0].bbox_indices, [0, 1])
        self.assertEqual(layout.columns[1].bbox_indices, [2, 3])

    def test_two_column_with_centered_full_width_title(self) -> None:
        # A full-width section title above two body columns must not flip
        # the page to single column.
        bboxes = [
            _box(40, 60, 380, 85),    # 0 full-width title
            _box(40, 100, 180, 120),  # 1 left
            _box(40, 130, 180, 150),  # 2 left
            _box(240, 100, 380, 120),  # 3 right
            _box(240, 130, 380, 150),  # 4 right
        ]
        layout = detect_columns(bboxes, page_width=PAGE_W, page_height=PAGE_H)
        self.assertEqual(layout.column_count, 2)
        # Full-width title (above body) first, then left col, then right col.
        self.assertEqual(layout.reading_order, [0, 1, 2, 3, 4])

    def test_scattered_margin_note_does_not_flip(self) -> None:
        # One stray bbox on the right margin among many centered body lines
        # should stay single column.
        body = [_box(70, 80 + i * 25, 350, 100 + i * 25) for i in range(8)]
        margin = [_box(380, 90, 410, 110)]
        layout = detect_columns(body + margin, page_width=PAGE_W, page_height=PAGE_H)
        self.assertEqual(layout.column_count, 1)


class RobustnessTests(unittest.TestCase):
    def test_malformed_boxes_ignored(self) -> None:
        bboxes = [
            _box(60, 80, 360, 100),
            [10, 20],  # malformed
            _box(60, 105, 360, 125),
        ]
        layout = detect_columns(bboxes, page_width=PAGE_W, page_height=PAGE_H)
        self.assertEqual(layout.column_count, 1)
        self.assertEqual(layout.reading_order, [0, 2])


if __name__ == "__main__":
    unittest.main()

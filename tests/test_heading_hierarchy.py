"""Unit tests for :mod:`src.ingestion.heading_hierarchy` (plan 1.4).

The CECS758 p24 acceptance fixture is built from real evidence in
``data/review/v2/pdf_pipeline_cases.jsonl`` case ``v2-f3b23a466827-p0024-formula``:
the float-method flow formula numbered ``5.4.2-2`` lives in section ``5.4.2``,
whose context text references 浮标法 (float method) and 流量 (flow). The
heading chain on that page is therefore ``5`` > ``5.4`` > ``5.4.2``. Section
titles are inferred from the formula context (浮标法 / 流量计算); the assertion
focuses on the label chain, which is the documented acceptance point.
"""

from __future__ import annotations

import unittest

from src.ingestion.heading_hierarchy import (
    HIGH_CONFIDENCE,
    HeadingInput,
    build_section_path,
    infer_headings,
)


def _inp(text: str, idx: int, *, x0: float = 60.0, font: float | None = None) -> HeadingInput:
    return HeadingInput(text=text, bbox=[x0, 100.0 + idx * 20, 360, 118 + idx * 20],
                        source_index=idx, font_size=font)


class NumberingFormatTests(unittest.TestCase):
    def test_chinese_chapter_and_section(self) -> None:
        nodes = infer_headings([
            _inp("第一章 总则", 0),
            _inp("第二节 术语", 1),
        ])
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[0].label, "第一章")
        self.assertEqual(nodes[0].title, "总则")
        self.assertEqual(nodes[0].level, 1)
        self.assertEqual(nodes[1].label, "第二节")
        self.assertEqual(nodes[1].title, "术语")
        self.assertEqual(nodes[1].level, 2)

    def test_dotted_arabic_levels(self) -> None:
        nodes = infer_headings([
            _inp("5 管渠混接调查", 0),
            _inp("5.4 浮标法", 1),
            _inp("5.4.2 流量计算", 2),
        ])
        self.assertEqual(len(nodes), 3)
        self.assertEqual([(n.label, n.level) for n in nodes],
                         [("5", 1), ("5.4", 2), ("5.4.2", 3)])
        self.assertEqual(nodes[0].title, "管渠混接调查")
        self.assertEqual(nodes[1].title, "浮标法")
        self.assertEqual(nodes[2].title, "流量计算")

    def test_trailing_dot_numbering(self) -> None:
        # "5." with a title still parses to label "5", level 1.
        nodes = infer_headings([_inp("5. 管渠混接调查", 0)])
        self.assertEqual(nodes[0].label, "5")
        self.assertEqual(nodes[0].level, 1)

    def test_three_level_numbering(self) -> None:
        nodes = infer_headings([_inp("1.0.1 本规程适用于...", 0)])
        self.assertEqual(nodes[0].label, "1.0.1")
        self.assertEqual(nodes[0].level, 3)


class ConfidenceTests(unittest.TestCase):
    def test_explicit_numbered_high_confidence(self) -> None:
        nodes = infer_headings([_inp("5.4.2 流量计算", 0)])
        self.assertGreaterEqual(nodes[0].confidence, HIGH_CONFIDENCE)

    def test_ambiguous_low_confidence(self) -> None:
        # A bare number with no title and no other signal -> low confidence.
        nodes = infer_headings([_inp("5.", 0)])
        self.assertEqual(len(nodes), 1)
        self.assertLess(nodes[0].confidence, 0.5)

    def test_body_text_not_heading(self) -> None:
        nodes = infer_headings([
            _inp("管渠过流面积，m²", 0),
            _inp("式中：Q——流量（m³/d）", 1),
            _inp("本条说明了浮标法的使用条件。", 2),
        ])
        self.assertEqual(nodes, [])

    def test_element_caption_not_heading(self) -> None:
        nodes = infer_headings([
            _inp("表7.2.2 单个混接点或混接源分级", 0),
            _inp("图3 圆形断面", 1),
            _inp("式5.4.2-2 浮标法流量公式", 2),
        ])
        self.assertEqual(nodes, [])


class SectionPathTests(unittest.TestCase):
    def _cecs758_p24_headings(self) -> list:
        # Headings appearing at/before the p24 formula element, in reading
        # order. Titles inferred from the formula context (浮标法 / 流量).
        return infer_headings([
            _inp("5 管渠混接调查", 0),
            _inp("5.4 浮标法", 1),
            _inp("5.4.2 流量计算", 2),
            _inp("Q=1/n·Σ A×L_i/Δt_i×k×3600×24", 3),  # formula line, not heading
        ])

    def test_cecs758_p24_section_path(self) -> None:
        """Acceptance: section_path for the 5.4.2 formula element."""
        headings = self._cecs758_p24_headings()
        # The formula element is at source_index 3.
        path = build_section_path(headings, target_index=3)
        labels = [p["label"] for p in path]
        self.assertEqual(labels, ["5", "5.4", "5.4.2"])
        # Each entry has the design-doc shape {label, title}.
        for entry in path:
            self.assertIn("label", entry)
            self.assertIn("title", entry)

    def test_cecs758_p24_path_outer_to_inner(self) -> None:
        headings = self._cecs758_p24_headings()
        path = build_section_path(headings, target_index=3)
        # Levels strictly increase outer -> inner.
        # Re-derive levels from the headings to assert ordering.
        label_to_level = {h.label: h.level for h in headings}
        levels = [label_to_level[p["label"]] for p in path]
        self.assertEqual(levels, sorted(levels))
        self.assertEqual(levels, [1, 2, 3])

    def test_low_confidence_level_truncated(self) -> None:
        # Mix a high-confidence 5.4 with a low-confidence (bare-number) 5.4.1
        # that should be dropped from the path.
        nodes = infer_headings([
            _inp("5 管渠混接调查", 0),
            _inp("5.4 浮标法", 1),
            _inp("5.4.1", 2),  # bare number, low confidence
        ])
        path = build_section_path(nodes, target_index=3)
        labels = [p["label"] for p in path]
        # 5.4.1 dropped -> path stops at 5.4.
        self.assertEqual(labels, ["5", "5.4"])

    def test_path_only_high_confidence(self) -> None:
        # All-low-confidence headings -> empty path (截短, 不猜测).
        nodes = infer_headings([_inp("5.", 0), _inp("5.4.", 1)])
        path = build_section_path(nodes, target_index=2)
        self.assertEqual(path, [])

    def test_sibling_section_resets_stack(self) -> None:
        nodes = infer_headings([
            _inp("5 管渠混接调查", 0),
            _inp("5.4 浮标法", 1),
            _inp("5.4.2 流量计算", 2),
            _inp("5.5 其他方法", 3),  # sibling of 5.4
            _inp("5.5.1 一般规定", 4),
        ])
        path = build_section_path(nodes, target_index=5)
        self.assertEqual([p["label"] for p in path], ["5", "5.5", "5.5.1"])


if __name__ == "__main__":
    unittest.main()

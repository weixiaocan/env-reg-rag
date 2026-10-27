import unittest
import json
from pathlib import Path
import tempfile

from scripts.audit_completeness import require_complete_canonical_set

from src.application.page_coverage import (
    PageReference, audit_page, bigrams, normalize, page_references, toc_pages,
)
from src.application.structure_checks import (
    apply_exceptions, check_equation_numbers, check_lead_ins, check_references,
)


class CanonicalSetTest(unittest.TestCase):
    def test_incomplete_assembly_cannot_report_zero_missing_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "data/registry"
            canon = root / "data/canonical/v2/corpus-test"
            registry.mkdir(parents=True)
            canon.mkdir(parents=True)
            (registry / "corpus-test.json").write_text(json.dumps({
                "assets": [{"sha256": "a" * 64}, {"sha256": "b" * 64}]}), encoding="utf-8")
            (canon / f"{'a' * 64}.canonical.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "incomplete canonical set: 1 of 2"):
                require_complete_canonical_set(root, "corpus-test")
            (canon / f"{'b' * 64}.canonical.json").write_text("{}", encoding="utf-8")
            self.assertEqual(len(require_complete_canonical_set(root, "corpus-test")), 2)


def _text(eid, text, page=1, role="paragraph", bbox=(50, 100, 300, 112)):
    return {"element_id": eid, "type": "text", "role": role, "content": {"text": text},
            "source_spans": [{"physical_page": page, "bbox": list(bbox)}]}


def _formula(eid, latex, page=1, bbox=(150, 200, 250, 212)):
    return {"element_id": eid, "type": "formula", "role": "formula",
            "content": {"latex": latex, "formula_number": None},
            "source_spans": [{"physical_page": page, "bbox": list(bbox)}]}


def _figure(eid, caption, page=1, bbox=(60, 300, 340, 500)):
    return {"element_id": eid, "type": "figure", "role": "figure",
            "content": {"caption": caption, "references": [], "asset": None},
            "source_spans": [{"physical_page": page, "bbox": list(bbox)}]}


def _line(text, bbox):
    return {"text": text, "bbox": list(bbox)}


class NormalizeTest(unittest.TestCase):
    def test_latex_and_ocr_spellings_meet(self):
        self.assertEqual(normalize(r"t=t_{1}+t_{2}"), normalize("t=t1+t2"))
        self.assertEqual(normalize(r"Q=R\cdot V"), normalize("Q=R·V").replace("·", ""))

    def test_layout_and_width_ignored(self):
        self.assertEqual(normalize("式中：Ｑ ——流量（m³/d）"), normalize("式中:Q——流量(m3/d)"))


class PageCoverageTest(unittest.TestCase):
    def _reference(self, elements):
        return page_references({"elements": elements}).get(1, PageReference())

    def test_line_in_corpus_is_covered(self):
        ref = self._reference([_text("e1", "4.1.11 雨水管渠的降雨历时应按下式计算：")])
        [v] = audit_page(sha="s", page=1, lines=[_line("4.1.11雨水管渠的降雨历时应按下式计算：", (50, 100, 300, 112))],
                         reference=ref, page_width=400, page_height=600)
        self.assertEqual(v.status, "covered")

    def test_printed_formula_absent_from_corpus_is_missing(self):
        ref = self._reference([_text("e1", "4.1.11 雨水管渠的降雨历时应按下式计算：")])
        verdicts = audit_page(sha="s", page=1, lines=[_line("t=t1+t2", (170, 300, 215, 310))],
                              reference=ref, page_width=400, page_height=600)
        self.assertEqual(verdicts[0].status, "missing")

    def test_formula_element_covers_its_line(self):
        ref = self._reference([_formula("f1", r"t=t_{1}+t_{2}")])
        [v] = audit_page(sha="s", page=1, lines=[_line("t=t1+t2", (170, 300, 215, 310))],
                         reference=ref, page_width=400, page_height=600)
        self.assertEqual(v.status, "covered")

    def test_misread_line_confirmed_by_second_reading(self):
        ref = self._reference([_text("e1", "检查井应进行记录。")])
        line = _line("检查井应进行记求。", (50, 100, 300, 112))
        missing = audit_page(sha="s", page=1, lines=[line], reference=ref,
                             page_width=400, page_height=600)
        # The first reading alone cannot tell a misread from a loss ...
        self.assertIn(missing[0].status, {"covered", "missing"})
        confirmed = audit_page(sha="s", page=1, lines=[_line("井应进行记X求X。", (50, 100, 300, 112))],
                               reference=ref, page_width=400, page_height=600,
                               alternates=[[_line("检查井应进行记录。", (50, 100, 300, 112))]])
        self.assertEqual(confirmed[0].status, "covered")
        self.assertEqual(confirmed[0].rule, "alternate_reading")

    def test_misread_fragment_confirmed_by_full_second_reading(self):
        ref = self._reference([_text("e1", "L——试验管段的长度（m）。")])
        [verdict] = audit_page(
            sha="s", page=1, lines=[_line("I.", (80, 270, 93, 282))],
            reference=ref, page_width=400, page_height=600,
            alternates=[[_line("L——试验管段的长度（m）。", (79, 269, 220, 283))]])
        self.assertEqual((verdict.status, verdict.rule), ("covered", "alternate_reading"))

    def test_printed_number_resolves_to_numbered_formula_on_same_line(self):
        formula = _formula("f1", "x+y", bbox=(160, 300, 250, 330))
        formula["content"]["formula_number"] = "7"
        ref = self._reference([formula])
        line = _line("……(7)", (320, 310, 350, 320))
        [verdict] = audit_page(sha="s", page=1, lines=[line], reference=ref,
                               page_width=400, page_height=600)
        self.assertEqual((verdict.status, verdict.rule), ("covered", "numbered_formula"))
        line["text"] = "……(8)"
        [wrong] = audit_page(sha="s", page=1, lines=[line], reference=ref,
                             page_width=400, page_height=600)
        self.assertEqual(wrong.status, "missing")

    def test_page_number_in_footer_is_furniture(self):
        [v] = audit_page(sha="s", page=1, lines=[_line("· 14 ·", (180, 580, 220, 590))],
                         reference=PageReference(), page_width=400, page_height=600)
        self.assertEqual(v.status, "furniture")
        for number in ("Ⅱ", "·9."):
            [v] = audit_page(sha="s", page=1, lines=[_line(number, (180, 580, 220, 590))],
                             reference=PageReference(), page_width=400, page_height=600)
            self.assertEqual(v.status, "furniture")

    def test_toc_page_lines_are_not_missing(self):
        [v] = audit_page(sha="s", page=1, lines=[_line("(36)", (320, 100, 345, 110))],
                         reference=PageReference(), page_width=400, page_height=600, toc_page=True)
        self.assertEqual(v.status, "toc")

    def test_exception_names_the_line(self):
        line = _line("外部标准名称", (50, 100, 300, 112))
        [first] = audit_page(sha="s", page=1, lines=[line], reference=PageReference(),
                             page_width=400, page_height=600)
        [v] = audit_page(sha="s", page=1, lines=[line], reference=PageReference(),
                         page_width=400, page_height=600, exceptions={first.key: "reason"})
        self.assertEqual((v.status, v.rule), ("excepted", "reason"))

    def test_toc_pages_come_from_canonical_roles(self):
        canonical = {"elements": [_text("e1", "1 总则 ……(1)", page=5, role="toc_navigation_only")]}
        self.assertEqual(toc_pages(canonical), {5})

    def test_bigrams_of_short_text(self):
        self.assertEqual(bigrams("a"), {"a"})
        self.assertEqual(bigrams(""), set())


class StructureChecksTest(unittest.TestCase):
    def test_lead_in_needs_a_formula(self):
        elements = [_text("e1", "4.1.11 雨水管渠的降雨历时应按下式计算："),
                    _text("e2", "式中：t——降雨历时(min)；")]
        checks = {f.check for f in check_lead_ins("s", elements)}
        self.assertEqual(checks, {"lead_in_without_formula", "where_without_formula"})
        elements.insert(1, _formula("f1", "t=t_{1}+t_{2}"))
        self.assertEqual(check_lead_ins("s", elements), [])

    def test_underground_is_not_a_lead_in(self):
        self.assertEqual(check_lead_ins("s", [_text("e1", "地下式泵站应设置除臭装置。")]), [])

    def test_printed_equation_number_needs_a_formula(self):
        lines = {1: [_line("(4.1.11)", (300, 202, 345, 212))]}
        findings = check_equation_numbers("s", [], lines, {1: 400})
        self.assertEqual([f.check for f in findings], ["equation_number_without_formula"])
        self.assertEqual(check_equation_numbers("s", [_formula("f1", "t")], lines, {1: 400}), [])
        self.assertEqual(check_equation_numbers("s", [], lines, {1: 400}, toc_pages={1}), [])

    def test_figure_reference_resolves_to_caption(self):
        body = _text("e1", "调查与治理的环节宜包括混接预判等(图3.0.4)；")
        findings = check_references("s", [body])
        self.assertEqual([f.check for f in findings], ["figure_reference_unresolved"])
        self.assertEqual(check_references("s", [body, _figure("g1", "图3.0.4 混接调查与治理环节示意图")]), [])
        caption = _text("c1", "图\n3. 0. 4\n混接调查与治理环节示意图", role="caption")
        self.assertEqual(check_references("s", [body, caption]), [])

    def test_figure_one_resolves_to_appendix_figure_caption(self):
        body = _text("e1", "工作技术路线如图1所示。")
        self.assertEqual(check_references("s", [body, _figure("g1", "附图1 工作技术路线图")]), [])

    def test_external_table_reference_ignored(self):
        body = _text("e1", "应符合现行国家标准《给水排水管道工程施工及验收规范》GB 50268表4.3.2的规定")
        self.assertEqual(check_references("s", [body]), [])

    def test_exceptions_close_findings(self):
        [finding] = check_lead_ins("s", [_text("e1", "按下式计算：")])
        open_, excepted = apply_exceptions([finding], {finding.key: "formula is an image"})
        self.assertEqual((open_, excepted[0][1]), ([], "formula is an image"))


if __name__ == "__main__":
    unittest.main()


class FigureTitleCoverageTest(unittest.TestCase):
    def test_title_inside_figure_box_must_be_text(self):
        ref = page_references({"elements": [_figure("g1", None, bbox=(56, 62, 342, 509))]})[1]
        title, label = audit_page(
            sha="s", page=1, reference=ref, page_width=400, page_height=600,
            lines=[_line("图3.0.4 混接调查与治理环节示意图", (332, 212, 342, 362)),
                   _line("编制混接整改方案", (292, 262, 298, 303))])
        self.assertEqual(title.status, "missing")
        self.assertEqual(label.status, "geometry")


class FormulaSymbolCoverageTest(unittest.TestCase):
    def test_ocr_box_around_a_symbol_formula_counts(self):
        ref = page_references({"elements": [_formula("f1", r"t_{\circ}", bbox=(79, 253, 86, 261))]})[1]
        [v] = audit_page(sha="s", page=1, reference=ref, page_width=400, page_height=600,
                         lines=[_line("t0", (77, 252, 88, 264))])
        self.assertEqual(v.status, "geometry")

"""Audit round 4: fact-acceptance gate with per-fact bindings.

Round-3 reconciliation is kept (G037/G004 fixes; no PDF-pipeline rework).
Round-4 audit requirements, implemented here:

1. **Per-fact bindings**: every ledger entry binds the fact to the actual
   carrying ``chunk_id`` (offline-reproduced, equals the published payload
   id), the ``evidence_index`` it anchors to, and the original PDF page --
   for machine-unprovable exceptions additionally the verified page render
   and excerpt.
2. **Real probe ladder**: ``missing`` / ``pdf_only`` / ``rendering_only``
   are produced by actually probing all corpus chunks, the PDF text layer
   and the manual renderings -- never pre-seeded empty lists.
3. **Acceptance gate** (``gate_problems``): ledger hash staleness, full
   95-fact coverage, binding integrity (evidence exists, chunk exists and
   supports the fact), exception material present.  Tampered facts,
   deleted evidence, stale ledger hashes all fail.
4. **Idempotent writes**: re-running ``--write`` never duplicates evidence.
5. **Human-ruling placeholders**: every machine-unprovable semantic
   exception is marked ``human_ruling="pending"`` for explicit human
   adjudication; the resubmitted list separates 87 content facts from
   8 refusal meta-facts.

Read-only outside ``eval/``: dataset edits require ``--write``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402

from src.application.v2_chunker import chunk_document  # noqa: E402
from src.application.evidence_policy import chunk_usage_policy  # noqa: E402

from scripts.close_pending_evidence import DATASET, _append_note, _window  # noqa: E402
from scripts.reanchor_golden_v2 import _normalized  # noqa: E402

TAG = "【fact-reconciliation 2026-09-30】"
OUT_DIR = ROOT / "eval" / "audits" / "fact_reconciliation"
LEDGER = OUT_DIR / "fact_ledger.json"
RULINGS = OUT_DIR / "fact_rulings.json"
REPORT = OUT_DIR / "fact_reconciliation.md"
FACT_LIST = OUT_DIR / "fact_list.md"
RENDER_DIR = OUT_DIR / "renders"
BAR = 0.50
_NUM_TOKEN = re.compile(r"\d+(?:\.\d+)?")
_SUPERSCRIPT = str.maketrans({"³": "3", "²": "2", "¹": "1"})

# machine-unprovable semantic exception classes: each entry stays recorded
# with human_ruling="pending" for explicit human adjudication
EXCEPTION_CLASSES = {
    "covered_table_cell",
    "covered_formula_structure",
    "covered_verbatim_parts",
    "covered_ocr_variant",
    "covered_attribution_binding",
    "fixed_by_new_evidence",
    "rewritten_per_audit",
}
CONTENT_CLASSES = {"covered"} | EXCEPTION_CLASSES

# OCR-degraded numbers: (case, number) -> where the value actually lives.
OCR_NUMBER_VARIANTS = {("G046", "0.9"): "p24 OCR 'o. 9 0'（0 误读为 o）"}


# ---------------------------------------------------------------------------
# offline corpus with reproducible chunk ids
# ---------------------------------------------------------------------------

class ChunkedCorpus:
    """Offline reconstruction with the published chunk ids (deterministic)."""

    def __init__(self) -> None:
        current = json.loads((ROOT / "data" / "registry" / "corpus-current.json").read_text(encoding="utf-8"))
        canonical_dir = ROOT / "data" / "canonical" / "v2" / current["corpus_version"]
        self.documents: dict[str, dict[str, Any]] = {}
        self.chunks: dict[str, list[dict[str, Any]]] = {}
        self.pdf_paths: dict[str, str] = {}
        manifest = ROOT / "data" / "canonical" / "corpus-0b9a1fd84e3d-documents.jsonl"
        if manifest.is_file():
            for line in manifest.read_bytes().decode("utf-8").split("\n"):
                if line.strip():
                    row = json.loads(line)
                    self.pdf_paths[row["file_name"]] = (row.get("source_files") or [{}])[0].get("rel_path") or ""
        for path in canonical_dir.glob("*.canonical.json"):
            document = json.loads(path.read_text(encoding="utf-8"))
            file_name = document["source"]["file_name"]
            self.documents[file_name] = document
            self.chunks[file_name] = [
                {
                    "chunk_id": chunk.chunk_id,
                    "norm": _normalized(chunk.text),
                    "ids": set(chunk.element_ids),
                    "pages": {s.physical_page for s in chunk.source_spans or [] if s.physical_page},
                    "text": chunk.text,
                }
                for chunk in chunk_document(document)
            ]

    def find_chunk(self, doc_file: str, needle: str) -> dict[str, Any]:
        hits = [c for c in self.chunks[doc_file] if _normalized(needle) in c["norm"]]
        if len(hits) != 1:
            raise AssertionError(f"needle {needle!r} in {doc_file}: expected 1 chunk, got {len(hits)}")
        return hits[0]

    def chunk_by_id(self, doc_file: str, chunk_id: str) -> dict[str, Any] | None:
        for c in self.chunks.get(doc_file, []):
            if c["chunk_id"] == chunk_id:
                return c
        return None

    def chunk_policy(self, doc_file: str, chunk: dict[str, Any]) -> str:
        elements = self.documents[doc_file]["elements"]
        by_id = {e["element_id"]: e for e in elements}
        return chunk_usage_policy([by_id[i] for i in chunk["ids"] if i in by_id], chunk["text"])

    def pdf_pages_text(self, doc_file: str) -> dict[int, str]:
        path = ROOT / self.pdf_paths.get(doc_file, "")
        if not path.exists():
            return {}
        out: dict[int, str] = {}
        pdf = fitz.open(path)
        try:
            for i, page in enumerate(pdf):
                out[i + 1] = _normalized(page.get_text())
        finally:
            pdf.close()
        return out


# ---------------------------------------------------------------------------
# checker (red-light grade: numbers literal, text bigrams >= 0.5)
# ---------------------------------------------------------------------------

def fact_support(fact: str, raw_text: str, case_id: str = "") -> dict[str, Any]:
    numbers = [n.translate(_SUPERSCRIPT) for n in _NUM_TOKEN.findall(fact)]
    compact = re.sub(r"\s+", "", raw_text).translate(_SUPERSCRIPT)
    missing = sorted({n for n in numbers if n not in compact and (case_id, n) not in OCR_NUMBER_VARIANTS})
    text_norm = _normalized(re.sub(r"\d+(?:\.\d+)?", " ", fact))
    fact_bigrams = {
        text_norm[i] + text_norm[i + 1]
        for i in range(len(text_norm) - 1)
        if "一" <= text_norm[i] <= "鿿"
    }
    if fact_bigrams:
        t = _normalized(re.sub(r"\d+(?:\.\d+)?", " ", raw_text))
        t_bigrams = {t[i] + t[i + 1] for i in range(len(t) - 1)}
        text_cov = round(len(fact_bigrams & t_bigrams) / len(fact_bigrams), 4)
    else:
        text_cov = 1.0
    return {
        "supported": not missing and text_cov >= BAR,
        "missing_numbers": missing,
        "text_cov": text_cov,
    }


def union_support(fact: str, texts: list[str], case_id: str = "") -> dict[str, Any]:
    return fact_support(fact, "\n".join(texts), case_id)


# ---------------------------------------------------------------------------
# audit-mandated edits (round 3, kept; application is idempotent)
# ---------------------------------------------------------------------------

FACT_REWRITES = {
    ("G004", 2): (
        "两指标名称与年份不同：“十三五”指标为城市污水处理率"
        "（2020年97.5%），“十四五”目标为全国城市生活污水集中收集率"
        "（2025年力争达到70%以上）",
        "审核意见裁定（第3轮冻结审核）：原 fact『两指标口径分别为污水处理率与"
        "生活污水集中收集率，不宜直接等同』为归纳性表述，语料无『口径』注，"
        "按审核意见改写为两指标的名称与年份差异——两份证据（建设规划表1 / "
        "资源化利用规划主要目标）分别逐字佐证。pending 闭合。",
    ),
}

NEW_EVIDENCE = [
    dict(
        case="G037", after=1, needle="综合径流系数可按表5. 2. 4 的规定取值",
        start="按下式计算", end="史一综合径流系数；",
        page=20,
        rendering="式中：Q——雨水设计流量（L/s）；ψ——综合径流系数；（OCR"
                  "『史一』为 ψ——之误读；Q = ψqF（5.2.6））",
        note="fact0『Q为雨水设计流量（L/s）』的式中释义逐字在承载 chunk"
             "（式中：Q——雨水设计流量（L/s）；），5.2.3 另含设计暴雨强度句。",
    ),
    dict(
        case="G037", after=2, needle="q——设计暴雨强度",
        start=None, end=None,  # whole 1-element chunk (q 行归一化仅 11 字符)
        page=20,
        rendering="q——设计暴雨强度［L/（s·hm²）］",
        note="fact0『q为设计暴雨强度』的式中释义逐字在承载 chunk"
             "（policy=answer_and_citation 的单元素 chunk）。",
    ),
    dict(
        case="G022", after=0, needle="单站日降雨量等级以",
        start="3.1 单站日降雨量等级", end=None,
        page=9,
        rendering="3.1 单站日降雨量等级：单站日降雨量等级以该站日降雨量为划分"
                  "依据，见表1。",
        note="fact0『以该站日降雨量为划分依据，见表1』的定义句逐字在 3.1 条文"
             " chunk（p9）；表1 chunk（ev[0]）承载等级区间。",
    ),
    dict(
        case="G044", after=0, needle="降水量按24h，12h两个时间段进行",
        start="3降水量等级划分的原则", end="两个时间段进行",
        page=4,
        rendering="3 降水量等级划分的原则：降水量按24h、12h两个时间段进行划分",
        note="fact0『按24h、12h两个时段划分』的划分原则句逐字在 3 章 chunk"
             "（p4，corpus 形态『时间段』）；表 chunk（ev[0]）承载 12h/24h 阈值。",
    ),
    dict(
        case="G047", after=1, needle="重度混接(3级) | d≥600",
        start="表7. 2. 2", end=">30 | >200",
        page=28,
        rendering="表7.2.2 单个混接点或混接源混接程度分级标准：重度混接(3级)"
                  "——接入管管径d≥600mm、出流流量q>600m³/d、氨氮>30mg/L、"
                  "COD>200mg/L（中度/轻度分级见续表7.2.2）。",
        note="fact0『重度：d≥600mm且/或q>600 m3/d』的重度分级行逐字在表7.2.2"
             " chunk（p28）；续表 chunk（ev[1]）承载中度/轻度行。",
    ),
    dict(
        case="G046", after=2, needle="[公式 5.4.21]",
        start="[公式 5.4.21]", end="隔为3h ～4h;",
        page=23,
        rendering="式中：Q——流量（m3/d)；η——测定次数（次/d），每日测量"
                  "6次～8次，对应的时间间隔为3h～4h；V——第1次测定的容器内"
                  "水的体积（m3)；Δt——第1次测定的收集时间（s）。",
        note="fact0『容器体积法公式编号(5.4.21)、Q单位m3/d、每日测6~8次』的"
             "式中说明逐字在 5.4.21 式中 chunk（p23）——此前证据只有条文至"
             "公式行，式中内容仅人工 rendering 可见。",
    ),
    dict(
        case="G023", after=2, doc="短时气象服务降雨量等级TCMSA-2019.pdf",
        anchor_after=1, needle="[QX/T 489－2019,定义2.2]",
        start="2 术语和定义", end=None,
        page=5,
        rendering="2 术语和定义 [QX/T 489－2019,定义2.2]",
        note="fact1『并引用QX/T 489－2019定义』的引用注逐字在术语定义 chunk"
             "（p5）——降雨量定义引用 QX/T 489 之 2.2。",
    ),
]


def apply_edits(rows: list[dict[str, Any]], corpus: ChunkedCorpus) -> list[dict[str, Any]]:
    """Apply the audit-mandated edits. Idempotent: re-running never
    duplicates evidence or re-rewrites a fact."""
    by_case = {row["id"]: row for row in rows}
    edits: list[dict[str, Any]] = []

    for (case_id, fact_i), (new_fact, ruling) in FACT_REWRITES.items():
        case = by_case[case_id]
        if case["expected_facts"][fact_i] != new_fact:
            old = case["expected_facts"][fact_i]
            case["expected_facts"][fact_i] = new_fact
            edits.append({"edit": "fact_rewrite", "case": case_id, "fact_index": fact_i,
                          "old": old, "new": new_fact})
        evidence = case["evidence"][0]
        pendings = list(evidence.get("pending") or [])
        if "fact_coverage_needs_review" in pendings:
            evidence["pending"] = [m for m in pendings if m != "fact_coverage_needs_review"]
            _append_note(evidence, f"[fact_coverage_needs_review 闭合] {ruling}")

    for spec in NEW_EVIDENCE:
        case = by_case[spec["case"]]
        anchor = case["evidence"][spec.get("anchor_after", spec["after"])]
        doc = spec.get("doc") or anchor["doc_file"]
        # idempotency: skip if an evidence with this page+quote-head exists
        probe = corpus.find_chunk(doc, spec["needle"])
        probe_quote = _window(probe["text"], spec.get("start"), spec.get("end"), spec.get("end_mode", "inclusive"))
        if any(e.get("doc_file") == doc and e.get("page") == spec["page"]
               and _normalized(e.get("quote") or "") == _normalized(probe_quote)
               for e in case["evidence"]):
            continue
        chunk = probe
        quote = probe_quote
        assert _normalized(quote) in chunk["norm"], spec["case"]
        assert spec["page"] in chunk["pages"], spec["case"]
        policy = corpus.chunk_policy(doc, chunk)
        evidence = {
            "doc_file": doc,
            "page": spec["page"],
            "element_ids": list(chunk["ids"]),
            "quote": quote,
            "rendering": spec["rendering"],
            "must_hit": True,
            "quote_kind": "rebuilt_element_excerpt",
            "usage_policy": policy,
            "pending": [],
            "notes": TAG + spec["note"],
        }
        if anchor.get("standard_number"):
            evidence["standard_number"] = anchor["standard_number"]
        case["evidence"].insert(spec["after"] + 1, evidence)
        edits.append({"edit": "new_evidence", "case": spec["case"], "index": spec["after"] + 1,
                      "chunk_id": chunk["chunk_id"], "page": spec["page"], "policy": policy,
                      "quote_head": quote[:40], "quote_len": len(quote), "note": spec["note"]})
    return edits


# ---------------------------------------------------------------------------
# explicit semantic-exception classifications (machine-unprovable, recorded)
# ---------------------------------------------------------------------------

CLASSIFICATIONS = {
    ("G011", 0): ("covered_table_cell",
                  "表格单元格事实：6.1 逐字在承载 chunk 氨氮行×灰水列×均值列"
                  "（『氨氮（mg/L） | 3.6~9.6 | 6.1 |…』），行/列表头逐字在场；"
                  "关联为表格位置性，bigram 度量跨单元格不连接。"),
    ("G011", 1): ("covered_table_cell",
                  "表格单元格事实：46.8~109.3 与均值 76.8 逐字在承载 chunk"
                  " 黑水行（『黑水…46.8~109.3 | 76.8』），表头逐字在场。"),
    ("G019", 0): ("covered_table_cell",
                  "表格单元格事实：灰水 6.1、黑水 76.8 逐字在承载 chunk 表5-1"
                  "（『灰水 | 3.6~9.6 | 6.1…黑水 | 46.8~109 | 76.8』），表头"
                  "（水量来源/范围/均值）逐字在场。"),
    ("G047", 1): ("covered_table_cell",
                  "表格单元格事实：轻度混接(1级) 行 d<300 | q<200 逐字在续表"
                  " chunk（m³/d 为上标 3，已归一）；『量级』为设问措辞。"),
    ("G006", 0): ("covered_formula_structure",
                  "公式结构事实：LaTeX 公式逐字在承载 chunk——"
                  "R=(w·Σ(r_max)_i+(1-w)·Σ(Σr_d/T0)_i)/n，即最大日雨量项"
                  "（w 加权）与过程平均日雨量项（Σr_d/T0）加权后对 n 个测站"
                  "平均；改写措辞的 bigram 度量无法核对数学结构。"),
    ("G006", 1): ("covered_formula_structure",
                  "式中定义逐字在承载 chunk：权重 w（区域取0.5）、rmax"
                  "（日最大雨量值）、rd（日雨量值）、T0（降雨过程持续时间）——"
                  "『过程累计日雨量』为 Σr_d 的结构表述。"),
    ("G012", 0): ("covered_verbatim_parts",
                  "标准号『DB 4201/T 651—2021』逐字在封面承载 chunk"
                  "（2021-11-12 发布 / 2021-12-13 实施）；残余 bigram"
                  "（标准号为）为设问填充词。"),
    ("G013", 0): ("covered_verbatim_parts",
                  "步骤名『混错接点判定』『混错接流量测定』逐字在目次承载"
                  " chunk（5.4/5.5 节名）；残余（包含/调查评估步骤）为设问措辞。"),
    ("G035", 0): ("covered_verbatim_parts",
                  "『Qs = qΨ』与『汇水面积（hm2）』逐字在承载 chunk 式中结构；"
                  "残余（表述为/见式中说明）为设问措辞。"),
    ("G046", 2): ("covered_verbatim_parts",
                  "两种方法的一天内多次测量均有逐字支撑：5.4.21 条文"
                  "『每日流量应基于一天内多次的测量数据』（ev[2]，『→天』为"
                  " OCR 一天之误读）与两式 η『每日测量6次～8次』；残余"
                  "（两种/均/取用）为设问措辞。"),
    ("G046", 1): ("covered_ocr_variant",
                  "0.9 在承载 chunk 为扫描 OCR『o. 9 0』（0 误读为 o），"
                  "数值在场；公式编号(5.4.22)与 0.8 逐字在 chunk。"),
    ("G019", 1): ("covered_attribution_binding",
                  "内容句 t≈0.9（接接入管管径/混接水量/混接水质/评估，混接"
                  "为 OCR『1昆』mojibake）；标准号 758 为文档归属——逐字在"
                  "封面 chunk（p1『T / CECS 7 58-2020』），由证据文档绑定"
                  "佐证，非该 chunk 正文。"),
}
FIXED_BY_NEW_EVIDENCE = {
    ("G023", 1): "新增术语定义 chunk（p5，『[QX/T 489－2019,定义2.2]』逐字）",
    ("G046", 0): "新增 5.4.21 式中 chunk（p23，Q单位m3/d、每日测量6~8次、"
                 "间隔3h~4h 逐字）",
}


# ---------------------------------------------------------------------------
# auditable human-ruling registry (round-4 rework, second pass)
#
# Adjudication records live in ``fact_rulings.json`` -- a separate registry
# that ledger regeneration READS but never writes, so recorded rulings
# survive regeneration.  The ledger's ``human_ruling`` is always COMPUTED
# from (classification, valid rulings) -- hand-editing the string never
# passes, the gate recomputes the expected value.
# ---------------------------------------------------------------------------

RULING_STATUSES = {"accepted", "rejected"}


def load_rulings(path: Path | None = None) -> dict[str, Any]:
    """Load the rulings registry; missing registry == no recorded rulings."""
    path = path or RULINGS
    if not path.is_file():
        return {"rulings": []}
    return json.loads(path.read_text(encoding="utf-8"))


def ruling_ok(ruling: dict[str, Any], fact: str, classification: str) -> bool:
    """A ruling applies to a fact iff it is a complete record for exactly
    that fact text and its current ledger classification."""
    return (
        ruling.get("ruling") in RULING_STATUSES
        and bool(ruling.get("adjudicator"))
        and bool(ruling.get("date"))
        and bool(ruling.get("justification"))
        and ruling.get("fact") == fact
        and ruling.get("classification") == classification
    )


def expected_human_ruling(entry: dict[str, Any], rulings: dict[str, Any]) -> str:
    """Status transition rule (authoritative, recomputed by the gate):
    refuse_meta -> n/a; covered -> machine_approved; machine-unprovable
    exception -> human_adjudicated iff a complete recorded ruling applies,
    otherwise pending (which blocks formal)."""
    cls = entry.get("classification")
    if cls == "refuse_meta":
        return "n/a"
    if cls == "covered":
        return "machine_approved"
    ruling = next(
        (r for r in rulings.get("rulings") or []
         if r.get("case") == entry.get("case")
         and r.get("fact_index") == entry.get("fact_index")
         and ruling_ok(r, entry.get("fact") or "", cls)),
        None,
    )
    if ruling is not None and ruling.get("ruling") == "accepted":
        return "human_adjudicated"
    return "pending"


def ruling_conflicts(ledger: dict[str, Any], rulings: dict[str, Any]) -> list[str]:
    """Ruling records that conflict with the per-fact acceptance rules:
    rulings on covered/refuse_meta facts (no ruling needed), stale fact
    texts, classification mismatches, incomplete records, duplicates."""
    problems: list[str] = []
    entries = {(e["case"], e["fact_index"]): e for e in ledger.get("facts") or []}
    seen: set[tuple[str, int]] = set()
    for r in rulings.get("rulings") or []:
        key = (r.get("case"), r.get("fact_index"))
        marker = f"{key[0]}[f{key[1]}]"
        if key in seen:
            problems.append(f"{marker}: duplicate ruling record")
            continue
        seen.add(key)
        entry = entries.get(key)
        if entry is None:
            problems.append(f"{marker}: ruling for a fact not in the ledger (stale)")
            continue
        cls = entry.get("classification")
        if cls in ("covered", "refuse_meta"):
            problems.append(
                f"{marker}: ruling conflicts with per-fact rules "
                f"(classification {cls} takes no ruling)"
            )
            continue
        if cls not in EXCEPTION_CLASSES:
            problems.append(f"{marker}: ruling on non-exception class {cls!r}")
            continue
        if not ruling_ok(r, entry.get("fact") or "", cls):
            problems.append(
                f"{marker}: ruling record conflicts with the ledger "
                "(incomplete/stale: fact text, classification, adjudicator, "
                "date, justification and status must all match)"
            )
    return problems


# ---------------------------------------------------------------------------
# carriers + reconciliation
# ---------------------------------------------------------------------------

def carrier_for(corpus: ChunkedCorpus, evidence: dict[str, Any]) -> dict[str, Any] | None:
    doc = evidence.get("doc_file")
    ids = set(evidence.get("element_ids") or [])
    if not doc or not ids:
        return None
    sup = [c for c in corpus.chunks[doc]
           if _normalized(evidence["quote"]) in c["norm"]
           and ids <= c["ids"] and evidence["page"] in c["pages"]]
    if not sup:
        return None
    exact = [c for c in sup if c["ids"] == ids]
    return exact[0] if exact else max(sup, key=lambda c: len(c["ids"] & ids))


def case_carriers(corpus: ChunkedCorpus, row: dict[str, Any]) -> list[tuple[int, dict[str, Any], dict[str, Any]]]:
    out = []
    for idx, ev in enumerate(row.get("evidence") or []):
        if not ev.get("must_hit", True):
            continue
        chunk = carrier_for(corpus, ev)
        if chunk is not None:
            out.append((idx, ev, chunk))
    return out


def classify_fact(case_id: str, fi: int, fact: str, row: dict[str, Any],
                  corpus: ChunkedCorpus, refuse_cases: set[str],
                  pdf_cache: dict[str, dict[int, str]],
                  union_supported: bool) -> tuple[str, str]:
    """THE established classification rules, stated once so the ledger and
    the acceptance gate compute the category from the case, its evidence
    and the rules -- never from the ledger's own label.  Only true refuse
    cases (expected_answerability == refuse) can be refuse_meta."""
    if case_id in refuse_cases:
        return ("refuse_meta",
                "拒答题 meta-fact（应拒答/不应编造/不得套用），非语料内容事实，"
                "不入内容证据对账分母。")
    explicit = CLASSIFICATIONS.get((case_id, fi))
    if union_supported:
        if explicit:
            return explicit
        return "covered", ""
    if (case_id, fi) in FIXED_BY_NEW_EVIDENCE:
        return ("fixed_by_new_evidence",
                "本轮补证据修复：" + FIXED_BY_NEW_EVIDENCE[(case_id, fi)])
    if explicit:
        return explicit
    return _probe_ladder(fact, row, corpus, pdf_cache, case_id)


def reconcile(rows: list[dict[str, Any]], corpus: ChunkedCorpus,
              rulings: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, list]]:
    """Full 50-case reconciliation with a REAL probe ladder: missing /
    pdf_only / rendering_only are produced by probing, never pre-seeded.
    ``human_ruling`` is computed from the rulings registry -- regeneration
    reads recorded rulings but cannot overwrite or erase them."""
    refuse_cases = {row["id"] for row in rows if row.get("expected_answerability") == "refuse"}
    rulings = rulings if rulings is not None else load_rulings()
    fact_entries: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    pdf_cache: dict[str, dict[int, str]] = {}

    for row in rows:
        case_id = row["id"]
        carriers = case_carriers(corpus, row)
        union_texts = [chunk["text"] for _, _, chunk in carriers]
        for fi, fact in enumerate(row.get("expected_facts") or []):
            per = []
            for idx, ev, chunk in carriers:
                s = fact_support(fact, chunk["text"], case_id)
                per.append({
                    "evidence_index": idx,
                    "chunk_id": chunk["chunk_id"],
                    "pdf_page": ev["page"],
                    "doc": ev["doc_file"],
                    "supported": s["supported"],
                    "missing_numbers": s["missing_numbers"],
                    "text_cov": s["text_cov"],
                })
            us = union_support(fact, union_texts, case_id)
            cls, note = classify_fact(
                case_id, fi, fact, row, corpus, refuse_cases, pdf_cache,
                us["supported"],
            )
            # bindings: fact -> actual chunk_id + evidence index + pdf page
            bindings = [
                {
                    "evidence_index": idx,
                    "doc": ev["doc_file"],
                    "chunk_id": chunk["chunk_id"],
                    "pdf_page": ev["page"],
                    "supported": fact_support(fact, chunk["text"], case_id)["supported"],
                }
                for idx, ev, chunk in carriers
            ]
            entry = {
                "case": case_id, "fact_index": fi, "fact": fact,
                "answerability": row.get("expected_answerability"),
                "per_evidence": per,
                "union_supported": None if case_id in refuse_cases else us["supported"],
                "union_missing_numbers": us.get("missing_numbers"),
                "union_text_cov": us.get("text_cov"),
                "bindings": bindings,
                "classification": cls,
                "human_ruling": expected_human_ruling(
                    {"case": case_id, "fact_index": fi, "fact": fact, "classification": cls},
                    rulings,
                ),
                "note": note,
            }
            if cls in ("pdf_only", "rendering_only", "missing"):
                entry["probe_details"] = _probe_details(fact, row, corpus, pdf_cache, case_id)
            fact_entries.append(entry)
            counts[cls] = counts.get(cls, 0) + 1
    return fact_entries, counts, _gap_categories(fact_entries)


def _probe_ladder(fact: str, row: dict[str, Any], corpus: ChunkedCorpus,
                  pdf_cache: dict[str, dict[int, str]], case_id: str) -> tuple[str, str]:
    """Real classification: chunk_elsewhere (fixable) must have been fixed
    this round; pdf_only / rendering_only / missing are the irreducible
    gap categories.  Returns (classification, note)."""
    # probe 1: any chunk in the corpus supports it (fixable, should not remain)
    for doc, chunks in corpus.chunks.items():
        for c in chunks:
            if fact_support(fact, c["text"], case_id)["supported"]:
                return "unresolved", f"chunk else-where supports it: {doc} {c['chunk_id']}"
    # probe 2: PDF text layer of the evidence docs
    for ev in row.get("evidence") or []:
        doc = ev.get("doc_file")
        if doc not in pdf_cache:
            pdf_cache[doc] = corpus.pdf_pages_text(doc)
        for pg, text in pdf_cache[doc].items():
            if fact_support(fact, text, case_id)["supported"]:
                return "pdf_only", f"only visible in the PDF text layer: {doc} p{pg}（chunk 不可见，不得关闭）"
    # probe 3: manual renderings
    for ev in row.get("evidence") or []:
        r = ev.get("rendering")
        if r and fact_support(fact, r, case_id)["supported"]:
            return "rendering_only", f"only visible in the manual rendering: {ev.get('doc_file')}（chunk 不可见，不得关闭）"
    return "missing", "语料与渲染均不可见（记录，待人工裁定）"


def _probe_details(fact: str, row: dict[str, Any], corpus: ChunkedCorpus,
                   pdf_cache: dict[str, dict[int, str]], case_id: str) -> dict[str, Any]:
    best_chunk, best_pdf, best_rend = None, None, None
    for doc, chunks in corpus.chunks.items():
        for c in chunks:
            s = fact_support(fact, c["text"], case_id)
            if s["text_cov"] and (best_chunk is None or s["text_cov"] > best_chunk["text_cov"]):
                best_chunk = {"doc": doc, "chunk_id": c["chunk_id"], "text_cov": s["text_cov"]}
    for ev in row.get("evidence") or []:
        doc = ev.get("doc_file")
        if doc not in pdf_cache:
            pdf_cache[doc] = corpus.pdf_pages_text(doc)
        for pg, text in pdf_cache[doc].items():
            s = fact_support(fact, text, case_id)
            if best_pdf is None or s["text_cov"] > best_pdf["text_cov"]:
                best_pdf = {"doc": doc, "page": pg, "text_cov": s["text_cov"]}
        r = ev.get("rendering")
        if r:
            s = fact_support(fact, r, case_id)
            if best_rend is None or s["text_cov"] > best_rend["text_cov"]:
                best_rend = {"doc": doc, "text_cov": s["text_cov"]}
    return {"best_chunk": best_chunk, "best_pdf_page": best_pdf, "best_rendering": best_rend}


def _gap_categories(fact_entries: list[dict[str, Any]]) -> dict[str, list]:
    return {
        cat: [
            {"case": e["case"], "fact_index": e["fact_index"], "fact": e["fact"]}
            for e in fact_entries if e["classification"] == cat
        ]
        for cat in ("missing", "pdf_only", "rendering_only")
    }


# ---------------------------------------------------------------------------
# acceptance gate
# ---------------------------------------------------------------------------

def gate_problems(rows: list[dict[str, Any]], corpus: ChunkedCorpus,
                  ledger: dict[str, Any], dataset_sha: str,
                  rulings: dict[str, Any] | None = None) -> list[str]:
    problems: list[str] = []
    if ledger is None:
        return ["fact ledger missing -- run scripts/reconcile_fact_evidence.py --write"]
    if ledger.get("dataset_sha256") != dataset_sha:
        problems.append(
            f"ledger hash stale: ledger {str(ledger.get('dataset_sha256'))[:16]}… "
            f"!= dataset {dataset_sha[:16]}…"
        )
    rulings = rulings if rulings is not None else load_rulings()
    raw_facts = ledger.get("facts") or []
    entries = {(e["case"], e["fact_index"]): e for e in raw_facts}
    if len(raw_facts) != len(entries):
        problems.append(
            f"ledger holds {len(raw_facts) - len(entries)} duplicate fact "
            "entries -- authoritative acceptance refuses duplicates"
        )
    expected = {
        (row["id"], fi) for row in rows
        for fi in range(len(row.get("expected_facts") or []))
    }
    if set(entries) != expected:
        problems.append(
            f"ledger coverage mismatch: {len(set(entries) - expected)} stale, "
            f"{len(expected - set(entries))} missing entries"
        )
    refuse_cases = {row["id"] for row in rows if row.get("expected_answerability") == "refuse"}
    pdf_cache: dict[str, dict[int, str]] = {}
    for row in rows:
        case_id = row["id"]
        carriers = case_carriers(corpus, row)
        carrier_texts = [c["text"] for _, _, c in carriers]
        for fi, fact in enumerate(row.get("expected_facts") or []):
            entry = entries.get((case_id, fi))
            if entry is None:
                continue
            if entry.get("fact") != fact:
                problems.append(f"{case_id}[f{fi}]: ledger fact text mismatch (tampered or stale)")
                continue
            cls = entry.get("classification")
            # the ledger's classification is NEVER trusted: recompute it
            # from the case, its evidence and the established rules -- only
            # true refuse cases can be refuse_meta, so a relabeled content
            # fact cannot bypass bindings or adjudication
            us = union_support(fact, carrier_texts, case_id)
            recomputed_cls, _note = classify_fact(
                case_id, fi, fact, row, corpus, refuse_cases, pdf_cache,
                us["supported"],
            )
            if cls != recomputed_cls:
                problems.append(
                    f"{case_id}[f{fi}]: classification {cls!r} != recomputed "
                    f"{recomputed_cls!r} (relabeled)"
                )
                continue
            if cls == "refuse_meta":
                continue
            # binding integrity: evidence index exists, unique and must_hit;
            # doc matches the evidence; chunk id exists in that doc, carries
            # the evidence quote/element_ids, and actually supports the fact
            # -- the ledger's self-filled supported flags are NEVER trusted,
            # they are recomputed here and any mismatch fails
            if not entry.get("bindings"):
                problems.append(f"{case_id}[f{fi}]: no bindings")
                continue
            indices = [b.get("evidence_index") for b in entry["bindings"]]
            if len(indices) != len(set(indices)):
                problems.append(f"{case_id}[f{fi}]: duplicate bindings (evidence_index {indices})")
                continue
            evidences = row.get("evidence") or []
            for binding in entry["bindings"]:
                idx = binding.get("evidence_index")
                if not isinstance(idx, int) or idx >= len(evidences) or not evidences[idx].get("must_hit", True):
                    problems.append(f"{case_id}[f{fi}]: binding evidence_index {idx} invalid/deleted")
                    continue
                ev = evidences[idx]
                if binding.get("doc") != ev.get("doc_file"):
                    problems.append(f"{case_id}[f{fi}]: binding doc {binding.get('doc')!r} != evidence doc")
                if binding.get("pdf_page") != ev.get("page"):
                    problems.append(f"{case_id}[f{fi}]: binding pdf_page != evidence page")
                chunk = corpus.chunk_by_id(ev.get("doc_file"), binding.get("chunk_id"))
                if chunk is None:
                    problems.append(f"{case_id}[f{fi}]: bound chunk_id {binding.get('chunk_id')} not in corpus")
                    continue
                quote = _normalized(ev.get("quote") or "")
                if quote not in chunk["norm"] or not set(ev.get("element_ids") or []) <= chunk["ids"]:
                    problems.append(
                        f"{case_id}[f{fi}]: bound chunk does not carry the evidence quote/element_ids"
                    )
                recomputed = fact_support(fact, chunk["text"], case_id)["supported"]
                if recomputed != bool(binding.get("supported")):
                    problems.append(
                        f"{case_id}[f{fi}]: binding supported flag mismatch "
                        f"(recorded {binding.get('supported')}, recomputed {recomputed})"
                    )
            # the recorded human_ruling string is never trusted -- the expected
            # value is recomputed from classification + rulings registry for
            # EVERY entry (a hand-edited string never passes)
            expected_ruling = expected_human_ruling(entry, rulings)
            if entry.get("human_ruling") != expected_ruling:
                problems.append(
                    f"{case_id}[f{fi}]: human_ruling {entry.get('human_ruling')!r} "
                    f"!= recomputed {expected_ruling!r} (hand-edited or stale)"
                )
            # machine-unprovable exceptions additionally need a ruling note
            if cls in EXCEPTION_CLASSES and not entry.get("note"):
                problems.append(f"{case_id}[f{fi}]: exception without ruling note")
            if cls == "covered":
                # same semantics as the checker: the union of must_hit
                # carriers supports the fact (numbers and bigrams may be
                # spread across several carriers)
                union_ok = union_support(fact, carrier_texts, case_id)["supported"]
                if not union_ok:
                    problems.append(f"{case_id}[f{fi}]: classified covered but no carrier supports it")
    problems += ruling_conflicts(ledger, rulings)
    return problems


def authoritative_acceptance(dataset_path: Path,
                             ledger_path: Path | None = None,
                             rulings_path: Path | None = None,
                             corpus: ChunkedCorpus | None = None) -> list[str]:
    """THE authoritative fact acceptance, actually invoked by the formal
    evaluation entry.  Verifies: ledger exists/readable, hash bound to the
    current dataset, full 95-fact coverage without duplicates, every
    binding (evidence index unique/valid, doc, chunk carries the quote and
    element_ids, recomputed support), all human_ruling values against the
    recomputed status transitions, and all ruling records against the
    per-fact rules."""
    ledger_file = ledger_path or LEDGER
    if not ledger_file.is_file():
        return [f"fact-ledger missing ({Path(ledger_file).name}) -- run scripts/reconcile_fact_evidence.py --write"]
    try:
        ledger = json.loads(Path(ledger_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"fact-ledger unreadable ({Path(ledger_file).name}): {exc}"]
    sha = hashlib.sha256(Path(dataset_path).read_bytes()).hexdigest()
    if ledger.get("dataset_sha256") != sha:
        return [
            f"fact-ledger hash stale: ledger {str(ledger.get('dataset_sha256'))[:16]}… "
            f"!= dataset {sha[:16]}… -- regenerate the ledger before a formal run"
        ]
    raw = Path(dataset_path).read_bytes().decode("utf-8")
    rows = [json.loads(line) for line in raw.split("\n") if line.strip()]
    corpus = corpus or ChunkedCorpus()
    return gate_problems(rows, corpus, ledger, sha, load_rulings(rulings_path))


# ---------------------------------------------------------------------------
# verification material (page renders for exception facts)
# ---------------------------------------------------------------------------

def render_exception_pages(fact_entries: list[dict[str, Any]], corpus: ChunkedCorpus,
                           rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    by_case = {row["id"]: row for row in rows}
    pages: dict[tuple[str, int], dict[str, str]] = {}
    for e in fact_entries:
        if e["classification"] not in EXCEPTION_CLASSES:
            continue
        evidences = by_case[e["case"]].get("evidence") or []
        pairs = {
            (evidences[b["evidence_index"]]["doc_file"], b["pdf_page"])
            for b in e["bindings"]
        }
        for b in e["per_evidence"]:
            pairs.add((b["doc"], b["pdf_page"]))
        for key in pairs:
            if key not in pages:
                pages[key] = {"doc": key[0], "page": key[1], "render": ""}
    rendered: list[dict[str, str]] = []
    for key, info in sorted(pages.items()):
        tag = re.sub(r"[^\w一-鿿]+", "_", Path(info["doc"]).stem)
        tag = re.sub(r"_+", "_", tag).strip("_")[:24]
        out = RENDER_DIR / f"{tag}-p{info['page']:02d}.png"
        if not out.exists():
            pdf_path = ROOT / corpus.pdf_paths.get(info["doc"], "")
            if pdf_path.exists():
                pdf = fitz.open(pdf_path)
                try:
                    if 1 <= info["page"] <= pdf.page_count:
                        out.parent.mkdir(parents=True, exist_ok=True)
                        pdf.load_page(info["page"] - 1).get_pixmap(matrix=fitz.Matrix(2, 2)).save(out)
                finally:
                    pdf.close()
        info["render"] = str(out.relative_to(ROOT)) if out.exists() else ""
        rendered.append(info)
    return rendered


# ---------------------------------------------------------------------------
# reports
# ---------------------------------------------------------------------------

def _write_reports(fact_entries: list[dict[str, Any]], counts: dict[str, int],
                   gaps: dict[str, list], edits: list[dict[str, Any]],
                   sha_before: str, sha_after: str, renders: list[dict[str, str]]) -> None:
    lines = [
        "# expected_fact → 实际承载 chunk → 原 PDF 对账（第 4 轮验收门，2026-09-30）",
        "",
        f"数据集 sha256：`{sha_before}` → `{sha_after}`；台账绑定同一哈希，",
        "数据集任何改动而未重生成台账 → 验收门哈希过期失败。",
        "",
        "## 匹配器（红灯级）",
        "",
        "数字事实：逐字比对（空格剥离、上标 ³²¹ 归一）；文本事实：非数字部分",
        "CJK bigram 比率 ≥ 0.5；作用域为 must_hit 承载 chunk 并集。锚点可定位",
        "不等于事实有证据——机器无法证明的语义例外逐项 `human_ruling=pending`",
        "留人工裁定，不得静默关闭。",
        "",
        "## 绑定（第 4 轮）",
        "",
        "每条 fact 台账绑定：**实际 chunk_id**（离线复现、等于发布 payload id）+",
        "**证据索引** + **原 PDF 页**；机器不可证明的例外另附已核验页图",
        f"（`renders/`，{len(renders)} 页）与摘录（quote）。",
        "",
        "## 分类计数（87 内容事实 + 8 拒答事实）",
        "",
        "| 分类 | 数量 | 含义 | human_ruling |",
        "| --- | --- | --- | --- |",
    ]
    meanings = {
        "covered": ("机器支持（数字逐字 + bigram ≥0.5）", "machine_approved"),
        "covered_table_cell": ("表格单元格事实：数值与行/列表头逐字在 chunk，关联为表格位置性", "pending"),
        "covered_formula_structure": ("公式结构事实：LaTeX 公式/式中定义逐字在 chunk", "pending"),
        "covered_verbatim_parts": ("内容 token 逐字在 chunk，残余 bigram 为设问措辞（逐条留痕）", "pending"),
        "covered_ocr_variant": ("数值在 chunk 为 OCR 退化形态（逐条留痕）", "pending"),
        "covered_attribution_binding": ("标准号为文档归属：逐字在封面 chunk，由证据文档绑定佐证", "pending"),
        "fixed_by_new_evidence": ("本轮补证据修复（新增 must_hit 证据）", "pending"),
        "rewritten_per_audit": ("按审核意见改写 fact", "pending"),
        "refuse_meta": ("拒答题 meta-fact，不入内容证据对账分母", "n/a"),
    }
    for cls in ["covered", "covered_table_cell", "covered_formula_structure",
                "covered_verbatim_parts", "covered_ocr_variant", "covered_attribution_binding",
                "fixed_by_new_evidence", "rewritten_per_audit", "refuse_meta"]:
        if counts.get(cls):
            m, ruling = meanings[cls]
            lines.append(f"| `{cls}` | {counts[cls]} | {m} | {ruling} |")
    lines += [
        "",
        "## 缺失 / 仅 PDF / 仅 rendering（真实探针阶梯产生，记录不关闭）",
        "",
    ]
    any_gap = False
    for cat, items in gaps.items():
        for e in items:
            any_gap = True
            lines.append(f"- `{cat}` {e['case']}[f{e['fact_index']}]：{e['fact'][:80]}")
    if not any_gap:
        lines.append("真实探针（全库 chunk → PDF 文本层 → 人工 rendering）逐 fact 探测："
                     "三类不可机器佐证缺口 **0**。")
    lines += ["", "## 结构性编辑（第 3 轮，保留）", ""]
    for e in edits:
        if e["edit"] == "fact_rewrite":
            lines.append(f"- **fact 改写** {e['case']}[f{e['fact_index']}]（审核裁定）：{e['new'][:60]}…")
        else:
            lines.append(f"- **补证据** {e['case']}[f{e['index']}] p{e['page']} "
                         f"chunk `{e['chunk_id'][:20]}…` policy={e['policy']}：{e['note']}")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # resubmitted fact list: 87 content + 8 refusal
    lst = [
        "# golden_v2 事实清单（第 4 轮重提交，2026-09-30）",
        "",
        "87 条内容事实 + 8 条拒答 meta-fact；每条绑定实际 chunk_id / 证据索引 /",
        "原 PDF 页；`human_ruling=pending` 为机器无法证明的语义例外，逐项待人工裁定。",
        "",
    ]
    for e in fact_entries:
        refuse = e["classification"] == "refuse_meta"
        b = "; ".join(
            f"ev{bb['evidence_index']}→{bb['chunk_id'][:24]}… p{bb['pdf_page']}"
            for bb in e["bindings"]
        ) or "—"
        tag = " [拒答]" if refuse else ""
        ruling = "" if e["human_ruling"] in ("machine_approved", "n/a") else " **待人工裁定**"
        lst.append(
            f"### {e['case']}[f{e['fact_index']}]{tag} — {e['classification']}{ruling}",
        )
        lst.append(f"- fact：{e['fact']}")
        lst.append(f"- 绑定：{b}")
        if e["note"]:
            lst.append(f"- 留痕：{e['note']}")
        lst.append("")
    FACT_LIST.write_text("\n".join(lst), encoding="utf-8")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="apply dataset edits")
    args = parser.parse_args()

    raw = DATASET.read_bytes().decode("utf-8")
    rows = [json.loads(line) for line in raw.split("\n") if line.strip()]
    sha_before = hashlib.sha256(DATASET.read_bytes()).hexdigest()
    corpus = ChunkedCorpus()

    edits = apply_edits(rows, corpus)
    rulings = load_rulings()
    fact_entries, counts, gaps = reconcile(rows, corpus, rulings)

    # numbers gate for the explicit content classes
    problems: list[str] = []
    by_case_carriers = {row["id"]: case_carriers(corpus, row) for row in rows}
    for entry in fact_entries:
        if entry["classification"] not in {
            "covered_table_cell", "covered_verbatim_parts", "covered_formula_structure",
        }:
            continue
        union_text = "\n".join(c["text"] for _, _, c in by_case_carriers[entry["case"]])
        nums = [n.translate(_SUPERSCRIPT) for n in _NUM_TOKEN.findall(entry["fact"])]
        compact = re.sub(r"\s+", "", union_text).translate(_SUPERSCRIPT)
        missing = sorted({n for n in nums if n not in compact and (entry["case"], n) not in OCR_NUMBER_VARIANTS})
        if missing:
            problems.append(
                f"{entry['case']}[f{entry['fact_index']}] {entry['classification']}: "
                f"numbers {missing} not in carrier union"
            )

    print(f"dataset sha256 before: {sha_before}")
    print(f"edits applied: {len(edits)}"
          + (f" {[e.get('edit') for e in edits]}" if edits else " (idempotent no-op)"))
    print(f"evidence rows: {sum(len(r.get('evidence') or []) for r in rows)} "
          f"(must_hit {sum(1 for r in rows for e in r.get('evidence') or [] if e.get('must_hit', True))})")
    print(f"classification counts: {json.dumps(counts, ensure_ascii=False)}")
    print(f"gap categories: {json.dumps({k: len(v) for k, v in gaps.items()})}")
    print(f"problems: {problems or 'none'}")

    if problems:
        print("REFUSING to write: problems present (see above)")
        sys.exit(1)
    if not args.write:
        print("dry-run only; pass --write to apply")
        return

    out = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    DATASET.write_text(out, encoding="utf-8")
    sha_after = hashlib.sha256(DATASET.read_bytes()).hexdigest()
    print(f"dataset sha256 after:  {sha_after}")

    renders = render_exception_pages(fact_entries, corpus, rows)
    # re-reconcile is unnecessary: bindings/classifications are sha-stable;
    # ledger binds the post-edit hash
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps({
        "generated": "2026-09-30",
        "dataset_sha256": sha_after,
        "checker": "numbers literal in space-stripped text (superscripts normalised) "
                   "+ non-numeric CJK bigram ratio >= 0.5, over the union of must_hit carriers",
        "threshold": BAR,
        "classification_counts": counts,
        "gap_categories": gaps,
        "verified_renders": renders,
        "edits": edits,
        "facts": fact_entries,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"ledger -> {LEDGER}")
    # authoritative self-check: the freshly written ledger must pass the
    # same acceptance the formal entry runs (recorded rulings survive
    # regeneration; pending rulings keep blocking formal)
    residual = authoritative_acceptance(DATASET, LEDGER, RULINGS, corpus)
    print(f"authoritative self-check: {'PASS' if not residual else residual}")
    _write_reports(fact_entries, counts, gaps, edits, sha_before, sha_after, renders)
    print(f"report -> {REPORT}")
    print(f"fact list -> {FACT_LIST}")


if __name__ == "__main__":
    main()

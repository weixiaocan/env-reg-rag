"""Prepare the human-adjudication pack for the 14 pending fact rulings.

READ-ONLY over the dataset, corpus and ledger; writes ONLY inside
``eval/audits/fact_reconciliation/`` (the pack markdown and, for
auxiliary verification material, page renders derived from the original
PDFs).  It never fills in a ruling, never touches the golden dataset and
never modifies the gate -- the accept/reject decision belongs to the
responsible human reviewer; the pack only frames the point to be judged.

Process encoded here (order matters):

1. 逐条裁定并处理驳回项（驳回项按 justification 处置：改写 fact /
   调整 answerability / 重锚，处置后重新对账并重出材料）；
2. 逐题确认 status=frozen；
3. 重生成绑定**最终数据集哈希**的台账（``--write``；裁定注册表只读合入，
   human_ruling 由重算得出，不得手改台账字符串）；
4. 只读门禁复验（``_fact_ledger_blockers(full=True)`` / ``gate_problems``
   必须返回空）；``--formal`` 未经另行授权不得运行。

Auxiliary verification material (e.g. a cover page for a standard-number
attribution fact) is clearly marked as such: it is NOT a must-hit
binding and does NOT change the golden set.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402

OUT_DIR = ROOT / "eval" / "audits" / "fact_reconciliation"
LEDGER = OUT_DIR / "fact_ledger.json"
PACK = OUT_DIR / "adjudication_pack.md"
RENDER_DIR = OUT_DIR / "renders"
MANIFEST = ROOT / "data" / "canonical" / "corpus-0b9a1fd84e3d-documents.jsonl"

# auxiliary verification material, keyed by (case, fact_index): the page
# is NOT part of the must-hit bindings -- it is supporting context for the
# human reviewer, rendered from the original PDF with a traceable source
AUXILIARY_MATERIAL = {
    ("G019", 1): {
        "doc_file": "T_CECS_758-2020城镇排水管道混接调查及治理技术规程.pdf",
        "page": 1,
        "what": "封面页：文本层含『T / CECS 7 58-2020』（封面正文起始）——"
                "供人工核验『标准号 758 为文档归属』的佐证链。",
    },
}

# the point to be judged, per fact -- framed from the ledger's recorded
#留痕 and the checker's limitation; never an answer
JUDGMENT_POINTS = {
    ("G011", 0): "氨氮行的『灰水均值 6.1』：机器只能核对数值与行/列表头逐字，"
                 "无法核对『该 6.1 属于氨氮×灰水×均值』的表格位置关联——请对照"
                 "页图确认表位。",
    ("G011", 1): "黑水行『46.8~109.3 / 均值 76.8』：同上，请确认数值属于黑水"
                 "行的范围列与均值列。",
    ("G019", 0): "表5-1『灰水 6.1 / 黑水 76.8』：《征求意见稿》附录表格位置"
                 "关联请对照页图确认。",
    ("G047", 1): "轻度混接(1级)行『d<300 | q<200』：数值在续表 chunk 逐字在场，"
                 "但『量级』为设问措辞——请确认轻级行数值即题目所指。",
    ("G006", 0): "公式结构：LaTeX 公式逐字在 chunk，但『最大日雨量项与过程平均"
                 "日雨量项加权平均』为结构表述的改写——请判断表述与公式是否等价。",
    ("G006", 1): "式中 w/rmax/rd/T0 定义逐字在 chunk；『过程累计日雨量』是否"
                 "即 Σr_d 的结构表述，请判断。",
    ("G012", 0): "标准号『DB 4201/T 651—2021』逐字在封面 chunk；残余二元组"
                 "（『标准号为』）是否确系设问填充词，请判断。",
    ("G013", 0): "步骤名『混错接点判定』『混错接流量测定』逐字在**目次** chunk"
                 "（5.4/5.5 节名）——目次承载能否作为步骤名佐证（抑或应锚定正文节），"
                 "请判断。",
    ("G035", 0): "『Qs = qΨ』与『汇水面积（hm2）』逐字在式中结构；残余"
                 "（『表述为/见式中说明』）是否设问措辞，请判断。",
    ("G046", 2): "『一天内多次测量』：5.4.21 条文有『每日流量应基于一天内多次的"
                 "测量数据』（『→天』为 OCR 一天之误读）与两式 η『每日测量6次～8次』"
                 "——两种方法均有的判断请确认。",
    ("G046", 1): "数值 0.9 在承载 chunk 为扫描 OCR『o. 9 0』（0 误读为 o）——"
                 "请对照页图确认该形态确为 0.9。",
    ("G019", 1): "标准号 758 为文档归属：内容句在正文 chunk（t≈0.9），标准号逐字"
                 "在**封面** chunk（p1『T / CECS 7 58-2020』）——『由证据文档绑定"
                 "佐证标准号』是否成立，请判断。",
    ("G023", 1): "补证据（术语定义 chunk，p5『[QX/T 489－2019,定义2.2]』逐字）"
                 "是否真实承载『并引用 QX/T 489-2019 定义』这一事实，请判断。",
    ("G046", 0): "补证据（5.4.21 式中 chunk，p23）是否真实承载『Q单位m3/d、"
                 "每日测量6~8次、间隔3h~4h』，请判断。",
}


def _pdf_sources() -> dict[str, tuple[str, str]]:
    """doc_file -> (rel_path, sha256) from the canonical manifest."""
    out = {}
    for line in MANIFEST.read_bytes().decode("utf-8").split("\n"):
        if line.strip():
            row = json.loads(line)
            rel = (row.get("source_files") or [{}])[0].get("rel_path") or ""
            path = ROOT / rel
            sha = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""
            out[row["file_name"]] = (rel, sha)
    return out


def _render_auxiliary(sources: dict[str, tuple[str, str]]) -> dict[tuple[str, int], dict]:
    """Render auxiliary pages from the ORIGINAL PDFs (real page images with
    traceable sources).  Writes only into ``renders/``."""
    rendered = {}
    for key, spec in AUXILIARY_MATERIAL.items():
        rel, sha = sources[spec["doc_file"]]
        tag = f"aux-{key[0]}f{key[1]}"
        out = RENDER_DIR / f"{tag}-{spec['doc_file'].split('-')[0]}-p{spec['page']:02d}.png"
        page_text = ""
        if not out.exists():
            pdf = fitz.open(ROOT / rel)
            try:
                page = pdf.load_page(spec["page"] - 1)
                page_text = page.get_text()
                out.parent.mkdir(parents=True, exist_ok=True)
                page.get_pixmap(matrix=fitz.Matrix(2, 2)).save(out)
            finally:
                pdf.close()
        else:
            pdf = fitz.open(ROOT / rel)
            try:
                page_text = pdf.load_page(spec["page"] - 1).get_text()
            finally:
                pdf.close()
        rendered[key] = {
            "render": str(out.relative_to(ROOT)),
            "source_pdf": rel,
            "source_sha256": sha,
            "page": spec["page"],
            "page_text_head": " ".join(page_text.split())[:120],
            "what": spec["what"],
        }
    return rendered


def main() -> None:
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    pending = [e for e in ledger["facts"] if e["human_ruling"] == "pending"]
    pending_keys = {(e["case"], e["fact_index"]) for e in pending}
    if pending_keys != set(JUDGMENT_POINTS):
        raise SystemExit(
            f"pending keys != judgment points: only-in-ledger="
            f"{pending_keys - set(JUDGMENT_POINTS)}, only-in-pack="
            f"{set(JUDGMENT_POINTS) - pending_keys} -- regenerate after the ledger settles"
        )
    renders = {(r["doc"], r["page"]): r["render"] for r in ledger.get("verified_renders") or []}
    # every pending fact must have at least one verified page image to review
    missing_renders = [
        f"{e['case']}[f{e['fact_index']}]"
        for e in pending
        if not any((b["doc"], b["pdf_page"]) in renders for b in e["bindings"])
    ]
    if missing_renders:
        raise SystemExit(f"pending facts without any verified page image: {missing_renders}")
    aux = _render_auxiliary(_pdf_sources())

    lines = [
        "# 人工裁定材料（14 条待裁定事实，2026-09-30）",
        "",
        f"台账：`fact_ledger.json`（dataset sha256 `{ledger['dataset_sha256'][:16]}…`）。",
        "每条事实列出：fact 原文、承载 chunk（实际 chunk_id / 证据索引 / 文档）、",
        "原 PDF 页及已核验页图、机器留痕与**待判断点**。",
        "",
        "**接受/驳回由负责人逐条决定，不得代填。** 标注『辅助核验材料』的页图",
        "只是供人工对照的原始 PDF 页（来源可回查：PDF 相对路径 + sha256 + 页码），",
        "**不是 must-hit 绑定，也不改变黄金集**。",
        "",
        "裁定记录写入 `fact_rulings.json`，逐条字段：",
        "",
        "```json",
        '{"case": "Gxxx", "fact_index": 0, "ruling": "accepted|rejected",',
        ' "adjudicator": "<负责人>", "date": "YYYY-MM-DD",',
        ' "justification": "<理由；驳回时须写明缺陷与处置>",',
        ' "fact": "<与本材料 fact 原文逐字一致>",',
        ' "classification": "<与本材料分类逐字一致>"}',
        "```",
        "",
        "`fact` 原文与 `classification` 必须与本材料逐字一致，否则验收判 conflict。",
        "",
        "## 处理流程（顺序固定）",
        "",
        "1. **逐条裁定并处理驳回项**：接受→记录 accepted；驳回→记录 rejected，",
        "   并按 justification 中的处置（改写 fact / 调整 answerability / 重锚）",
        "   处理后重新对账、重出本材料；",
        "2. **逐题确认 `status=frozen`**；",
        "3. **重生成绑定最终数据集哈希的台账**：",
        "   `python scripts/reconcile_fact_evidence.py --write`（注册表只读合入，",
        "   human_ruling 由重算得出，不得手改台账字符串）；",
        "4. **只读门禁复验**：`_fact_ledger_blockers(dataset, full=True)` /",
        "   `gate_problems(...)` 必须返回空；`--formal` 未经另行授权不得运行。",
        "",
        "## 状态转换规则（权威，重算核对）",
        "",
        "- `refuse_meta`（真拒答 8 条）→ n/a，不入裁定；",
        "- `covered`（机器支持 73 条）→ machine_approved，不入裁定；",
        "- 机器不可证明例外（本表 14 条）→ 有完整 accepted 裁定记录才",
        "  `human_adjudicated`，否则 `pending`（阻塞 formal）。",
        "",
        "---",
        "",
    ]
    for n, e in enumerate(pending, start=1):
        key = (e["case"], e["fact_index"])
        lines.append(f"## {n}. {e['case']}[f{e['fact_index']}] — {e['classification']}")
        lines.append("")
        lines.append(f"- **fact**：{e['fact']}")
        for b in e["bindings"]:
            render = renders.get((b["doc"], b["pdf_page"]), "")
            lines.append(
                f"- **承载（must-hit 绑定）**：证据 ev{b['evidence_index']} · "
                f"`{b['doc']}` · chunk `{b['chunk_id']}` · PDF p{b['pdf_page']}"
                + (f" · 页图 `{render}`" if render else "")
            )
        if key in aux:
            a = aux[key]
            lines.append(
                f"- **辅助核验材料（非 must-hit 绑定，不改变黄金集）**："
                f"封面页图 `{a['render']}`——来源 `{a['source_pdf']}` "
                f"p{a['page']}，PDF sha256 `{a['source_sha256']}`；页文本起始："
                f"『{a['page_text_head']}』。{a['what']}"
            )
        lines.append(f"- **机器留痕**：{e['note']}")
        lines.append(f"- **待判断点**：{JUDGMENT_POINTS[key]}")
        lines.append("")
        lines.append("- 负责人决定：☐ 接受 ☐ 驳回（驳回时请在 justification 中")
        lines.append("  指明缺陷与处置：改写 fact / 调整 answerability / 重锚）")
        lines.append("")
    PACK.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"adjudication pack -> {PACK} ({len(pending)} facts)")
    for key, a in aux.items():
        print(f"auxiliary render {key} -> {a['render']}")


if __name__ == "__main__":
    main()

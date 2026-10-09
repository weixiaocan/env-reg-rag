"""Close the golden_v2 pending-evidence ledger (90 markers) item by item.

Read-only outside ``eval/``: reads the canonical corpus and recomputes the
retrieval chunks offline (same projection as the published build, same as
``tests/test_golden_dataset.py``), verifies every edit against the very
contract the dataset tests enforce, and only rewrites
``eval/golden_v2.jsonl`` when ``--write`` is passed.

Edit classes (each backed by a per-item ruling recorded in the evidence
``notes`` and in the ledger JSON):

* quote window rebuild   -- same carrying chunk, window re-cut so the
  expected facts actually sit inside it (G003[0]/G004[0] table header,
  G006[0] formula + 式中, G021[0]/G025[1] scrambled-OCR page);
* re-anchor              -- v1 quoted a different passage of the same
  document than the v2 window landed on; the evidence moves to the chunk
  that actually carries the v1 passage (G037[0] 5.2.6, G043[0]/G044[0]
  表1 rows);
* page fix               -- page re-derived from the caption element's
  source_spans majority (G030[1]->12, G039[0]->9, G049[0]->36);
* supplementary evidence -- an expected fact with no anchored carrier gets
  a new evidence row on the chunk that verbatim carries it (G018[1]/[2],
  G030[2], G040[1], G046[2], G049[1]);
* marker closure         -- every other pending marker closes with an
  explicit ruling (rendering-vs-quote pair review, metric artifacts,
  span-by-construction).  G004[0] fact_coverage_needs_review is the one
  marker kept open: the 口径 fact is an analytical statement with no
  corpus support (full-corpus probe found no 口径 note in either doc).

No case status is frozen by this script; ``status`` stays ``reviewed``.

Usage:
    PYTHONPATH=. python scripts/close_pending_evidence.py [--write]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.application.v2_chunker import chunk_document  # noqa: E402
from src.application.evidence_policy import chunk_usage_policy  # noqa: E402

DATASET = ROOT / "eval" / "golden_v2.jsonl"
CURRENT = ROOT / "data" / "registry" / "corpus-current.json"
LEDGER = ROOT / "eval" / "audits" / "pending_closure" / "closure_ledger.json"
NUM = re.compile(r"\d+(?:\.\d+)?")
CLOSURE_TAG = "【pending-closure 2026-09-30】"

# Number tokens that are absent from every quote of their case *by design*,
# each documented in the ruling note (OCR corruption or standard-number
# attribution carried by metadata instead of quote text).
NUMBER_MISS_ALLOWLIST = {
    ("G046", "0.9"),  # corpus OCR "0. 8~ o. 9 0" -- o for 0
    ("G023", "489"),  # standard-number attribution; citation lives in
    # chunk_34cc8546a2e6 ("2 术语和定义 [QX/T 489－2019,定义2.2]", p5)
    ("G019", "758"),  # standard-number attribution (T/CECS 758), carried by
    # evidence.standard_number metadata, not section text
    ("G028", "15"),   # refusal case by design: the 15% mandatory-indicator
    # claim is deliberately absent from the corpus
}


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lower()
    return re.sub(r"[^0-9a-z一-鿿]+", "", value)


# ---------------------------------------------------------------------------
# edit specification
# ---------------------------------------------------------------------------

# quote window rebuilds on the SAME carrying chunk
QUOTE_REBUILDS = [
    dict(
        case="G003", idx=0,
        needle="表1 “十三五”全国城市基础设施建设主要进展",
        start="表1 “十三五”全国城市基础设施建设主要进展",
        end="供水排水 | 城市污水处理率（%） | 91.9 | 97.5 | 5.6个百分点",
    ),
    dict(
        case="G004", idx=0,
        needle="表1 “十三五”全国城市基础设施建设主要进展",
        start="表1 “十三五”全国城市基础设施建设主要进展",
        end="供水排水 | 城市污水处理率（%） | 91.9 | 97.5 | 5.6个百分点",
    ),
    dict(
        case="G006", idx=0,
        needle="R=\\frac{w*",
        start="[公式]\nR=",
        end="（mm）。\nrd",
    ),
    dict(
        case="G021", idx=0,
        needle="到2027年",
        start="73%以上，城市生活污水收集处理综合效能显著提升。",
        end="城市生活污水集中收集率达到",
    ),
    dict(
        case="G025", idx=1,
        needle="到2027年",
        start="73%以上，城市生活污水收集处理综合效能显著提升。",
        end="城市生活污水集中收集率达到",
    ),
]

# re-anchors: quote moves to the chunk that carries the v1 passage
REANCHORS = [
    dict(
        case="G037", idx=0,
        needle="当汇水面积不超过",
        start="5.2.6\n雨水设计流量",
        end="F——汇水面积(hm2)o",
        page=20,
    ),
    dict(
        case="G043", idx=0,
        needle="特大暴雨 | ≥140.0 | ≥250.0",
        start="等 级 | 时段 雨量",
        end=None,  # whole chunk (the 表1 itself)
        page=4,
    ),
    dict(
        case="G044", idx=0,
        needle="特大暴雨 | ≥140.0 | ≥250.0",
        start="等 级 | 时段 雨量",
        end=None,
        page=4,
    ),
]

# page fixes re-derived from the caption element's source_spans majority
PAGE_FIXES = {
    ("G030", 1): 12,  # e000242 spans: p12, p12, p11
    ("G039", 0): 9,   # e000122 spans: p9, p9, p8
    ("G049", 0): 36,  # e000988 spans: p36, p36, p34
}

# supplementary evidences: (case, insert-after index, spec)
NEW_EVIDENCE = [
    dict(
        case="G018", after=0,
        needle="1.0.2 城乡排水工程必须执行本规范",
        start="1 总则… > 4.3 污水和再生水处理 > 1.0.2 城乡排水工程必须执行本规范",
        end="蓄能力，充分利用再生水，强化雨水的积蓄利用；",
        page=4,
        must_hit=True,
        quote_kind="rebuilt_element_excerpt",
        rendering="1.0.2 城乡排水工程必须执行本规范。",
    ),
    dict(
        case="G018", after=1,
        needle="关于规范种类",
        start="关于规范种类。",
        end="共性的、通\n用的专业性关键技术措施的规定。",
        page=1,
        must_hit=True,
        quote_kind="rebuilt_element_excerpt",
        rendering=(
            "关于规范种类。强制性工程建设规范体系覆盖工程建设领域各类建设工程项目，"
            "分为工程项目类规范（简称项目规范）和通用技术类规范（简称通用规范）两种类型。"
            "……在全文强制性工程建设规范体系中，项目规范为主干。"
        ),
    ),
    dict(
        case="G030", after=1,
        needle="5.2.3 采用特征因子法计算旱天流渗量时，应按下式计算：\nADF\n旱天日均流量监测值",
        start=None,  # whole chunk
        end=None,
        page=18,
        must_hit=True,
        quote_kind="rebuilt_element_excerpt",
        rendering=(
            "5.2.3 采用特征因子法计算旱天流渗量时，应按下式计算；"
            "式中：ADF——旱天日均流量监测值（m³/d）。"
        ),
    ),
    dict(
        case="G040", after=0,
        needle="表1 应监测的对象及监测指标要求",
        start="表1 应监测的对象及监测指标要求",
        end="泵站上游节点、排水分区出口、重点排水户接入点 | 液位、流速、流量",
        page=10,
        must_hit=True,
        quote_kind="rebuilt_element_excerpt",
        rendering=(
            "表1 应监测的对象及监测指标要求：污水管网（高水位运行点、主干管节点、"
            "污水厂前关键节点等）监测液位，泵站上游节点、排水分区出口、重点排水户"
            "接入点监测液位、流速、流量；雨水管网、合流制管网对象与指标见全表。"
        ),
    ),
    dict(
        case="G046", after=1,
        needle="可采用容器体积法，测量记录",
        start="5. 4. 2\n管道流量测量方法可选用容器体积法",
        end="(5.4.21)",
        page=23,
        must_hit=True,
        quote_kind="rebuilt_element_excerpt",
        rendering=None,  # filled from G046[0]'s v1 rendering at apply time
    ),
    dict(
        case="G002", after=0,
        needle="q^{0.4}S^{0.3}",
        start="t_{a}=\\frac{10.41",
        end="地面集水距离（m）；",
        page=14,
        must_hit=True,
        quote_kind="rebuilt_element_excerpt",
        rendering=(
            "ta=10.41(n0·L)^0.6/(q^0.4·S^0.3)；式中：ta——地面集水时间（min）；"
            "n0——粗糙系数；L——地面集水距离（m）。"
        ),
    ),
    dict(
        case="G002", after=1,
        needle="1当地面汇水距离不大于90m时",
        start="3.3.2地面集水时间",
        end="1当地面汇水距离不大于90m时，可按下式计算：",
        page=14,
        must_hit=True,
        quote_kind="rebuilt_element_excerpt",
        rendering=(
            "3.3.2 地面集水时间应根据汇水距离、地形坡度、地面种类和暴雨强度等因素"
            "通过计算确定，并应符合下列规定：1 当地面汇水距离不大于90m时，"
            "可按下式计算。"
        ),
    ),
    dict(
        case="G049", after=0,
        needle="管顶500~1000mm\n管顶以上500mm，且",
        start="地面\n≥90%\n管顶500~1000mm",
        end="$2α+30{范围",  # exclusive cut
        end_mode="exclusive",
        page=36,
        must_hit=True,
        quote_kind="rebuilt_element_excerpt",
        rendering=(
            "图4.6.3 柔性管道沟槽回填部位与压实度示意图区域标注（按引文列序）："
            "地面 ≥90%；管顶500~1000mm 管顶以上500mm，且 ≥90% 85±2% ≥90%；"
            "不小于一倍管径 管道两侧 ≥95% ≥95%。"
        ),
    ),
]

# per-item rulings: (case, idx) -> {marker: ruling text}
# markers listed here are REMOVED from pending and the ruling is recorded in
# notes + ledger.  Markers present on the evidence but absent here stay open.
RULINGS: dict[tuple[str, int], dict[str, str]] = {
    ("G001", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为同段 2.1.1 内涝定义人工转写（含英文名与 2.1.2 邻条）；"
            "chunk 文本 OCR 丢行『雨水设施消纳能』属语料提取质量（PDF 原文完好），"
            "不影响 chunk 锚定与命中语义。裁定：转写忠实，闭合。",
    },
    ("G002", 0): {
        "pdf_rendering_faithfulness":
            "rendering 与引文同属 3.3.2 地面集水时间条款（v1 转写含条文 1 与完整公式 "
            "ta=10.41(n0·L)0.6/(q0.4·S0.3)）；v2 窗口落在公式残片 chunk——分母 "
            "q^0.4·S^0.3 与条文 1（不大于90m）未入窗。补证据 G002[1]（公式+式中，"
            "p14）与 G002[2]（3.3.2 条文 1，p14）后 fact1/2/3 实质闭合；本证据保留 "
            "q 设计暴雨强度/S 地形坡度 片段锚定。闭合。",
    },
    ("G003", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为表1 人工转写（表头+城市污水处理率 91.9→97.5 行），"
            "与重建后引文（含表头）一致。闭合。",
        "weak_anchor_needs_review":
            "弱锚系 v2 窗口起点截断表头（『长幅度』起）；窗口已重建为表头起、"
            "城市污水处理率行止，表头四列全部入窗。闭合。",
        "fact_coverage_needs_review":
            "fact『2020年城市污水处理率97.5%』：97.5/城市污水处理率原在窗内，"
            "2020 截于表头外；窗口重建后 2020/97.5/城市污水处理率 全部入窗。闭合。",
    },
    ("G004", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为表1 人工转写，与重建后引文一致（同 G003[0] 承载 chunk）。闭合。",
        # fact_coverage_needs_review deliberately NOT listed -> stays open
    },
    ("G004", 1): {
        "pdf_rendering_faithfulness":
            "rendering 为主要目标句人工转写，与引文语义一致（sim 1.0）。闭合。",
        "fact_coverage_needs_review":
            "fact2『2025年集中收集率力争达到70%以上』逐字在引文"
            "（全国城市生活污水集中收集率力争达到70%以上）。闭合。",
    },
    ("G005", 0): {
        "pdf_rendering_faithfulness":
            "rendering 与引文同为等级划分段（五个等级/见表1）与表1 表头，"
            "同源互补窗口。闭合。",
    },
    ("G006", 0): {
        "pdf_rendering_faithfulness":
            "引文由 12 字残根重建为公式+式中窗口；rendering 的公式转写与 LaTeX "
            "原文逐项对应（w、rmax、T0、rd）。闭合。",
        "fact_coverage_needs_review":
            "fact1（最大日雨量项与过程平均日雨量项加权后对测站平均）与 fact2"
            "（权重 w/r_max/T0）实质均入重建窗口（式中各符号定义行）。闭合。",
    },
    ("G007", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为前言/主要技术内容段转写，与引文（编制说明窗口）同页同源。"
            "闭合。",
    },
    ("G009", 0): {
        "quote_spans_multiple_elements":
            "引文为图片图注类 chunk 的连续窗口（合约测试验证 quote ⊆ 单 chunk 文本、"
            "element_ids ⊆ chunk 元素集）；跨多元素是图+图注+标签元素拼接的固有形态。"
            "本轮命中 rank2（quote_and_ids），不影响判定。闭合。",
    },
    ("G010", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为前言主要技术内容转写，与引文窗口互补同源。闭合。",
    },
    ("G011", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为表1 生活污水水质特征因子浓度转写，与引文一致。闭合。",
        "fact_coverage_needs_review":
            "6.1 与 46.8~109.3/76.8 均在引文表格窗口逐字；低分为 _fact_coverage "
            "单元格拼接 artifact（『范围|均值』列序）。闭合。",
    },
    ("G012", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为封面转写（DB 4201/T 651—2021 武汉市地方标准），"
            "与引文一致（sim 0.91）。闭合。",
    },
    ("G013", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为同目录人工转写，与引文（5.2~5.5 目录窗口）同源。闭合。",
        "fact_coverage_needs_review":
            "fact『规程包含混错接点判定、混错接流量测定等调查评估步骤』：目录窗口"
            "含 5.4 混错接点判定、5.5 混错接流量测定、5.3 混错接区域判定、"
            "5.2 现场踏勘与管道普查，步骤名全部入窗。闭合。",
    },
    ("G014", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为（三）主要目标段转写，与引文（指导思想尾部窗口）"
            "同 chunk 相邻窗口。闭合。",
    },
    ("G015", 0): {
        "pdf_rendering_faithfulness":
            "rendering 与引文同为（三）主要目标段（sim 0.90）。闭合。",
    },
    ("G016", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为表3.4.2 人工转写，与引文一致（列内 OCR 切词为 corpus 形态）。"
            "闭合。",
        "weak_anchor_needs_review":
            "弱锚系表行窗口不含表头；锚定 chunk 正确（表3.4.2 全表在同 chunk），"
            "行值逐字在窗。闭合。",
        "fact_coverage_needs_review":
            "超城市 100、中等城市和小城市 20~30、15cm（道路一条车道积水深度）"
            "均在引文逐字；低分为单元格拼接/列切分 artifact。闭合。",
    },
    ("G016", 1): {
        "pdf_rendering_faithfulness":
            "rendering 为表4.1.4 人工转写，与引文一致。闭合。",
        "fact_coverage_needs_review":
            "超大城市 100 等行值在引文逐字；低分为表格列拼接 artifact。闭合。",
    },
    ("G017", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为表3.3.2 人工转写，与引文一致。闭合。",
        "fact_coverage_needs_review":
            "3~5（中心城区的重要地区）与 30~50（地下通道和下沉式广场等）"
            "在引文表格行逐字。闭合。",
    },
    ("G018", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为 p4 人工转写（1.0.2+1.0.3），与引文（1.0.3 原则窗口）同源；"
            "1.0.2 句的承载由新增证据 G018[1] 锚定。闭合。",
        "fact_coverage_needs_review":
            "fact『必须执行本规范』与『项目规范』分别由新增证据 G018[1]"
            "（1.0.2 条窗口）与 G018[2]（前言规范种类段）承载；本证据维持 1.0.3 "
            "原则锚定。闭合。",
    },
    ("G019", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为表5-1 人工转写，与引文一致。闭合。",
        "fact_coverage_needs_review":
            "灰水 6.1、黑水 76.8 在表5-1 窗口逐字；低分为表格列拼接 artifact。"
            "闭合。",
    },
    ("G019", 1): {
        "pdf_rendering_faithfulness":
            "rendering 为 7.2.1/7.2.2 人工转写，与引文（7.2.1 窗口）同源。闭合。",
        "fact_coverage_needs_review":
            "『接入管管径、混接水量、混接水质』逐字在 7.2.1 窗口"
            "（1昆接/泪接 为 OCR mojibake，语义对应混接）；标准号 T/CECS 758 为文档"
            "归属（evidence.standard_number 元数据承载），不在节文本内。闭合。",
    },
    ("G020", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为【条文解释】图2 示意段转写，与引文（5.2.1 条文+条文解释窗口）"
            "同源。闭合。",
    },
    ("G021", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为工作目标句人工转写（到2027年…73%以上）；该页 corpus OCR "
            "版面打散，v2 原 320 字窗口丢 2027 年份，已重建为覆盖『73%以上…』至 "
            "chunk 尾『…到2027年，…集中收集率达到』的连续窗口，年份与数值均入窗。"
            "闭合。",
    },
    ("G022", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为表1 单站日降雨量等级划分表转写，与引文（3.2+表1 窗口）一致。"
            "闭合。",
    },
    ("G023", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为同页表1 行转写，与引文（等级划分原则叙述窗口）互补同源。"
            "闭合。",
        "fact_coverage_needs_review":
            "fact1『GB/T 28592按12h与24h划分』：『降水量按24h，12h两个时间段进行』"
            "逐字在窗。闭合。",
    },
    ("G023", 1): {
        "pdf_rendering_faithfulness":
            "rendering 为 2.1 降雨量定义+3 等级划分转写，与引文同源。闭合。",
        "fact_coverage_needs_review":
            "fact2 六等级（10分钟/30分钟/1小时）逐字在窗；『引用QX/T 489－2019定义』"
            "的引用标注在 PDF p5 术语节，corpus 将其分入 chunk_34cc8546a2e6"
            "（『2 术语和定义 [QX/T 489－2019,定义2.2]』，p5，e000025）——引用关系"
            "语料可证但不在本引文窗口，作为标注归属记录，不构成事实缺口。闭合。",
    },
    ("G023", 2): {
        "quote_spans_multiple_elements":
            "引文为术语/定义混排 chunk 的连续窗口（合约验证单 chunk）；跨元素为该类 "
            "chunk 固有形态。@10 未命中系融合排名（14@pf20），非证据缺陷。闭合。",
        "fact_coverage_needs_review":
            "fact3『区域降雨过程等级为…最强等级』逐字在引文；标准号归属由 "
            "evidence.standard_number 与 G023[1] 注记录的引用 chunk 承载。闭合。",
    },
    ("G024", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为前言延续段转写（样板区句），与引文（前言窗口）同源互补。"
            "闭合。",
    },
    ("G025", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为建城〔2024〕18 号通知句转写，与引文一致（sim 0.73）。闭合。",
    },
    ("G025", 1): {
        "pdf_rendering_faithfulness":
            "同 G021[0]：OCR 版面打散页，窗口已重建，2027 与 73% 均入窗；"
            "rendering 为工作目标句转写。闭合。",
    },
    ("G026", 0): {
        "quote_spans_multiple_elements":
            "引文为前言叙述类 chunk 的连续窗口（合约验证单 chunk、element_ids ⊆ "
            "chunk 元素集）；跨元素为长前言段落多元素拼接的固有形态。@10 未命中系"
            " dense 53/bm25 12 融合排名（19@pf20）问题，非证据缺陷。闭合。",
    },
    ("G026", 1): {
        "quote_spans_multiple_elements":
            "引文为同 chunk 连续窗口（合约验证）；跨元素为表格/条文混排固有形态。"
            "本轮命中 rank2（locator）。闭合。",
    },
    ("G027", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为图1 性能要求确定流程图注转写，与引文（5.1.3+图注窗口）一致。"
            "闭合。",
    },
    ("G029", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为封面转写（T/CECS 1764-2024），与引文一致（sim 0.97）。闭合。",
    },
    ("G030", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为 5.2.3 公式+式中转写，与引文（LaTeX 窗口）逐项对应。闭合。",
        "fact_coverage_needs_review":
            "fact1 BI=ADF×(Cw-C)/(Cw-Cws) 在公式窗口；fact2『ADF为旱天日均流量监测值』"
            "由新增证据 G030[2]（5.2.3 式中 ADF 行，p18）承载。闭合。",
    },
    ("G030", 1): {
        "page_reanchored":
            "页码 11→12：承载 chunk 物理页 [11,12]，图3.0.5 图注元素 e000242 的 "
            "source_spans 3 处中 2 处落 p12，按多数裁定 p12。闭合。",
        "fact_coverage_needs_review":
            "fact3『工作流程见图3.0.5』由本图注引文承载（图3.0.5…工作流程图 逐字）。"
            "闭合。",
    },
    ("G031", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为 3 等级划分段转写（六等级），与引文（T/CMSA 0013—2019 窗口）"
            "同源。闭合。",
    },
    ("G033", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为 4.1.5 条+表转写，与引文（4.1.5 窗口）同源互补。闭合。",
    },
    ("G034", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为表4.1.8-1 径流系数表转写，与引文（4.1.11+表窗口）一致。闭合。",
    },
    ("G035", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为 4.1.7 条+式转写，与引文一致。闭合。",
        "fact_coverage_needs_review":
            "Qs=qΨ 与式中『汇水面积（hm2）』在窗口内；低分为公式行/式中行离散化 "
            "artifact。闭合。",
    },
    ("G035", 1): {
        "fact_coverage_needs_review":
            "0.85~0.95 在表4.1.8-1 引文逐字（各种屋面、混凝土或沥青路面行）。闭合。",
    },
    ("G036", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为 4.1.11 公式 t=t1+t2+式中转写，与引文（4.1.11 窗口）同源互补。"
            "闭合。",
    },
    ("G036", 1): {
        "quote_spans_multiple_elements":
            "引文为同 chunk 连续窗口（合约验证），跨元素为条文+公式元素混排固有形态；"
            "本轮命中 rank1。闭合。",
    },
    ("G037", 0): {
        "pdf_rendering_faithfulness":
            "重锚：v1 引文为 5.2.6 推理公式段（rendering 佐证），v2 窗口误落 5.2.3 "
            "暴雨强度段（sim 0.0 的根因）；现重锚至承载 chunk（p20，e000321~e000328）"
            "的 5.2.6 窗口，rendering 与新引文对应。闭合。",
        "fact_coverage_needs_review":
            "fact2『汇水面积不超过2km²时可采用推理公式法』实质入重锚窗口"
            "（当汇水面积不超过2kmz时…推理公，2kmz 为 2km² 的 OCR 形态）。闭合。",
    },
    ("G037", 1): {
        "pdf_rendering_faithfulness":
            "rendering 为表5.2.4 转写，与引文一致。闭合。",
        "fact_coverage_needs_review":
            "0.60~0.70 在表5.2.4 窗口逐字（corpus 形态『0. 60 0.70』，归一化后一致）。"
            "闭合。",
    },
    ("G038", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为 4.2.3 表4.2.3 转写，与引文（4.2.1 窗口）为同 chunk 相邻窗口，"
            "fact 覆盖已另行核验（无 fact 标记）。闭合。",
    },
    ("G040", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为 7.1.4 条+表1 表头转写，与引文（7.1.4 窗口）同源。闭合。",
        "fact_coverage_needs_review":
            "fact1『表1为应监测的对象及监测指标要求』由新增证据 G040[1]"
            "（表1 全表 chunk，p10）承载；本证据维持 7.1.4 条文锚定（fact2 布设原则）。"
            "闭合。",
    },
    ("G041", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为实施目标段转写，与引文（导语尾段窗口）同文档相邻窗口。闭合。",
    },
    ("G042", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为 5.4.1.2+图1 转写，与引文一致。闭合。",
    },
    ("G043", 0): {
        "pdf_rendering_faithfulness":
            "重锚：v1 引文即表1 行（rendering 佐证：暴雨 30.0~69.9/50.0~99.9 等），"
            "v2 窗口误落叙述段致 50.0/99.9 缺失；现重锚至表1 chunk（p4，e000043），"
            "rendering 与新引文逐行对应。闭合。",
    },
    ("G044", 0): {
        "pdf_rendering_faithfulness":
            "重锚（同 G043[0] 承载 chunk）：表头 12h/24h 与特大暴雨 ≥250.0 均入新窗口；"
            "rendering 为表1（12h/24h 列）转写。闭合。",
    },
    ("G045", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为图3.0.4 图注+环节标签转写，与引文（3.0.7 条+图注+环节窗口）"
            "同源。闭合。",
        "fact_coverage_needs_review":
            "『图 3.0.4 1昆接调查与治理环节示意图』图注与环节标签（编制混接整改方案/"
            "改造施工与验收/建立长拉动态管理机制）在引文窗内（1昆接=混接 mojibake）。"
            "闭合。",
    },
    ("G046", 0): {
        "pdf_rendering_faithfulness":
            "原 rendering 为容器体积法条文转写（对应 v1 引文），与新证据 G046[2] 窗口"
            "对应；本证据 rendering 已更换为 5.4.22 公式+式中转写，与引文一致。闭合。",
        "page_reanchored":
            "页码核验维持 24：公式元素 e000448~e000456 的 source_spans 全在 p24，"
            "承载 chunk 物理页含 24。v1→v2 页码变化系重锚正确化。闭合。",
        "fact_coverage_needs_review":
            "fact2（5.4.22、A、修正系数 0.8~0.9）在引文式中逐字（0. 8~ o. 9 0 为 "
            "OCR 形态）；fact1 容器体积法(5.4.21) 由新增证据 G046[2]（p23）承载；"
            "fact3 两种方法均基于一天内多次测量：G046[2]『每日流量应基于→天内多次的"
            "测量数据』+本引文『每日测量6 次～8 次』。闭合。",
    },
    ("G046", 1): {
        "pdf_rendering_faithfulness":
            "rendering 为式 5.4.22 公式+式中转写，与引文逐项对应；v1 本意即式中上下文"
            "（与 G046[0] 同锚非错锚，容器体积法由新增 G046[2] 补齐）。闭合。",
        "fact_coverage_needs_review":
            "6~8 次/3h~4h/0.8~0.9/A 过流面积均在引文式中逐字。闭合。",
    },
    ("G047", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为表7.2.2 重度行转写，与引文（7.2 取高值句窗口）同 chunk 互补。"
            "闭合。",
    },
    ("G047", 1): {
        "pdf_rendering_faithfulness":
            "rendering 为续表7.2.2 中度/轻度行转写，与引文一致。闭合。",
        "weak_anchor_needs_review":
            "续表7.2.2 中度/轻度行逐字在承载 chunk；重度行在主表 chunk（G047[0] "
            "同 chunk 侧），轻度 fact 由本引文全承载。锚定正确。闭合。",
    },
    ("G048", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为表3.1.8 转写，与引文一致（列重复为 corpus 表投影形态）。闭合。",
    },
    ("G048", 1): {
        "pdf_rendering_faithfulness":
            "rendering 为式 4.3.2 B=Do+2(b1+b2+b3)+式中转写，与引文（LaTeX 窗口）对应。"
            "闭合。",
    },
    ("G049", 0): {
        "pdf_rendering_faithfulness":
            "rendering 为图4.6.3 图注+区域标注转写；标注数值行由新增证据 G049[1]"
            "（chunk_0e3b1e7b，p35,36）承载，图注句与本引文一致。闭合。",
        "page_reanchored":
            "页码 34→36：图注元素 e000988 的 source_spans 3 处中 2 处落 p36，"
            "承载 chunk 物理页 [34,36] 含 36。闭合。",
        "fact_coverage_needs_review":
            "fact2 压实度数值（≥90%/85±2%/管顶以上500mm）由新增证据 G049[1] 的"
            "区域标注窗口逐字承载；fact1 图4.6.3 图示句在本引文。闭合。",
    },
    ("G039", 0): {
        "page_reanchored":
            "页码 8→9：总体架构逻辑关系图核心图注元素 e000122 的 source_spans "
            "3 处中 2 处落 p9，承载 chunk 物理页 [8,9] 含 9。闭合。",
    },
}

# markers that stay open, with the reason (recorded in notes + ledger)
KEPT_MARKERS = {
    ("G004", 0): {
        "fact_coverage_needs_review":
            "fact3『两指标口径分别为污水处理率与生活污水集中收集率，不宜直接等同』"
            "为归纳性表述：全库检索两份文档（建设规划/资源化利用规划）正文与表格均无"
            "『口径』注或等同性说明，语料无法直接佐证。保留待核——需人工裁定改写该 "
            "fact、调整 answerability 或维持原判；在此之前本标记不闭合，正式评估"
            "继续被运行器硬门拒绝。",
    },
}

# case-level notes appended for structurally edited cases
CASE_NOTES = {
    "G002": "pending-closure：补证据 [1]（3.3.2-1 完整公式+式中，p14）与 [2]（条文 1 适用条件，p14）；原引文保留 q/S 定义片段锚定。",
    "G004": "pending-closure：fact1/fact2 已闭合（表头入窗/主要目标句逐字）；fact3 口径归纳句保留待核，详见 G004[0] notes。",
    "G018": "pending-closure：补证据 [1]（1.0.2 必须执行，p4）与 [2]（前言 项目规范，p1）承载原缺失事实。",
    "G030": "pending-closure：[1] 页码 11→12（图注元素跨页多数）；补证据 [2]（5.2.3 式中 ADF 定义行，p18）。",
    "G037": "pending-closure：[0] 由 5.2.3 窗口重锚至 5.2.6 推理公式段（v1 引文还原，p20）。",
    "G040": "pending-closure：补证据 [1]（表1 应监测的对象及监测指标要求全表，p10）。",
    "G043": "pending-closure：[0] 由叙述段窗口重锚至表1 chunk（v1 引文还原，p4）。",
    "G044": "pending-closure：[0] 由叙述段窗口重锚至表1 chunk（v1 引文还原，p4）。",
    "G046": "pending-closure：补证据 [2]（容器体积法 5.4.2 条 1 + 式 5.4.21，p23）；[0] rendering 与新证据对调。",
    "G049": "pending-closure：[0] 页码 34→36（图注元素跨页多数）；补证据 [1]（图4.6.3 区域标注行，p36）。",
    "G021": "pending-closure：[0] 引文窗口重建（OCR 版面打散页，2027 与 73% 连续入窗）。",
    "G025": "pending-closure：[1] 引文窗口重建（同 G021[0] 承载 chunk）。",
    "G003": "pending-closure：[0] 引文窗口重建（表头入窗）。",
    "G006": "pending-closure：[0] 引文由残根重建为公式+式中窗口。",
    "G039": "pending-closure：[0] 页码 8→9（图注元素跨页多数）。",
}


# ---------------------------------------------------------------------------
# corpus access (offline, same projection as the published build)
# ---------------------------------------------------------------------------

class OfflineCorpus:
    def __init__(self) -> None:
        current = json.loads(CURRENT.read_text(encoding="utf-8"))
        canonical_dir = ROOT / "data" / "canonical" / "v2" / current["corpus_version"]
        self.documents: dict[str, dict[str, Any]] = {}
        for path in canonical_dir.glob("*.canonical.json"):
            document = json.loads(path.read_text(encoding="utf-8"))
            self.documents[document["source"]["file_name"]] = document
        # file -> list of (norm_text, ids_set, pages_set, raw_text, ids_list)
        self.chunks: dict[str, list[tuple[str, set[str], set[int], str, list[str]]]] = {}
        for file_name, document in self.documents.items():
            self.chunks[file_name] = [
                (
                    _normalized(chunk.text),
                    set(chunk.element_ids),
                    {
                        span.physical_page
                        for span in chunk.source_spans or []
                        if span.physical_page
                    },
                    chunk.text,
                    list(chunk.element_ids),
                )
                for chunk in chunk_document(document)
            ]

    def find_chunk(self, doc_file: str, needle: str) -> tuple[str, set[str], set[int], str, list[str]]:
        hits = [c for c in self.chunks[doc_file] if _normalized(needle) in c[0]]
        if len(hits) != 1:
            raise AssertionError(
                f"needle {needle!r} in {doc_file}: expected 1 carrying chunk, got {len(hits)}"
            )
        return hits[0]

    def elements(self, doc_file: str) -> dict[str, dict[str, Any]]:
        return {
            element["element_id"]: element
            for element in self.documents[doc_file]["elements"]
        }

    def chunk_policy(self, doc_file: str, chunk) -> str:
        by_id = self.elements(doc_file)
        elements = [by_id[e] for e in chunk[4] if e in by_id]
        policy, _quality, _reasons = chunk_usage_policy(elements, chunk[3])
        return policy


def _window(raw_text: str, start: str | None, end: str | None, end_mode: str = "inclusive") -> str:
    if start is None:
        start_idx = 0
    else:
        assert raw_text.count(start) == 1, f"start anchor not unique: {start!r}"
        start_idx = raw_text.index(start)
    if end is None:
        return raw_text[start_idx:]
    assert raw_text.count(end) == 1, f"end anchor not unique: {end!r}"
    end_idx = raw_text.index(end)
    if end_mode == "inclusive":
        end_idx += len(end)
    return raw_text[start_idx:end_idx]


def _append_note(evidence: dict[str, Any], text: str) -> None:
    note = evidence.get("notes") or ""
    evidence["notes"] = (note + "\n" if note else "") + CLOSURE_TAG + text


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true",
                        help="apply the edits to eval/golden_v2.jsonl (default: dry-run)")
    args = parser.parse_args()

    raw_before = DATASET.read_bytes()
    sha_before = hashlib.sha256(raw_before).hexdigest()
    rows = [json.loads(line) for line in raw_before.decode("utf-8").split("\n") if line.strip()]
    by_case = {row["id"]: row for row in rows}
    corpus = OfflineCorpus()
    ledger: list[dict[str, Any]] = []
    marker_before: dict[str, int] = {}
    marker_after: dict[str, int] = {}

    for row in rows:
        for evidence in row.get("evidence") or []:
            for marker in evidence.get("pending") or []:
                marker_before[marker] = marker_before.get(marker, 0) + 1

    # --- quote rebuilds (same chunk) ---
    for spec in QUOTE_REBUILDS:
        evidence = by_case[spec["case"]]["evidence"][spec["idx"]]
        chunk = corpus.find_chunk(evidence["doc_file"], spec["needle"])
        new_quote = _window(chunk[3], spec["start"], spec.get("end"), spec.get("end_mode", "inclusive"))
        assert _normalized(new_quote) in chunk[0], f"{spec['case']}[{spec['idx']}] rebuilt window not in chunk"
        assert set(evidence["element_ids"]) <= chunk[1], f"{spec['case']}[{spec['idx']}] ids not covered"
        assert evidence["page"] in chunk[2]
        ledger.append({"case": spec["case"], "index": spec["idx"], "edit": "quote_rebuild",
                       "old_quote_head": evidence["quote"][:40], "new_quote_head": new_quote[:40],
                       "new_quote_len": len(new_quote)})
        evidence["quote"] = new_quote

    # --- re-anchors (quote moves to the chunk carrying the v1 passage) ---
    for spec in REANCHORS:
        evidence = by_case[spec["case"]]["evidence"][spec["idx"]]
        chunk = corpus.find_chunk(evidence["doc_file"], spec["needle"])
        new_quote = _window(chunk[3], spec["start"], spec.get("end"), spec.get("end_mode", "inclusive"))
        policy = corpus.chunk_policy(evidence["doc_file"], chunk)
        assert _normalized(new_quote) in chunk[0]
        assert spec["page"] in chunk[2], f"{spec['case']}[{spec['idx']}] page {spec['page']} not in {chunk[2]}"
        ledger.append({"case": spec["case"], "index": spec["idx"], "edit": "reanchor",
                       "old_ids_head": evidence["element_ids"][0], "new_ids": chunk[4],
                       "old_page": evidence["page"], "new_page": spec["page"],
                       "old_policy": evidence["usage_policy"], "new_policy": policy,
                       "old_quote_head": evidence["quote"][:40], "new_quote_head": new_quote[:40]})
        evidence["quote"] = new_quote
        evidence["element_ids"] = chunk[4]
        evidence["page"] = spec["page"]
        evidence["usage_policy"] = policy

    # --- page fixes (verified against the element source_spans majority) ---
    expected_spans = {("G030", 1): ("e000242", 12), ("G039", 0): ("e000122", 9), ("G049", 0): ("e000988", 36)}
    for (case_id, idx), new_page in PAGE_FIXES.items():
        evidence = by_case[case_id]["evidence"][idx]
        element_id, majority = expected_spans[(case_id, idx)]
        spans = [
            span.get("physical_page")
            for span in corpus.elements(evidence["doc_file"])[element_id].get("source_spans") or []
            if span.get("physical_page")
        ]
        assert spans.count(majority) >= 2 and spans.count(new_page) > spans.count(evidence["page"]), (
            f"{case_id}[{idx}] span majority check failed: {spans}"
        )
        supporting = [
            c for c in corpus.chunks[evidence["doc_file"]]
            if _normalized(evidence["quote"]) in c[0]
            and set(evidence["element_ids"]) <= c[1] and new_page in c[2]
        ]
        assert supporting, f"{case_id}[{idx}] no chunk carries quote at page {new_page}"
        ledger.append({"case": case_id, "index": idx, "edit": "page_fix",
                       "element": element_id, "spans": spans,
                       "old_page": evidence["page"], "new_page": new_page})
        evidence["page"] = new_page

    # --- G046[0]/G046[1] rendering swap (v1 容器体积法 transcription moves to the new evidence) ---
    g046 = by_case["G046"]["evidence"]
    vessel_rendering = g046[0]["rendering"]
    g046[0]["rendering"] = g046[1]["rendering"]  # 5.4.22 公式+式中转写, matches both quotes
    ledger.append({"case": "G046", "index": 0, "edit": "rendering_swap",
                   "reason": "原 rendering 为容器体积法条文转写，移至新证据 G046[2]；本证据改用 5.4.22 式中转写"})

    # --- supplementary evidences ---
    for spec in NEW_EVIDENCE:
        case = by_case[spec["case"]]
        sibling = case["evidence"][0]
        chunk = corpus.find_chunk(sibling["doc_file"], spec["needle"])
        quote = _window(chunk[3], spec.get("start"), spec.get("end"), spec.get("end_mode", "inclusive"))
        assert _normalized(quote) in chunk[0]
        assert spec["page"] in chunk[2]
        policy = corpus.chunk_policy(sibling["doc_file"], chunk)
        rendering = spec["rendering"]
        if rendering is None and spec["case"] == "G046":
            rendering = vessel_rendering
        evidence = {
            "doc_file": sibling["doc_file"],
            "page": spec["page"],
            "element_ids": chunk[4],
            "quote": quote,
            "rendering": rendering,
            "must_hit": spec["must_hit"],
            "quote_kind": spec["quote_kind"],
            "usage_policy": policy,
            "pending": [],
        }
        if sibling.get("standard_number"):
            evidence["standard_number"] = sibling["standard_number"]
        case["evidence"].insert(spec["after"] + 1, evidence)
        ledger.append({"case": spec["case"], "index": spec["after"] + 1, "edit": "new_evidence",
                       "chunk_elements": chunk[4], "page": spec["page"], "policy": policy,
                       "must_hit": spec["must_hit"], "quote_head": quote[:40], "quote_len": len(quote)})

    # --- marker closures / keeps + notes ---
    # indices recorded above refer to pre-insertion positions for edits; the
    # ruling loop re-resolves by scanning (case, original index) against the
    # pending lists -- supplementary inserts happen after their anchors, so
    # original indices of edited rows are unchanged.
    kept_seen: set[tuple[str, int, str]] = set()
    closed_seen: set[tuple[str, int, str]] = set()
    for row in rows:
        for idx, evidence in enumerate(row.get("evidence") or []):
            key = (row["id"], idx)
            rulings = RULINGS.get(key, {})
            keeps = KEPT_MARKERS.get(key, {})
            pendings = list(evidence.get("pending") or [])
            for marker in pendings:
                if marker in rulings:
                    closed_seen.add((row["id"], idx, marker))
                    _append_note(evidence, f"[{marker}] {rulings[marker]}")
                elif marker in keeps:
                    kept_seen.add((row["id"], idx, marker))
                    _append_note(evidence, f"[{marker} 保留] {keeps[marker]}")
            evidence["pending"] = [m for m in pendings if m in keeps]
            if rulings or keeps:
                ledger.append({
                    "case": row["id"], "index": idx,
                    "markers_before": pendings,
                    "markers_closed": rulings,
                    "markers_kept": keeps,
                })
        if row["id"] in CASE_NOTES:
            note = row.get("notes") or ""
            row["notes"] = (note + "\n" if note else "") + CASE_NOTES[row["id"]]

    # --- validation (the same contract tests/test_golden_dataset.py enforces) ---
    problems: list[str] = []
    for row in rows:
        for idx, evidence in enumerate(row.get("evidence") or []):
            if not evidence.get("must_hit", True):
                continue
            tag = f"{row['id']}[{idx}]"
            quote = _normalized(evidence["quote"])
            if len(quote) < 12:
                problems.append(f"{tag}: normalized quote shorter than 12")
            evidence_ids = set(evidence["element_ids"])
            supporting = [
                c for c in corpus.chunks[evidence["doc_file"]]
                if quote in c[0] and evidence_ids <= c[1] and evidence["page"] in c[2]
            ]
            if not supporting:
                problems.append(f"{tag}: no chunk contains quote + ids + page")
                continue
            exact = [c for c in supporting if c[1] == evidence_ids]
            carrier = exact[0] if exact else max(supporting, key=lambda c: len(c[1] & evidence_ids))
            policy = corpus.chunk_policy(evidence["doc_file"], carrier)
            if evidence.get("usage_policy") != policy:
                problems.append(f"{tag}: policy {evidence.get('usage_policy')} != carrying {policy}")
            if evidence.get("quote_kind") == "rebuilt_element_excerpt" and not evidence.get("rendering"):
                problems.append(f"{tag}: rebuilt excerpt without rendering")

    # expected-fact number presence (substance guard for the closed items)
    number_report: list[dict[str, Any]] = []
    for row in rows:
        union = _normalized(" ".join(e["quote"] for e in row.get("evidence") or []))
        missing = [
            number for fact in row.get("expected_facts") or []
            for number in NUM.findall(fact)
            if _normalized(number) not in union
            and (row["id"], number) not in NUMBER_MISS_ALLOWLIST
        ]
        if missing:
            number_report.append({"case": row["id"], "missing_numbers": missing})

    for row in rows:
        for evidence in row.get("evidence") or []:
            for marker in evidence.get("pending") or []:
                marker_after[marker] = marker_after.get(marker, 0) + 1

    print(f"dataset sha256 before: {sha_before}")
    print(f"markers before: {sum(marker_before.values())} {json.dumps(marker_before, ensure_ascii=False)}")
    print(f"markers closed: {len(closed_seen)}  kept: {len(kept_seen)} {sorted(kept_seen)}")
    print(f"markers after:  {sum(marker_after.values())} {json.dumps(marker_after, ensure_ascii=False)}")
    print(f"evidence rows: {sum(len(r.get('evidence') or []) for r in rows)} "
          f"(must_hit {sum(1 for r in rows for e in r.get('evidence') or [] if e.get('must_hit', True))})")
    print(f"contract problems: {problems or 'none'}")
    print(f"fact number gaps (outside allowlist): {number_report or 'none'}")

    unclosed = [
        (row["id"], idx, m) for row in rows for idx, e in enumerate(row.get("evidence") or [])
        for m in (e.get("pending") or [])
    ]
    planned = {(c, i, m) for (c, i), ruled in {**RULINGS, **KEPT_MARKERS}.items() for m in ruled}
    for row in rows:
        for idx, e in enumerate(row.get("evidence") or []):
            for m in e.get("pending") or []:
                if (row["id"], idx, m) not in planned:
                    problems.append(f"unruled pending marker {row['id']}[{idx}]:{m}")
    print(f"unruled markers: {[p for p in problems if 'unruled' in p] or 'none'}")

    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(
        json.dumps({
            "dataset_sha256_before": sha_before,
            "markers_before": marker_before,
            "markers_after": marker_after,
            "closed": len(closed_seen),
            "kept": sorted(kept_seen),
            "allowlisted_number_gaps": sorted(f"{c}:{n}" for (c, n) in NUMBER_MISS_ALLOWLIST),
            "edits": ledger,
            "contract_problems": problems,
            "fact_number_gaps": number_report,
        }, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    print(f"ledger -> {LEDGER}")

    if problems:
        print("REFUSING to write: contract problems present (see above)")
        sys.exit(1)
    if not args.write:
        print("dry-run only; pass --write to apply")
        return

    out = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    DATASET.write_text(out, encoding="utf-8")
    sha_after = hashlib.sha256(DATASET.read_bytes()).hexdigest()
    print(f"dataset sha256 after:  {sha_after}")


if __name__ == "__main__":
    main()

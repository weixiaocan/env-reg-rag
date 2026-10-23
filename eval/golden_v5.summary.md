# golden_v5 变更说明

## 2026-10-23：重新对齐到 corpus-913c134c18ae

`corpus-913c134c18ae` 发布：239 张表格逐格核对后全部可引用，扫描件识别的投票规则修正（行内单位符号、含行内公式的正文行不再丢失）。用同一脚本把证据锚点重新对齐到 913c：

- 76/79 条对齐（上次 73/79），未对齐 3 条：G013 第 3 页、G049 第 35 页（必中，原因同下文）、G050（拒答题的非必中证据）；
- 证据使用策略：`answer_and_citation` 65 条、`source_locator_only` 14 条（表格可引用后，多数证据变为可正式引用）；
- 对齐前，G002、G046 的 4 条引文已改为更正后的原文（旧引文保留在 `quote_before_20261023`）。

产物 SHA-256 `75375e4ef1eaf13c8daab37248f3355db56f0fae952a42bf175bee4f0894de89`，逐条记录见 `eval/golden_v5.reanchor_report.json`。以下为 2026-10-20 首次生成 v5 时的说明。

---

日期：2026-10-20。来源：`eval/golden_v4.jsonl`（SHA-256 `dcfbe780…8c9c23f`）。
产物：`eval/golden_v5.jsonl`（SHA-256 `530f7b4af3f8f2df3218b05a81b9609d3edc245d2e11dcadd0f93164b9330e10`），锚定 `corpus-9097cecb12d5`。

## 为什么改

2026-10-20 发布了 `corpus-9097cecb12d5`：在 7411 的基础上应用负责人逐条核对过的 35 个公式更正和 2 处式中文字更正，并把这些公式标为已核对（`data/registry/source-corrections.json`）。运行器要求黄金集的 `anchor_corpus` 等于当前发布版本，所以证据锚点必须对齐到新语料。

v4 的锚点本来就已过时：v3 迁到 7411 时只改了 `anchor_corpus`，76 条必中证据里 64 条的引文在当时的分块里已经找不到。这次一并重新对齐。

## 改了什么

只改了每条证据的 `quote`、`page`、`element_ids`、`usage_policy`、`quote_kind`、`pending` 和 `anchor_corpus`。问题、`expected_facts`、`labels`、`doc_coverage`、`expected_answerability`、`status` 和题目级 `notes` 与 v4 相同。

对齐方法见 `scripts/reanchor_golden.py`：

- 用证据的干净原文 `rendering`（没有时用旧引文）在同文档、同页（找不到再扩到前后一页）的分块里找匹配；
- 公共片段（6 个字以上的连续串）要覆盖较短一方的 60% 以上，并且总长度足够，在合格的分块里取公共部分最长的；
- 新 `quote` 是该分块里承载证据的连续行，逐字摘自新语料，并用检索评估的命中判定 `_quote_matches` 校验过。

自动规则判不了的 11 条由人工指定分块，理由见 `eval/golden_v5.reanchor_manual.json`，对应证据的 `notes` 里有【manual reanchor】标记。逐条匹配记录见 `eval/golden_v5.reanchor_report.json`。

| 结果 | 条数 |
| --- | --- |
| 自动对齐 | 62 |
| 人工指定分块 | 11 |
| 未对齐（`reanchor_unresolved`） | 6 |

未对齐的 6 条：

- G008、G050：拒答题的非必中证据，库内本来就没有对应原文；
- G012、G029：答案在封面上；G013：答案在目录页上。封面和目录按设计不进入索引，检索评估无法命中，待人工决定是否改换证据；
- G049 第 2 条：图4.6.3 图内的压实度标注（≥90%、85±2%），图内文字没有进入索引。

证据使用策略：`answer_and_citation` 44 条、`source_locator_only` 35 条（v4 记录的是旧语料的策略，分别为 35 和 44）。

涉及本次更正页面的题目只有 G034–G036（GB 50014 第 22 页），它们的期望事实不含被更正的公式，无需修改。

## 状态

- 仍为 `reviewed`，6 条证据带 `reanchor_unresolved`，只能做诊断运行，不能走 `--formal`。
- 预期口径沿用 v4（`answer` 46、`correct_premise` 2、`refuse` 2）。
- 以后每次发布新语料，用同一脚本重新对齐：`scripts/reanchor_golden.py --source <上一版> --output <新版> --manual <人工指定文件>`。

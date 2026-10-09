# S1 可回答性差距对账 — 一页根因汇总（DIAGNOSTIC，只读审计）

> 语料 `corpus-0b9a1fd84e3d` / 黄金集 sha `3b1f1cdc459a…` / 基线 `diagnostic-diag-baseline-20261001`；实测于发布 collection（只读）。

## 生产 answer 路径（代码实测口径）

`src/server/runtime.py`：answer profile = RRF 检索 + **Qdrant 内过滤** `usage_policy=answer_and_citation` + limit 10；**空结果 ⇒ REFUSED@retrieval**；生成上下文 = 过滤后 top-10 的**前 5 条**（generate_context_limit=5）。source_lookup profile 恒 SEARCH_ONLY（skip_generation=True，代码推断）。

## 结果速览（50 题）

- **检索级可回答（目标证据 carrier 为 answer_and_citation 且入 answer top-5）**：11 题 — G009、G013、G021、G023、G025、G031、G032、G037、G038、G040、G046；其余 39 题在检索级不可回答；
- answer-space 检索**空结果 0 题**——没有一题因检索层直接 REFUSED；
- 4 道拒答题（G008/G015/G028/G050）answer profile 均返回非空候选：**拒答完全依赖生成层判断**，端到端未验证（G5/G6）。

## 根因分布（按题目主根因，可多根因并存）

| 根因 | 题数 | 题目 |
| --- | --- | --- |
| 证据准入不足 | 41 | G001、G002、G003、G004、G005、G006、G007、G010、G011、G012、G014、G016、G017、G018、G019、G020、G022、G023、G024、G025、G026、G027、G029、G030、G032、G033、G034、G035、G036、G037、G039、G040、G041、G042、G043、G044、G045、G046、G047、G048、G049 |
| 尚待生成验证 | 9 | G008、G009、G013、G015、G021、G028、G031、G038、G050 |
| 原文不支持 | 4 | G008、G015、G028、G050 |
| 检索漏召回 | 3 | G019、G037、G047 |

## 根因解读与最小修复顺序

1. **证据准入不足（41 题，最大根因）**：目标证据的承载 chunk 均为 `source_locator_only`——answer-space 过滤把它们整体排除，answer profile 只能取到其他无关内容。这一根因**不是检索排序问题**（unfiltered top-10 中这些 chunk 大多在位），而是结构化内容（表格/公式/图）默认 locator-only（D3）+ 质量状态未 passed 的准入面问题。最小修复：**按 D3 升级路径逐文档人工复核 structured 元素并绑定 review_assertion**（准入升级，须另立任务+审核），而非动检索。
2. **检索漏召回（3 题：G019/G037/G047）**：存在 answerable carrier，但 answer-space top-10 未取到（RRF 在过滤子空间内排序把稀薄 answer chunk 排出）。与 E1 结论一致：candidate 池小 + 无 rerank；最小修复：E2（prefetch 复核）/policy 感知 rerank（审后另立）。
3. **上下文截断（并入根因 2 观测）**：carrier 在 answer top-10 但 >5（本轮 0 题单因截断）。
4. **尚待生成验证（9 题计入，全部 11+4 题的生成层均未端到端验证）**：检索级就绪不代表生成层能产出带出处回答——G5/G6 缺口，需 LLM 授权另行申请。**这是 46 题目标与现状之间唯一无法在本轮实测的环节**。
5. **原文不支持（4 题设计拒答）**：无修复需求；拒答正确性待生成层验证。
6. **提取缺失或失真（0 题）**：fact_ledger gap_categories 全空（missing/pdf_only/rendering_only 均 0）——第 3 轮事实对账后提取面无已知缺口。

## 基线 trace 一致性（@K 计数口径）

- live 复算与基线 trace 的 answer-chunk 计数失配 1 题（G013）——均为 @10 边界翻转、翻转 chunk 不触及证据 carrier（已知 RRF tie-break 现象），**real drift 0 条**；
- 承载 chunk 内容核验（169 carrier，只读实测）：**全部在 collection 在位**；引文逐字保留 167/169，其余 2 条（G035[1]）为 rendering 式表格窗口 vs live chunk '|' 单元分隔的口径差，**全部数值逐字在位**（fact 级保留成立），非内容丢失；
- 逐题 trace（match_rank / citable_match_rank）照录 baseline run，未重算。

## 约束遵守

只读审计：黄金集/质量状态/索引/指针/trace 零改动；未调用外部 LLM；未运行 formal。
产物仅 `eval/audits/s1_answerability/`（s1_per_case.jsonl 50 行 + s1_summary.json + 本文件）。
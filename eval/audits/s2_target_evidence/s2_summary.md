# S2 目标证据准备 — 一页汇总（DIAGNOSTIC，只读）

> 语料 `corpus-0b9a1fd84e3d` / 黄金集 sha `3b1f1cdc459a…`；全部实测于发布 collection（只读）；正式索引/指针/黄金集/trace 零改动。

## 完整可达（修复前后）

- **S1 口径（任一 evidence 承载可达）**：11/46 题；
- **S2 口径（生成 top-5 内来源正确、足以支持整条 fact 的 answer chunk）**：完整可达 **4** / 部分可达 3 / 不可达 39（46 内容题）；
- fact 级：9/87 内容 fact 可达（强 9 = 引文/事实文本逐字在 answer chunk；仅数字在位=弱线索 6799 处，不计可达）；
- 变化来源：**判定口径收紧（红灯：来源正确+整条 fact 逐字支持；数字巧合/跨文档重复不计）**——原 13 题中 G039（跨文档图注数字巧合）、G012（仅数字在位）降为不可达；非任何制品变更；
- 4 拒答题（G008/G015/G028/G050）按拒答目标单列，不计入。

## 统一 trace 口径（证据身份对账，替代计数比对）

- live 复算与基线 trace 同用 evaluate_case 匹配器，每条 evidence 的身份（matched/citable 的 chunk_id+rank）直接对账（`s2_per_case.jsonl` 每题 `trace_identity_reconciliation`）；计数不再是比对基础；
- 身份不一致 0 题，分三级：**boundary_rank_swap**（失配 chunk 两侧都在 top-10，并列换位）与 **boundary_tie_flip**（失配 chunk 在 @10 截断线翻进/翻出、分数等于两侧截断分，成员翻转）均为 RRF tie-break 噪声，非实质；**material**（截断分之上的在位变化或可引用性变化）为实质；
- 触及证据绑定 carrier 的**实质失配 0 条**（判据：mismatch_kind=material 且失配 chunk 触及绑定 carrier）；

## 排名 miss 逐题实测（prefetch 20 vs 100，已保存）

- 受阻清单中检索排名 miss 逐题实测：answer 空间 RRF 在 prefetch 20 与 100 两档下的目标 carrier 名次 + dense/bm25 双腿在 answer 空间内的排名，逐题参数与排名保存于 `s2_rank_probe.json`（2 条）；

## 受阻证据根因（分组）

| 根因 | 条目 | 处置 |
| --- | --- | --- |
| 普通文字质量(OCR待审) | 16 | 人工页图复核（短清单 A 组） |
| 普通文字质量(too_short等) | 50 | 人工复核（短清单 A 组） |
| 结构化D3复核 | 68 | D3 review_assertion 人工路径（短清单 A 组） |
| 检索排名 | 19 | 重排/权重，本轮排除（见下） |
| 旧台账 pending | 14 条 rulings（31 binding） | 人审暂停中，短清单 B 组 |

## PDF 回查（不把台账当内容证明）

- 回查 119 条（pending rulings + supported=False 绑定，引文为主探针、fact 数值为辅、±1 页窗口），**每条绑定源文件 SHA256**（`source_sha256`）：
  - PDF_VERIFIED 15；
  - PDF_WEAK(仅数值在位) 25；
  - PDF_TEXT_LAYER_EMPTY(扫描件) 43——文字层回查不可行，仅页图可核；
  - PDF_NOT_ON_PAGE 36——待人工页图复核（`s2_pdf_recheck.json` 逐条含 PDF 路径+页码+源文件 SHA）。

## 工程修复评估（实测，非假设）

1. **准入面**：62 个 blocked locator chunk 的 465 个源 element：needs_review 285 / passed 180（passed 全在结构化/受限 chunk 上，按 D3 结构化默认准入）——全库 **0 个 review assertion**；needs_review 须质量复核、passed 须 D3 升级路径，工程不可代填；
2. **检索排名**：逐题实测已保存（`s2_rank_probe.json`，prefetch 20/100 两档参数+双腿排名）；2 条目标——深 prefetch 不修复的结论由实测数据支撑（与 E2 受控实验一致）；唯一有效手段是重排/权重，本轮明确排除；
3. **结论：本轮工程可修复集合为空**——完整可达数变化全部来自判定口径修正；未产任何候选（无可修根因）、未动正式索引/指针/黄金集/trace、未调 LLM、未进生成或重排。

## 剩余人工依赖

- 受阻证据人工复核：184 条（短清单 `s2_human_review_list.md`，含 PDF 页码）；
- 14 条 pending rulings（人审暂停中）+ PDF 回查中 NOT_ON_PAGE 的页图复核；
- 生成层端到端验证（G5/G6，需 LLM 授权）——完整可达≠生成层可答。
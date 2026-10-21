# golden_v4 变更说明

日期：2026-10-19。来源：`eval/golden_v3.jsonl`（SHA-256 `c5b30aca…d11d2`）。
产物：`eval/golden_v4.jsonl`（SHA-256 `dcfbe7803726f969701b500792a6ff24437deeb40dbfa051a94a5b74b8c9c23f`），按 `eval/schemas/golden_case_v3.schema.json` 校验，50 条全部通过。

## 为什么改

`expected_answerability` 在 v3 及以前描述的是**当时的证据准入**：`locate` 表示目标证据是 `source_locator_only`，在当时的规则下只能定位原文，不能回答。此后产品口径变了：

- 2026-10-12 起，机器识别、未经人工核对的证据可以支持回答，对应结论标“未核对”；
- 2026-10-19 起，网页只保留一种提问方式，问题都由模型基于证据回答；统计时段等未说明的条件不再由规则追问，而是由模型分情况说明；
- 模型改为写整段回答并在句内标注来源，对“资料里没有所问数值”的问题，可以依据资料纠正前提，而不是一律返回空回答。

因此该字段改为描述**期望的系统行为**，证据策略仍由每条 `evidence[].usage_policy` 记录。

## 改了什么

只改了 `expected_answerability` 与 `notes`。问题、证据锚点（`element_ids`、页码、引文）、`expected_facts`、`labels`、`anchor_corpus`（`corpus-7411b3a05a07`）和 `status` 均与 v3 相同。

| 变化 | 条数 | 用例 |
| --- | --- | --- |
| `locate` → `answer` | 40 | v3 中全部 `locate` 用例 |
| `refuse` → `correct_premise` | 2 | G015（2035 年目标只有定性表述）、G028（不存在“年度更新改造率 15%”的规定） |
| 不变：`answer` | 6 | G009、G013、G021、G031、G038、G040 |
| 不变：`refuse` | 2 | G008（美国 EPA 手册）、G050（欧盟指令），均为库外 |

结果：`answer` 46、`correct_premise` 2、`refuse` 2。

## 新取值的判定

| 取值 | 可接受的结果 | 说明 |
| --- | --- | --- |
| `answer` | `answered` / `conflicting_sources` | 事实覆盖与引用支持另行统计；引用机器识别证据的结论带“未核对”标记 |
| `correct_premise` | `refused`，或 `answered` 且不给出所问的虚构数值 | 回答需人工语义复核；运行器单列 `needs_semantic_review` |
| `refuse` | `refused` | 库外问题 |

运行器：`scripts/run_generation_diag.py` 与 `scripts/run_e2e_diag.py` 默认数据集已改为 `golden_v4.jsonl`；`locate` 仍可读取，用于复现 v3 及以前的历史运行。

## 状态

- 黄金集仍为 `reviewed`，6 条证据带 `reanchor_unresolved` 待核标记，只能做诊断运行，不能走 `--formal`。
- **证据锚点未随语料重新对齐**：v3 迁到 `corpus-7411b3a05a07` 时只改了 `anchor_corpus` 字段，`evidence` 里的 `quote`、`element_ids`、`page` 仍是旧语料的识别文本和编号。2026-10-19 用当前语料重建分块核对，76 条必中证据中 64 条的原文在当前分块里找不到。生成和端到端诊断按 `expected_facts` 与引用判分，不受影响；若要重做检索命中评估，需先把证据锚点重新对齐到当前语料。`tests/test_golden_dataset.py` 因此只保留结构检查。
- 预期口径由负责人于 2026-10-19 确认；50 题诊断评估待本轮功能测试完成后统一重跑，v3 的历史结果不与 v4 直接比较。

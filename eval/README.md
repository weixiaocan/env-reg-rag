# 评估目录入口

旧版检索及真实模型端到端诊断总报告见 `FINAL_EVAL_REPORT.md`（2026-10-24，corpus-ed28，golden_v5）。新版 `corpus-b6e4850ef830` 的原始诊断见 `audits/diagnostic-e2e-20261027-b6-review.md`；2026-10-29 的修订与新运行见 `golden_v7.summary.md` 和 `runs/diagnostic-e2e-20261029-b6-v7/`。18 条证据锚点仍未核定，均非正式质量验收。

- 设计：`EVAL_SYSTEM_DESIGN_v0.md`
- 检索 Harness：`RETRIEVAL_HARNESS.md`
- 出题指南：`GOLDEN_SET_GUIDE.md`
- **旧版已核对锚点：`golden_v5.jsonl`**（50 条，`status=reviewed`，锚定 `corpus-ed28791afb8a`；`expected_answerability` 为 `answer` 46、`correct_premise` 2、`refuse` 2）。**当前诊断草稿：`golden_v7.jsonl`**，锚定 `corpus-b6e4850ef830`；50 题、80 条证据中仍有 18 条 `reanchor_unresolved`。v6 的 79 条/19 条状态及自动重锚报告保留为历史记录。两版均只供诊断，待核锚点不能当作人工通过。
- v7 修订说明：`golden_v7.summary.md`；v6 自动重锚与待核分型：`golden_v6.summary.md`、`audits/golden_v6_anchor_review.md`；v5 变更说明：`golden_v5.summary.md`（证据锚点 2026-10-24 重新对齐到 ed28，脚本 `scripts/reanchor_golden.py`）；Schema：`schemas/golden_case_v3.schema.json`
- 运行器：`scripts/run_generation_diag.py`（生成层）、`scripts/run_e2e_diag.py`（HTTP 端到端）、`scripts/evaluate_golden_retrieval.py`（检索），默认数据集为与当前指针对齐的诊断草稿 `golden_v7.jsonl`；旧版实验须显式传 `--dataset eval/golden_v5.jsonl` 并绑定旧语料。`--preflight-only` 只做离线绑定检查，不调用模型。
- 存档：`golden_v4.jsonl`（锚定 7411，锚点已过时）；以下不与 v4/v5 结果直接比较：
  - `golden_v3.jsonl`（`expected_answerability` 为当时的证据准入口径：`answer`/`locate`/`refuse`），`schemas/golden_case_v2.schema.json`
- `runs/`：历次诊断运行；v7 原始索引检索结果见 `runs/diagnostic-retrieval-20261029-b6-v7/`（必中证据 @5 为 53/77、@10 为 61/77）。它不经过新检索适配器，不能代表实际 HTTP 查询排序。v7 HTTP 运行目录为 `runs/diagnostic-e2e-20261029-b6-v7/`；v6 历史结果见 `runs/diagnostic-b6e4850ef830-v6/` 和 `runs/diagnostic-e2e-20261027-b6-r2/`。首次 v6 E2E 尝试遇 Qdrant 503，不计入完成结果。`runs/retrieval-baseline-v1/` 仅为 provisional 开发快照，不是最终基线。

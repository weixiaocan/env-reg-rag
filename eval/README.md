# 评估目录入口

完整交接见仓库根目录 [AGENTS.md](../AGENTS.md) **第 10 节**。

- 设计：`EVAL_SYSTEM_DESIGN_v0.md`
- 框架与冻结门禁：`FRAMEWORK.md`
- 检索 Harness：`RETRIEVAL_HARNESS.md`
- 出题指南：`GOLDEN_SET_GUIDE.md`
- 覆盖配额：`golden_coverage_v1.md`
- **现行数据集：`golden_v2.jsonl`**（50 条，`status=reviewed`，重锚定到
  `corpus-0b9a1fd84e3d`；policy-aware，诊断专用直至待核闭合）
- v2 摘要与待核清单：`golden_v2.summary.md`；逐条对账：`audits/step1_reconciliation.json`
- Schema：`schemas/golden_case_v2.schema.json`（v2 现行）；`schemas/golden_case_v1.schema.json`（v1 存档）
- 旧版（仅存档，锚定旧语料 `corpus-069284e6d60e`，不得在新语料上计分）：
  `golden_v1.jsonl`、`golden_v1.draft.jsonl`、`golden_v1.summary.md`
- `runs/retrieval-baseline-v1/`：仅为 provisional 开发快照，不是最终基线
- 审题板：**未建**（计划 `review_board/`）

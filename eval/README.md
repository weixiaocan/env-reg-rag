# 评估目录入口

最近一次评估总报告见 `FINAL_EVAL_REPORT.md`（基于 golden_v3 与 2026-10-15 的生成方式）。

- 设计：`EVAL_SYSTEM_DESIGN_v0.md`
- 检索 Harness：`RETRIEVAL_HARNESS.md`
- 出题指南：`GOLDEN_SET_GUIDE.md`
- **现行数据集：`golden_v5.jsonl`**（50 条，`status=reviewed`，锚定 `corpus-913c134c18ae`；`expected_answerability` 描述期望行为：`answer` 46、`correct_premise` 2、`refuse` 2；诊断专用直至待核闭合）
- v5 变更说明：`golden_v5.summary.md`（证据锚点 2026-10-23 重新对齐到 913c，脚本 `scripts/reanchor_golden.py`）；v4 变更说明：`golden_v4.summary.md`；Schema：`schemas/golden_case_v3.schema.json`
- 运行器：`scripts/run_generation_diag.py`（生成层）、`scripts/run_e2e_diag.py`（HTTP 端到端），默认数据集均为 `golden_v5.jsonl`；`--preflight-only` 只做离线绑定检查，不调用模型
- 存档：`golden_v4.jsonl`（锚定 7411，锚点已过时）；以下不与 v4/v5 结果直接比较：
  - `golden_v3.jsonl`（`expected_answerability` 为当时的证据准入口径：`answer`/`locate`/`refuse`），`schemas/golden_case_v2.schema.json`
- `runs/`：历次诊断运行；`runs/retrieval-baseline-v1/` 仅为 provisional 开发快照，不是最终基线

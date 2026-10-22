# Claude Code：项目约定

先读 `AGENTS.md`，再读 `.claude/EVALUATION_HANDOFF.md`（当前状态与待办）。历史报告里的数字不自动代表当前状态；以正式指针 `data/registry/corpus-current.json`、当前黄金集 `eval/golden_v5.jsonl`、运行配置和实际代码为准。保留工作区已有改动。

需要负责人明确授权的操作（不得从任务推定）：

- 外部 LLM 调用、向第三方发送 PDF 或检索片段（需指明提供方和发送范围）；
- 下载新模型；
- ingest、publish、切换正式指针，或改变质量裁定（`quality_status`、`usage_policy`、人工核对记录）；
- 提交、推送、部署服务器、发布 GitHub Release；
- 删除语料版本、Qdrant collection、原始 PDF 或人工复核记录。

工作方式：按"问题 → 证据 → 根因 → 最小方案 → 测试"推进；改代码后跑相关测试，跨层改动跑完整 pytest。每轮给负责人简短状态：完成了什么、正在做什么、唯一阻断是什么；详细日志写到产物里。黄金集未 frozen 时只能运行和报告 diagnostic，不能绕过 `--formal` 门禁。

评估里程碑完成后，可调用只读审核子 agent `rag-eval-reviewer` 独立审核；收到 REJECT 后按根因集中修复并复审，不因非阻断优化无限返工。

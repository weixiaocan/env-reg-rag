# golden_v6 本地诊断交接（2026-10-26）

`golden_v6.jsonl` 从 `golden_v5.jsonl` 复制问题和预期事实，使用 `scripts/reanchor_golden.py` 对齐本地语料 `corpus-b6e4850ef830`。它是诊断草稿，不是经人工逐项核对的新黄金集。原 v5 保留并继续锚定 `corpus-ed28791afb8a`。

## 锚点状态

- 共 50 题、79 条证据；60 条自动对齐，19 条留下 `reanchor_unresolved`。逐条结果见 `golden_v6.reanchor_report.json`。
- 待核题号：G002、G006、G013、G021、G030、G033、G036、G037、G045、G046、G047、G049、G050。其中 G050 的待核证据不是必中证据。
- 不得把自动对齐解释为原页人工复核，也不得将未对齐证据强行改成 `passed` 或从评估分母删除。

## 本地运行证据

本地及公网 Qdrant `corpus_v2_live` 已指向该版本，point 数与指针均为 4263。公网 `/api/v1/ready` 返回 ready；HTTP `source_lookup` 返回新版证据。公式（6）、（8）的独立 evidence 详情分别返回物理页 12、13 和原页 bbox，策略为 `answer_and_citation`，对应公网原 PDF 页图均返回 200。

诊断检索运行目录：`runs/diagnostic-b6e4850ef830-v6/`。50 题中有 47 题存在必中证据，76 条必中证据在前 5 条命中 50 条、前 10 条命中 58 条。旧版 ed28/v5 报告对应 54/76、60/76；由于本次锚点有 19 条待核，两组数字不能严格比较。排查优先级：G002 有 1 条公式证据未对齐；G023 已对齐的目标文档从旧版前 10 条第 4 位消失，属于需核查的检索坏例。

`audits/diagnostic-b6e4850ef830-v6-preflight.json` 只证明 50 题锚点版本与发布指针一致。随后真实智谱 GLM-5.2 HTTP 诊断已在 `runs/diagnostic-e2e-20261027-b6-r2/` 完成：45 回答、5 拒答，估算费用 1.688844 元。G001、G009、G023、G049 等坏例及待核锚点见 `audits/diagnostic-e2e-20261027-b6-review.md` 和 `audits/golden_v6_anchor_review.md`，不能宣称内容质量通过。

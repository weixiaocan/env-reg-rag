# 50 题 RAG 三层诊断综合报告

状态：三层 diagnostic 已完成；formal 门禁仍关闭。本文结果不是人工作为金标准的验收，也不代表法规内容或效力已获权威核验。

## 1. 固定身份与分母

- 当前正式指针：`corpus-0b9a1fd84e3d`
- 当前 Qdrant collection：`corpus_v2__staging__corpus-0b9a1fd84e3d__bi-d70cf457e0cac3fa3abbce0a`
- 指针 chunk 数：4232
- `golden_v2.jsonl` SHA-256：`3b1f1cdc459aadb50bd1b39531bc90f51ecc25a0d969612d41612fa011bd6e89`
- 题目：50；产品目标分母为 46 个内容题 + 4 个拒答题
- 检索分母：47 个 eligible case、77 条 must-hit evidence
- 修正后 S2 `s2_per_case.jsonl` SHA-256：`ec4701942d01efd02eeaa6288d9b4c70a110bf55dbe31ab47d77ffaf7056f69f`

没有重建、清理或发布语料，也没有切换正式指针。工作区既有修改、未跟踪文件和已暂存删除均保留。

## 2. 已实测结果

### 2.1 检索基线（复核既有产物，未重跑）

来源：`eval/runs/diagnostic-diag-baseline-20261001/`。

| 指标 | 结果 |
| --- | ---: |
| evidence micro Recall@5 | 54/77 = 0.701299 |
| evidence micro Recall@10 | 63/77 = 0.818182 |
| macro Recall@5 | 0.721631 |
| gold-evidence MRR | 0.684245 |
| document hit@5 / @10 | 0.957447 / 0.994681 |
| complete-case rate@5 / @10 | 0.595745 / 0.765957 |
| latency p50 / p95 | 61.84 / 128.441 ms |

这些数字衡量检索命中与排序，不等同于 46 个内容题能够生成完整答案。

### 2.2 E1 重排（复核既有 r1/r2，未重跑模型）

修复了比较脚本把同一 7 条救回重复计数、遗漏 miss→miss 的问题。r1/r2 与 control 的比较产物均重新由既有 trace 生成，并经独立只读复审。

| 指标 | baseline | E1 r2 |
| --- | ---: | ---: |
| micro Recall@5 | 0.701299 | 0.740260 |
| micro Recall@10 | 0.818182 | 0.870130 |
| citable Recall@10 | 0.785714 | 0.642857 |
| citable downgraded@10 | 1 | 2 |
| rerank latency p50 / p95 | — | 3265.32 / 4361.41 ms |

77 条 guardrail 转移完整闭合：14 变差、8 改善、7 从未命中变为命中、48 不变。净效应门槛为 `14 <= 7`，不通过；新增 citable 降级和延迟门槛也不通过。结论：E1 是一次 diagnostic 实验，不采纳到正式在线路径。

### 2.3 S2 答案证据准备度（本次重算，本地只读）

旧 S2 把 `supported=false` carrier 计入事实可达，导致 11 个内容题的乐观结果。本次修正为：每个 expected fact 必须有 `bindings[].supported=true`、`supports_fact=true`、`answer_and_citation`、实际存在且保存了精确正文的 carrier；检索旁路层不使用 retrieval rank 作为资格条件。

| 46 个内容题 | 数量 | case |
| --- | ---: | --- |
| 完整可达 | 4 | G009、G021、G031、G038 |
| 部分可达 | 3 | G025、G037、G040 |
| 不可达 | 39 | 见 `s2_per_case.jsonl` |

事实级分母 87，严格可达事实 9。主要阻断来自质量准入和结构化 D3 人工复核；全库当前没有可由工程代填的 review assertion。本轮没有伪造 `passed` 或提升 locator-only 权限。

### 2.4 已有 GLM 50 题运行的正确解释与复审

来源：`eval/runs/diagnostic-gen-20261007/`。这是**生产检索 → 应用生成链路**，不是给定金证据的独立生成，也没有经过 HTTP 入口。

- 状态：15 answered，35 refused；4 个拒答题全部正确拒答。
- 15 个已回答内容题经 agent 语义复核：7 full、6 partial、2 incorrect。
- 31/46 内容题没有答案。
- 15 个回答的引用 ID 均来自当次准入上下文，确定性绑定问题为 0。
- 典型错误：G014 改变全国目标的主体和条件；G019 把 6.1/76.8 mg/L 答成 6.0/27.3 mg/L；G023、G025、G026、G037、G040、G047 为事实覆盖不完整。
- generation latency：p50 5841.8 ms，p95 38004.26 ms，mean 10189.07 ms，n=50。
- total latency：p50 5910.51 ms，p95 38099.24 ms，mean 10249.0 ms，n=50。
- 用量：input 141861 tokens，output 36381 tokens。原运行没有已核实费率，因此不报告金额。

语义审核由 50 份 case 文件聚合哈希 `9c8cc092ec762636eef3ffbac6b25ccfc0a7de1d51183637eae7c09a523cea75` 绑定。输出质量与因果归因分开：当前只有 4 个内容题具备完整、可引用的金载体并在该运行中成功；其余 42 个内容题因金证据准备仍不完整，因果归因记为 unresolved，不能把坏答案或拒答武断归因给生成器。

## 3. 新完成的两层真实诊断

### 3.1 给定金证据生成

来源：`eval/runs/diagnostic-goldgen-20261008-r1/`。runner 为 `scripts/run_gold_generation_diag.py`；检索完全旁路，locator-only 不会被提升。运行绑定 dataset、pointer 和 S2 hash：

- 全部 50 题保留在分母；
- 实际调用模型 8 题：4 个完整内容题 G009/G021/G031/G038 + 4 个拒答题 G008/G015/G028/G050；
- 其余 42 题明确记录 `evidence_preparation_insufficient`，不冒充生成评估成功。

实测结果：4/4 可运行内容题完整正确且引用合规；4/4 拒答题正确拒答；42 个内容题因证据准备不足未调用、未验证。不能表述成“50 题生成质量全部通过”。生成延迟 n=8：p50 3827.89 ms，p95 5669.49 ms，mean 3842.72 ms。用量 7344 input + 1601 output = 8945 tokens；按[智谱官方 API 定价页](https://docs.bigmodel.cn/cn/guide/start/pricing)的 GLM-5.2 输入 8 元/百万 Tokens、输出 28 元/百万 Tokens 计算，估算 0.10358 元。

### 3.2 HTTP 入口端到端

来源：`eval/runs/diagnostic-e2e-20261008-r1/`。runner 为 `scripts/run_e2e_diag.py`，入口是 in-process ASGI 的 `POST /api/v1/queries`，使用生产组合根。已修复原测试的两项失败：HTTP 500 不再被 TestClient 重抛为 transport error；未合并 trace 的记录从 `trace_missing=true` 起步。

50 题全部完成并逐题保存 HTTP 状态、实际送入生成提示的精确 Top-5 证据正文、答案/引用、阶段耗时、token、费用与失败类别；14 个 answered 响应另保存 Top-10 返回证据，36 个 refused 响应按 HTTP 契约返回空证据列表。50 条 trace 全部合并，无 HTTP/transport 失败。

- 状态：14 answered、36 refused；4 个拒答题全部正确，32 个内容题在生成阶段拒答。
- 14 个回答的 agent 语义复核：6 full、5 partial、3 incorrect；32/46 内容题无答案。
- 确定性引用绑定问题：0。
- 新增坏例 G042：把“流量试验装置示意图及水流量标准装置+排水管道”误答成“速度—面积流量测定法示意图及探头+计算机”。G014、G019 仍为错误；G023、G025、G026、G037、G040 为部分回答。
- 总耗时：p50 6731.90 ms，p95 26292.50 ms，mean 10463.22 ms，n=50。
- 检索阶段：p50 66.49 ms，p95 109.68 ms；生成阶段：p50 6660.44 ms，p95 26239.35 ms。
- 用量：141708 input + 31562 output = 173270 tokens；估算 2.0174 元。

“generation_refusal=32”是观测到的阶段，不自动等于生成模型根因。按修正后的 S2，只有 4 个内容题具备完整金证据并成功；其余 42 个内容题的因果归因仍受金证据准备缺口限制，记为 unresolved，不把检索、准入和生成责任混为一谈。

## 4. 外部调用闸门

两轮真实运行已按人类授权精确匹配 provider、model、base URL、发送范围、说明、人民币预算和单次 reserve；输入/输出费率采用智谱官方 API 定价页的 8/28 元每百万 Tokens。实际合计估算费用为 2.12098 元，低于批准的 10 元保护额度。

单次 reserve 是调用前的本地保护，不是提供方侧硬消费上限；若单次实际费用超过 reserve，只能在响应后检测并停止后续题。因此最终授权必须接受这一边界，或先另行实现固定输入/输出 token 上限的保守硬封顶。

已授权且实际执行的发送范围：

- 金证据层：8 题的问题，以及 4 个内容题的已保存、可引用精确 chunk；4 个拒答题发送空证据。
- HTTP 层：50 个问题，以及生产检索实际送入生成的 Top-5 chunk。

## 5. 测试与独立复审

- 最终相关回归：44 项通过；覆盖外部调用闸门、HTTP runner、给金 runner、S2、E1 比较与 E1 实验 runner。
- 最终全量 pytest 跑至 100%，仅 1 项失败：`tests/test_repository_layout.py` 发现工作区既有未跟踪文件 `docs/HOSTING_OPTIONS_2026-10-07.md` 不在文档白名单。该文件与本评估改动无关，按“保留既有改动”要求未删除、未改测试掩盖。
- E1、生成/S2、HTTP runner 三个里程碑均经独立只读 agent 审核；阻断问题修复后复审通过。

## 6. 复现命令

```powershell
# 相关离线回归
.venv\Scripts\python.exe -m pytest tests\test_external_run_guard.py tests\test_e2e_diag_runner.py tests\test_gold_generation_diag_runner.py tests\test_s2_target_evidence.py tests\test_e1_compare.py tests\test_e1_experiment_runner.py -q

# 全量测试
.venv\Scripts\python.exe -m pytest -q

# 只重算既有 E1 比较，不重跑检索
.venv\Scripts\python.exe scripts\e1_compare.py eval\runs\diagnostic-exp-e1-control-20261006-r1 --write
.venv\Scripts\python.exe scripts\e1_compare.py eval\runs\diagnostic-exp-e1-rerank-20261006-r1 --write
.venv\Scripts\python.exe scripts\e1_compare.py eval\runs\diagnostic-exp-e1-rerank-20261006-r2 --write

# 本地只读 S2 与两个无模型预检
.venv\Scripts\python.exe scripts\s2_target_evidence.py
.venv\Scripts\python.exe scripts\run_gold_generation_diag.py --run-id diagnostic-goldgen-20261008 --preflight-only
.venv\Scripts\python.exe scripts\run_e2e_diag.py --run-id diagnostic-e2e-20261008 --preflight-only

# 复核既有 GLM 运行，不调用模型
.venv\Scripts\python.exe scripts\audit_generation_diag.py --output eval\audits\diagnostic_gen_20261007_audit.json

# 复核 HTTP 真实运行，不调用模型
.venv\Scripts\python.exe scripts\audit_e2e_diag.py
```

## 7. Diagnostic 与 formal 边界

- 已实测：既有检索基线、E1、修正后的 S2、2026-10-07 生产检索→生成运行、给定金证据 8 次真实调用、HTTP 50 题真实端到端、延迟、token 和按官方费率计算的估算费用。
- 尚未验证：42 个证据准备不足题的独立给金生成质量；formal 结果；独立人工裁定后的正式分数；账户账单的最终实际扣款。
- Diagnostic：本文全部指标和 agent 语义复核。
- Formal：未执行且当前不允许宣称；完成规范仍为 Draft，golden 尚未 frozen，人工裁定项未闭合。

综合结论：本轮要求的三层 **diagnostic** 已完成，且检索、给金、HTTP 三层的输入与分母已分开记录；但目标 46 个内容题当前只有 4 题具备完整可引用金证据。HTTP 层仅 6/46 内容题得到完整答案，5/46 部分、3/46 错误、32/46 无答案；4/4 拒答正确。主要产品差距是证据准备与准入，而不是可以通过改写报告消除的统计问题。formal 仍未完成，不得宣称正式黄金集验收通过。

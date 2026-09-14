# E06A 查询可观测性、Token 与成本记录

## 1. 目的

M6 需要回答的不只是“接口有没有返回”，还要能解释一次请求慢在哪里、使用了哪个模型和语料版本、消耗了多少 token、回答引用了哪些证据。为此，网页、直接应用调用和后续 MCP adapter 共用 `QueryApplicationService` 内的一条查询 trace，而不是各自重复记录。

这是一套项目内可复现的基础观测能力，不依赖 LangSmith 等外部平台。后续若接入第三方可观测平台，它应作为额外 adapter，不能取代本地评估证据。

## 2. Trace 契约

每次查询最多形成一条记录，包含：

- 请求 ID、调用方、状态、开始和结束时间；
- 条件解析、检索、生成、引用校验和总耗时；
- 语料版本、Qdrant collection alias、检索配置、embedding 和提示词版本；
- 返回的 evidence ID 以及 claim 到 evidence 的绑定关系；
- provider、model、输入 token、输出 token、总 token 和成本估算状态；
- 异常类型，不记录 API key。

生产组合使用追加写入的 `data/observability/query-traces.jsonl`。该文件包含原始用户问题，已加入 `.gitignore`，不能直接提交到公开仓库。评估报告只保留必要的、可公开的代表请求结果。

## 3. 成本口径

模型提供 token usage 时如实记录。只有同时配置并核实 `LLM_INPUT_COST_PER_MILLION` 与 `LLM_OUTPUT_COST_PER_MILLION` 后，系统才按输入/输出分别估算成本；否则成本为 `null`，状态为 `not_configured`。

这样设计是因为模型价格可能变化，也可能因套餐和缓存策略不同而变化。代码不硬编码一个容易过期的价格，面试或报告中也不能把未核实费率计算成“真实费用”。

## 4. 真实链路结果

2026-09-07 使用智谱 `glm-5.2`、正式语料 `formal-corpus-v1` 和 `qdrant-bge-small-zh-bm25-rrf+numeric` 执行一条真实降雨等级问题：

| 项目 | 结果 |
| --- | ---: |
| 查询状态 | `answered` |
| 条件解析 | 0.018 ms |
| 检索 | 92.272 ms |
| 模型生成 | 5673.653 ms |
| 引用校验 | 0.012 ms |
| 总耗时 | 5766.047 ms |
| 输入 / 输出 / 总 token | 1958 / 207 / 2165 |
| 成本 | 未配置已核实费率，`null` |

本次结果表明耗时主要来自模型生成而不是 Qdrant 检索；这只是单请求诊断证据，不能据此声明 P95 或吞吐能力。完整数据见 `data/eval_results/m6-query-trace-smoke-v1.json`。

## 5. 故障边界

trace recorder 是应用服务的外部 adapter。若本地日志目录不可写，服务记录错误日志，但不改变本来成功的问答结果；该行为已有契约测试。相反，模型、检索或引用校验本身失败时，trace 会记录 `error_type`，原业务错误仍按原契约返回。

## 6. 当前结论与限制

- 分阶段耗时、运行版本、证据绑定和 token 已在真实 HTTP 链路中验证；
- 成本估算公式及配置完整性已有单元测试，但当前没有配置已核实价格，因此不报告金额；
- JSONL 适用于单机 POC 和离线评估，不等于生产日志基础设施；
- 不少于 30 个代表请求的 p50、p95、最大值和错误率仍是下一项 M6 工作。

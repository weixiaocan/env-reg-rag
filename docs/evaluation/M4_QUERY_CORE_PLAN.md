# M4 查询核心与 LangChain 实施计划

## 1. 所在位置

M3 已回答“能否找到可回查证据”，M4 回答“系统何时可以基于这些证据作答，以及何时必须追问、只返回原文入口、展示冲突或拒答”。本阶段不实现网页样式和 MCP 协议映射。

## 2. 公共 seam

沿用已批准的单一公共接口：

```python
class QueryApplicationService:
    async def execute(self, request: QueryRequest) -> AnswerResult: ...
```

调用方只需理解 `QueryRequest`、`AnswerResult` 及其业务不变量。条件识别、Qdrant RRF、适用性分组、EvidencePack、模型草稿和引用校验都隐藏在模块内部。后续 FastAPI、网页和 MCP 只做无损映射，不另写查询逻辑。

## 3. 五条纵向切片

1. **需要澄清**：问题缺少会改变结论的统计时段，返回 `needs_clarification` 和结构化 `missing_conditions`，不生成假定答案。
2. **仅搜索**：命中 `source_locator_only`，返回文档、条款和页码入口，不把不可靠公式转写当答案。
3. **有证据回答**：证据充分时生成 claim，每个 claim 必须绑定当前 EvidencePack 中允许引用的 evidence ID。
4. **证据不足或越界**：正式语料没有对应地域文件，或用户要求系统直接作专业合规裁决时，返回受控 `refused`。
5. **来源冲突**：多份适用文件要求不一致时，返回 `conflicting_sources` 并逐份展示，不替用户裁决。

冲突不是“检索到多份文档”的同义词。只有在已解析地域、时间、对象和文件层级后，至少两个不同文档版本仍给出不能同时成立的要求，才进入 `conflicting_sources`。模型只提出结构化冲突候选；查询核心必须验证全部 evidence ID 可引用、来自不同文档版本，并且每个冲突来源都有独立回答主张，才能发布该状态。

指定地方地域时，检索候选同时允许该地方与全国文件进入，地方证据在 EvidencePack 中优先、全国证据作为补充。地域、文种、效力状态、发布机关和日期随证据返回；普通地域或层级差异不得被模型标为来源冲突。

每条切片按红灯测试、最小实现、回归验证推进，不先批量创建所有内部类。

## 4. LangChain 边界

LangChain 用于模型结构化输出、局部 Runnable 编排、重试和可关闭的 tracing。领域状态、EvidencePack、引用不变量、语料适用性和 Qdrant 发布不交给 LangChain 管理。模型原始输出必须先转换成项目领域对象并通过确定性校验，才能成为 `AnswerResult`。

首个生产模型适配器使用 `langchain-openai` 连接智谱的 OpenAI-compatible Chat Completions。当前只依赖标准消息内容和 JSON mode：模型输出先经 Pydantic 校验，再由查询核心执行 evidence ID 白名单和 `usage_policy` 校验。OpenAI、DeepSeek 等后续供应商继续复用 `AnswerGenerator` seam；切换供应商不改变领域状态机。官方能力与未验证项见 `docs/research/LLM_PROVIDER_INTEGRATION_2026-09-07.md`。

## 5. 首条验收例子

输入：“雨量为20毫米时，属于小雨、中雨还是大雨？”

当前问题没有说明 12 小时或 24 小时。两个统计口径可能产生不同结论，因此首条切片必须返回：

```text
status = needs_clarification
missing_conditions = [statistical_period]
answer = none
```

该结果不依赖回答模型是否“猜对”，而是查询核心的业务规则。

## 6. 受控连续追问

`needs_clarification` 返回显式 `continuation_context`，其中保存待补充问题和已经确认的适用范围。下一轮用户只回答“12小时”或“24小时”时，调用方把该上下文带回；查询核心生成包含原问题和补充条件的有效问题，再进行检索和回答。

回答完成后返回的问题锚点与已解析条件可以支持继续修改条件，例如从 12 小时切换到 24 小时。该机制不保存隐式服务端聊天历史，不允许把任意旧对话自动拼入规范查询，网页、REST 和后续 MCP 必须显式传递同一上下文。

## 7. 数值区间表格检索

对“20 毫米落入哪个降雨等级”这类问题，向量相似度和 BM25 负责相关语义召回，但不负责区间运算。系统从当前正式语料内已批准的表格证据派生数值区间投影，确定性匹配指标、单位、统计时段和上下界后，再从 Qdrant 回取原始表格证据并提升排序。

该投影可由证据单元和语料 manifest 重建，不是第二事实源，也不是另一套向量数据库。AI-001、AI-002 的正式语料检索结果见 `E04B_NUMERIC_RANGE_RETRIEVAL.md`。

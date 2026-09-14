# 应用编排与服务集成调研

> 调研日期：2026-09-03  
> 适用项目：排水规范证据助手  
> 状态：技术设计输入，不是最终架构决策  
> 来源边界：只采用截至调研日可核验的 LangChain/LangSmith、FastAPI、Model Context Protocol 规范及 Python SDK 等一手官方资料。本文把“官方事实”和“本项目建议”分开表述。

## 1. 结论摘要

本项目应采用“一个领域核心，三个入口适配器”的结构，而不是分别为网页、REST API 和 MCP 实现三套 RAG：

```text
网页工作台 ──> FastAPI HTTP/SSE 适配器 ─┐
普通 API ───> FastAPI JSON 适配器 ─────┼──> QueryApplicationService
其他 Agent ─> MCP tools/resources 适配器 ┘              │
                                                      v
                                    范围解析 → 证据检索 → 重排
                                    → 回答生成 → 引用校验 → 结果判定
                                                      │
                       ┌──────────────────────────────┼────────────────────────┐
                       v                              v                        v
                检索/索引端口                  模型调用端口             查询记录/观测端口
                       │                              │                        │
              BM25、向量库等适配器          LangChain Runnable 适配器   本地审计记录、LangSmith
```

推荐边界如下：

1. **领域核心拥有最终契约和规则。** `QueryRequest`、`Evidence`、`Citation`、`AnswerResult`、证据状态、拒答原因、语料版本等是本项目自己的类型；核心不导入 FastAPI、MCP 或 LangSmith，也不把 `LangChain Document.metadata` 当作权威领域模型。
2. **LangChain 有选择地用于模型编排和生态适配。** 使用 `Runnable`、模型结构化输出及必要的 tracing 配置；可以使用 `Document`、`VectorStore`、`BaseRetriever` 对接组件，但进入领域核心前转换成本项目证据对象。混合检索、法规适用性和引用规则由领域服务掌握。
3. **FastAPI 是人工网页和普通 API 的服务边界。** 首期同时提供完整 JSON 响应和 POST SSE 流；流式事件来自领域应用服务，FastAPI 只负责协议编码、鉴权、校验和错误映射。
4. **MCP 是次要调用方适配器，不是内部总线。** MCP tool 调用同一个 `QueryApplicationService`，返回结构化结果，并为证据附资源链接；不让领域核心返回 `CallToolResult`。
5. **LangSmith 是可替换的观测与评估工具，不是审计事实库。** 本地仍保存可复现查询、语料版本、证据和最终结果；LangSmith 用于跨步骤 trace、实验比较和失败样本回流。
6. **首期不需要 LangGraph 或 Agent 循环。** 当前主流程是有明确阶段和受控分支的证据问答；`RunnableSequence`/普通应用服务足够。只有出现持久化长任务、复杂人工中断恢复或真正循环决策时，才重新评估图式编排。

这套设计能够体现对 LangChain 的理解，同时保留替换框架的能力；框架存在是因为它解决了已验证的问题，而不是为了简历展示。

## 2. 调研问题与证据范围

本报告核验以下问题：

- LangChain 当前 `Runnable`/LCEL 式组合的能力和适用边界；
- `Document`、`VectorStore`、`Retriever` 是否适合作为本项目核心接口；
- 模型结构化输出、回调、流式事件和 LangSmith tracing/evaluation；
- FastAPI 如何提供浏览器友好的流式接口；
- MCP Python SDK 如何返回机器可读结果和可回查资源；
- 网页、API、MCP 如何共享同一领域核心；
- 每项推荐的风险、最小验证实验及推翻条件。

没有在本次调研中决定具体向量数据库、Embedding、重排模型、前端框架或部署平台。

## 3. LangChain 当前抽象

### 3.1 Runnable 与 LCEL 式组合

**官方事实**

LangChain Core 将 `Runnable` 定义为可调用、批处理、流式执行、转换和组合的工作单元，标准方法包括 `invoke/ainvoke`、`batch/abatch`、`stream/astream` 与事件流。主要组合原语是 `RunnableSequence` 和 `RunnableParallel`；`|` 运算符构造顺序组合，字典可构造并行组合。组合后的链自动获得同步、异步、批处理和流式接口。`RunnableConfig` 可以携带 tags、metadata 和并发等配置。[Runnable 官方 API](https://reference.langchain.com/python/langchain-core/runnables/base/Runnable)；[LangChain 官方源码说明](https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/runnables/base.py)

这说明通常所谓 LCEL 的实质是对 `Runnable` 的声明式组合方式，不是必须覆盖整套应用的独立运行时。当前官方参考的稳定核心概念是 `Runnable` 及其标准方法。

**本项目建议**

只在边界清晰的模型子流程中使用 Runnable，例如：

```text
问题 + 已继承条件
  → 条件抽取（结构化输出）
  → 查询改写
  → 调用领域检索端口
  → 上下文编排
  → 证据约束回答（结构化输出）
```

不要把文档发布、语料版本状态机、地域/效力规则、引用合法性和查询审计写成一条巨型 LCEL 表达式。这些步骤包含领域不变量、事务和明确错误语义，用普通 Python 应用服务更容易测试和解释。

### 3.2 Document、VectorStore 与 Retriever

**官方事实**

- LangChain `Document` 用于检索流程，保存 `page_content` 和 metadata；它不是聊天消息类型。[Document / langchain-core 参考](https://reference.langchain.com/python/langchain-core/prompts/base)
- `VectorStore` 提供统一接口，官方概览列出的主要操作包括 `add_documents`、`delete` 和 `similarity_search`，并由具体实现决定相似度、过滤和索引细节。[Vector store 官方文档](https://docs.langchain.com/oss/python/integrations/vectorstores)
- `Retriever` 比 vector store 更一般：输入非结构化字符串查询，返回 `Document` 列表；向量库可以转换为 retriever。`BaseRetriever` 本身遵循 Runnable 接口，并通过 `_get_relevant_documents`（可选异步版本）实现自定义检索。[Retriever 官方文档](https://docs.langchain.com/oss/python/integrations/retrievers/index)；[BaseRetriever 官方参考](https://reference.langchain.com/python/langchain-core/vectorstores/base)

**本项目取舍**

| 抽象 | 推荐使用位置 | 不作为核心接口的原因 |
| --- | --- | --- |
| `Document` | LangChain loader、splitter、vector store 和 retriever 适配层 | `metadata: dict` 太宽松，不能独立保证文档版本、效力、地域、页码、条款和来源等领域不变量 |
| `VectorStore` | 向量召回实现的候选适配器 | 本项目需要 BM25 + 向量 + 过滤 + 融合 + 重排；统一向量接口不承诺这些组合行为在各实现间完全一致 |
| `BaseRetriever` | 需要接入 LangChain Runnable/tracing 时，包装本项目检索服务 | 它的通用契约是字符串到 `Document[]`，不足以表达范围条件、语料版本、分路分数、拒绝原因和可复现检索参数 |

领域核心建议定义自己的检索端口：

```python
class EvidenceRetriever(Protocol):
    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult: ...
```

`RetrievalRequest` 显式包含规范化问题、地域、适用时间、对象、文件层级、指定文档和 `corpus_version`；`RetrievalResult` 包含候选证据、各阶段分数、使用的检索配置和降级/失败信息。LangChain 适配器负责在 `Document` 与领域对象之间转换。

### 3.3 结构化输出

**官方事实**

LangChain chat model 支持 `with_structured_output`，可接受 Pydantic、TypedDict 或 JSON Schema。Pydantic 提供运行时验证；具体强制方式取决于模型提供商，可使用原生 JSON Schema、tool calling 或 JSON mode。`include_raw=True` 可以同时获得原始消息、解析结果和解析错误。[LangChain Models：Structured output](https://docs.langchain.com/oss/python/langchain/models)

Agent API 也提供 `ProviderStrategy` 与 `ToolStrategy`，但当前项目不需要为了结构化输出而采用 Agent；直接在模型适配器调用 `with_structured_output` 更小、更可控。[LangChain Structured output](https://docs.langchain.com/oss/python/langchain/structured-output)

**本项目建议**

结构化输出用于两个窄任务：

1. `ScopeExtraction`：从当前问题和允许继承的对话条件中提取地域、时间尺度、对象、文档指向及缺失条件；
2. `AnswerDraft`：生成主张、每个主张引用的 evidence ID、限制条件和结果状态。

模型不得直接生成最终可展示引用。应用服务必须再次校验：

- evidence ID 是否来自本次检索结果；
- 证据是否属于本次语料版本；
- 每个规范性主张是否至少有可回查证据；
- 数值是否携带单位、统计时段、表头/注释等必要上下文；
- 不通过时降级为“仅展示搜索结果”或“需要补充条件”。

### 3.4 回调、事件和流式输出

**官方事实**

Runnable 支持 `astream_events()`，事件可携带名称、tags、metadata、输入、输出和 stream chunk；LangChain 模型流式执行时会触发 callback token 事件，供上层流式接口消费。[Runnable 官方 API](https://reference.langchain.com/python/langchain-core/runnables/base/Runnable)；[LangChain Models：Streaming](https://docs.langchain.com/oss/python/langchain/models)

**本项目建议**

不要把 LangChain callback event 直接暴露为网页 API。定义稳定的领域流式事件：

```text
query.accepted
scope.resolved | scope.input_required
retrieval.completed
answer.delta                 # 可选、非权威预览
answer.final                 # 引用校验后唯一权威结果
query.failed
```

适配层可以把 Runnable events 映射为上述事件，但客户端不能依赖 `on_chain_start` 等框架内部名字。这样更换编排方式不会破坏网页和 API。

对本项目而言，逐 token 显示与证据可靠性存在张力：token 流出现时引用尚未完成最终校验。首期更稳妥的做法是流式展示阶段进度，最终一次发送已校验的 `AnswerResult`；只有验证用户体验确实需要时，才增加带“生成中”标识的 `answer.delta`。

## 4. LangSmith 的观测与评估边界

### 4.1 官方能力

LangSmith 把一次操作记录为 trace，内部步骤记录为 runs；多轮对话可以通过共同的 `thread_id` metadata 关联。它可以自动追踪 LangChain，也可以用 `@traceable`、`trace` context manager 或 `RunTree` 手工埋点非 LangChain 代码。tags 和 metadata 可用于过滤和分组。[LangSmith Observability concepts](https://docs.langchain.com/langsmith/observability-concepts)；[Trace LangChain applications](https://docs.langchain.com/langsmith/trace-with-langchain)

LangSmith 的标准评估流程是：建立 dataset、定义 evaluator、运行 experiment、分析和比较结果。官方支持人工、代码规则、LLM-as-judge 和 pairwise evaluator；离线评估用于发布前比较/回归，在线评估用于生产 traces，并可把失败 trace 回流到 dataset。[LangSmith Evaluation](https://docs.langchain.com/langsmith/evaluation)；[Evaluation concepts](https://docs.langchain.com/langsmith/evaluation-concepts)

Dataset 示例包含 inputs、可选 reference outputs 和 metadata，并支持版本变化记录；实验记录每个样例的输出、评分和 trace，可比较提示词、模型或其他配置。[Manage datasets](https://docs.langchain.com/langsmith/manage-datasets)；[How to evaluate an LLM application](https://docs.langchain.com/langsmith/evaluate-llm-application)

LangSmith 官方还提供隐藏或变换 metadata、inputs 和 outputs 的机制，因此 tracing 并不等于必须上传全部原文；是否上传仍需由项目数据政策决定。[Mask inputs and outputs](https://docs.langchain.com/langsmith/mask-inputs-outputs)

### 4.2 本项目推荐

LangSmith 首期作为**可关闭的开发依赖**：

- trace 层级建议为 `query` → `scope` → `retrieve_sparse` / `retrieve_dense` → `fuse` → `rerank` → `compose` → `citation_validate`；
- metadata 只放可筛选的版本信息，如 `corpus_version`、`retrieval_config_version`、`prompt_version`、模型名、环境和匿名 query ID；
- 原始 PDF 全文不作为 metadata；公开种子文档可以按需求政策发送必要片段，但仍默认控制上下文长度；
- 本地查询记录是 R-08 的审计依据，至少保存请求、解析后的范围、语料版本、候选证据 ID、最终结果与配置版本；LangSmith 中断不能让产品失去可复现性；
- 评估数据的权威副本保存在项目中，LangSmith dataset/experiment 用于运行和比较，不是唯一存储位置。

评估分层：

| 层级 | 优先 evaluator | LangSmith 用途 |
| --- | --- | --- |
| 结构与引用 | 确定性代码规则 | 检查 schema、evidence ID、页码/条款、必要字段 |
| 检索 | 人工标注证据 + 代码指标 | 比较 Recall@k、MRR/nDCG 等配置实验 |
| 回答忠实性 | 人工复核为准，LLM judge 辅助 | 定位疑似无依据主张，不把 judge 分数当真值 |
| 拒答/追问 | 场景标签 + 规则/人工判断 | 比较条件不足、无覆盖、越界等分支 |
| 运行 | trace 时延、错误、token/cost | 发现慢步骤和失败模式 |

## 5. FastAPI 服务与流式接口

### 5.1 官方能力

FastAPI 默认返回 JSON。`StreamingResponse` 可以消费同步或异步 generator/iterator；直接返回 Response 子类时，需要注意自动数据转换和 OpenAPI 文档行为。[FastAPI Custom Response / StreamingResponse](https://fastapi.tiangolo.com/advanced/custom-response/)

截至调研日，FastAPI 提供一等 SSE 支持：路径函数 `yield` 数据并使用 `EventSourceResponse`；每个项目会编码为 SSE `data`，声明 `AsyncIterable[PydanticModel]` 时可用于验证、文档和序列化。官方文档还覆盖带 event/id/retry 的 `ServerSentEvent`、POST SSE 及 `Last-Event-ID` 恢复。[FastAPI Server-Sent Events](https://fastapi.tiangolo.com/tutorial/server-sent-events/)

FastAPI 也支持 JSON Lines，一个 JSON 对象一行，适合非浏览器流式消费者；官方把 SSE 作为相近但更适合浏览器原生消费的方式。[FastAPI Stream JSON Lines](https://fastapi.tiangolo.com/tutorial/stream-json-lines/)

### 5.2 本项目接口建议

保留两个明确契约：

```text
POST /api/v1/queries
  request: QueryRequest
  response: AnswerResult

POST /api/v1/queries:stream
  request: QueryRequest
  response: text/event-stream，内容为 QueryEvent
```

网页使用 POST SSE，因为问题、上下文和范围条件属于请求体；服务间不需要流式时直接使用完整 JSON。SSE 事件必须有 `query_id`、`sequence`、`event_type` 和对应 payload，最终事件携带完整 `AnswerResult`。

关键实现边界：

- FastAPI handler 只做边界校验、身份/权限、调用应用服务、事件序列化和错误到 HTTP 的映射；
- `QueryApplicationService.execute()` 与 `.stream()` 返回领域结果/事件，不返回 FastAPI Response；
- 客户断开时应把取消信号传给应用服务，并验证模型/检索调用是否真正停止；
- 保存结果应在最终事件发出前完成，避免客户端看到结果但审计记录不存在；
- 首期不承诺断线续传。只有真实需求出现时，才实现事件持久化和 `Last-Event-ID` 恢复。

## 6. MCP 服务集成

### 6.1 截至 2026-09-03 的协议事实

MCP 的服务端能力包括 prompts、resources 和 tools；tools 由模型控制调用，resources 由应用控制加载。[MCP Server overview](https://modelcontextprotocol.io/specification/2025-11-25/server/index)

`2026-07-28` 是截至调研日已经发布的协议版本。该版本取消协议层的 initialize handshake 和隐式 session，每个请求独立携带协议、客户端和能力信息；如果应用需要跨调用状态，应返回显式 handle 并在后续调用中传回。它同时调整 Streamable HTTP 路由与扩展机制。[MCP 2026-07-28 官方发布说明](https://blog.modelcontextprotocol.io/posts/2026-07-28/)

MCP tool result 可以包含：

- 给模型/人阅读的 `content` blocks；
- 给程序读取并按 `outputSchema` 校验的 `structuredContent`；
- `isError`；
- `TextContent`、`ResourceLink`、`EmbeddedResource` 等内容块。

官方 Python SDK client 文档明确区分 `content`（模型读取）与 `structured_content`（代码读取），并要求客户端在信任结构化内容前检查 `is_error`。SDK 支持 Pydantic 等有类型标注的返回值生成和校验结构化结果，也允许直接构造 `CallToolResult` 获得完整控制。[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)；[Python SDK Client](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/client/index.md)

MCP 规范允许 tool 返回 `resource_link`，包括 URI、name/title、description、MIME type、size 和 annotations；tool 返回的资源链接不保证出现在 `resources/list`。资源 URI 可以是 HTTPS、file 或自定义 scheme；若 HTTPS 资源不能由客户端直接读取，官方建议使用其他或自定义 URI，并由 MCP server 提供资源读取能力。[MCP 2025-11-25 Schema: ResourceLink](https://modelcontextprotocol.io/specification/2025-11-25/schema)；[MCP Resources](https://modelcontextprotocol.io/specification/2025-11-25/server/resources)

`2026-07-28` 放宽了 structured content 的根类型；但为兼容仍停留在 2025-11-25 的客户端，返回顶层 JSON object 比返回裸数组更稳妥。[MCP 2026-07-28 发布说明](https://blog.modelcontextprotocol.io/posts/2026-07-28/)；[MCP 2025-11-25 Schema](https://modelcontextprotocol.io/specification/2025-11-25/schema)

### 6.2 本项目 MCP 设计建议

首期只暴露少量只读工具，避免把内部 pipeline 的每一步都做成 MCP tool：

```text
search_evidence(question, scope?, conversation_context?, limit?)
  → 只检索并返回结构化证据，适合数据分析 Agent 自己使用证据

answer_with_evidence(question, scope?, conversation_context?)
  → 返回经过引用校验的回答，或条件不足/无可靠答案状态

get_document_evidence(evidence_id | document_version_id + locator)
  → 精确回查原文及定位
```

结构化返回建议保持对象根：

```json
{
  "query_id": "...",
  "status": "supported | input_required | search_only | out_of_scope | failed",
  "resolved_scope": {},
  "answer": {"claims": []},
  "evidence": [],
  "corpus_version": "...",
  "warnings": []
}
```

同时提供简短 `TextContent`，让只消费文本的旧 host 仍能理解结果；为每份证据附 `ResourceLink`。资源建议使用稳定、不可变的版本 URI，例如：

```text
evidence://corpus/{corpus_version}/documents/{document_version_id}/pages/{page}
```

若部署后有经过鉴权、客户端可直接访问的 HTTPS 证据页面，可在结构化字段中同时返回 `source_url`/`viewer_url`；不要假设所有 MCP host 都会渲染或读取自定义 resource link。

连续追问上下文不能依赖 MCP 连接：调用方应显式传递 `conversation_context`（已确认地域、统计时段、对象等），或者先获得应用级 `conversation_id`/handle 后在下一次工具调用中传回。网页也应使用同一显式上下文模型，这样 API 与 MCP 的语义一致。

MCP server 只负责：参数 schema、调用应用服务、领域结果到 `structuredContent`/content blocks/resource links 的映射，以及协议错误处理。它不得直接访问向量库或自行生成另一套回答。

## 7. 共享领域核心的具体边界

### 7.1 建议模块职责

```text
domain/
  query.py                 # 领域值对象、状态、错误、规则
  evidence.py              # 文档版本、定位、证据、引用不变量

application/
  query_service.py         # 完整问答用例与受控分支
  retrieval_service.py     # 检索、融合、重排的应用协调
  citation_validator.py    # 主张—证据校验
  ports.py                 # Retriever、Model、RecordStore、Observer 端口

adapters/
  langchain/               # Runnable、Document 转换、模型结构化输出
  retrieval/               # BM25、向量库、reranker
  fastapi/                 # JSON/SSE DTO 与路由
  mcp/                     # tools/resources 与 CallToolResult 映射
  langsmith/               # trace/evaluation adapter
  persistence/             # 查询记录和语料版本存储
```

这只是职责图，不是要求现在机械创建所有目录。实现时按可验证能力纵向落地。

### 7.2 共享契约

三个入口最终调用相同方法：

```python
class QueryApplicationService:
    async def execute(self, request: QueryRequest) -> AnswerResult: ...
    async def stream(self, request: QueryRequest) -> AsyncIterator[QueryEvent]: ...
```

约束：

- `AnswerResult` 是完整、版本化的领域结果；网页 DTO、API JSON 和 MCP structured content 只做无损映射；
- `query_id`、`corpus_version`、证据 ID 和定位信息在所有入口一致；
- 同一请求通过 API 与 MCP 调用时，忽略展示差异后应得到语义等价结果；
- 入口层不得覆写地域优先规则、拒答规则或引用判定；
- LangChain `RunnableConfig`、FastAPI `Request`、MCP context 和 LangSmith run ID 不进入领域实体。

### 7.3 为什么不把 LangServe 或 MCP 当统一内部 API

本项目需要同时满足人类界面的逐条引用、普通服务调用和 Agent 工具调用。直接把某个框架对象作为内部统一契约，会把其事件、错误和版本变化扩散到所有入口。领域 `AnswerResult` 才是业务稳定点；FastAPI 和 MCP 是两种不同消费协议，LangChain 是一个实现适配器。

## 8. 推荐采用范围

| 技术 | 首期决定 | 使用边界 |
| --- | --- | --- |
| LangChain Core / Runnable | 采用，但窄用 | 模型调用、结构化输出、局部组合、stream event/tracing；不承载领域状态机 |
| LangChain `Document` | 采用在适配层 | loader/vector/retriever 互操作；立即转换为领域 EvidenceCandidate |
| LangChain `VectorStore` | 先做实验后决定具体实现 | 只代表向量召回，不作为混合检索统一领域接口 |
| LangChain `BaseRetriever` | 可选适配器 | 需要接入 Runnable/LangSmith 时包装领域 retriever |
| LangSmith tracing | 开发阶段可选启用 | 调试与实验；本地审计记录不依赖它 |
| LangSmith evaluation | 采用做实验管理 | 权威评测集仍版本化保存在项目中；代码/人工 evaluator 优先 |
| FastAPI JSON | 采用 | 稳定的人工/API 服务入口 |
| FastAPI SSE | 最小实验通过后采用 | 阶段事件 + 最终已校验结果；首期不承诺恢复 |
| MCP Python SDK | 核心稳定后增加 | 只读 tool/resource 适配器，复用同一应用服务 |
| LangGraph | 首期不采用 | 仅在出现需要持久化恢复的复杂循环/人工中断流程后复评 |

## 9. 主要风险与控制

| 风险 | 影响 | 控制 |
| --- | --- | --- |
| LangChain 类型泄漏到领域核心 | 更换框架或测试检索规则时成本扩大 | 在 adapter 边界转换 `Document`；核心接口只使用项目类型 |
| 用 Runnable 拼成不可读巨链 | 分支、错误和审计语义难解释 | Runnable 只负责模型子流程；应用服务显式协调领域阶段 |
| provider 的结构化输出能力不一致 | schema 失败、流式行为不一致 | Pydantic 验证、保留 raw/error、限定重试，做模型兼容实验 |
| 统一 VectorStore 掩盖实现差异 | 过滤、删除、分数和混合检索行为不一致 | 领域检索端口定义所需语义；每个 adapter 做契约测试 |
| token 流先展示未校验结论 | 用户先看到错误或无依据陈述 | 默认流阶段状态，最终结果校验后发布；delta 明确标为草稿 |
| 客户断开但后端仍计算 | 浪费模型成本，留下半成品状态 | 取消传播实验；最终记录原子保存；设置 timeout |
| MCP host 对新协议/ResourceLink 支持不一致 | Agent 只拿到文本或无法打开证据 | 对象根 structured content + TextContent 兼容副本 + 普通 URL；对目标 host 做验收 |
| 把 MCP 连接当会话 | 新协议下丢失追问上下文 | 显式传 context 或应用级 handle；服务端不依赖连接内存 |
| LangSmith 上传不必要的原文或敏感信息 | 数据与合规风险 | 可关闭、最小字段、mask/hide；内部手册引入前重新评审 |
| LangSmith 成为唯一审计存储 | 网络/账号不可用时无法复现 | 本地持久化 query record 与版本；LangSmith 只是观察副本 |
| 同一逻辑在 API/MCP 重复实现 | 结果漂移、修复不一致 | contract parity test，入口层只映射协议 |

## 10. 最小验证实验

这些实验应在完整技术方案批准前或第一个实现增量中完成。每个实验使用同一组少量固定输入，并保存实际输出。

### E-01 Runnable 与结构化输出可行性

**目的：** 验证所选模型通过 LangChain 能稳定生成 `ScopeExtraction` 和 `AnswerDraft`，并能被观察和测试。

**做法：**

- 选 10～20 个问题，覆盖地域缺失、降雨时段缺失、指定文档、无覆盖和正常问答；
- 分别执行 `invoke/ainvoke`，测试 `with_structured_output(Pydantic, include_raw=True)`；
- 记录 schema 成功率、解析错误、重试、延迟和 token；
- 用 `RunnableConfig` tags/metadata 和 `astream_events` 确认步骤可识别。

**通过证据：** 无未捕获解析异常；失败能进入显式状态；trace 能区分 scope 与 answer 步骤。

**推翻条件：** 若 LangChain 包装相较提供商 SDK 没有带来可复用组合、结构化输出或可观测收益，且明显增加流式/错误处理复杂度，则缩减为仅使用 `langchain-core` schema/adapter，或直接采用提供商 SDK 并用 LangSmith `@traceable` 手工观测。

### E-02 检索接口适配实验

**目的：** 验证 `Document`/VectorStore/Retriever 可作为组件适配层，而不会损失领域证据信息。

**做法：**

- 以几份已确认来源的 PDF 条款构造领域 Evidence 与 LangChain Document 双向转换；
- 验证版本、地域、效力、页码、条款、表格上下文、source URI 和稳定 ID 无损；
- 用 BM25 fake/基线与一个 InMemoryVectorStore 走同一个 `EvidenceRetriever` 契约；
- 验证过滤、删除/撤回和分数含义不被错误统一。

**推翻条件：** 若所选 VectorStore adapter 无法可靠表达版本过滤、稳定 ID、删除/撤回或异步行为，则领域端口保留，直接为实际存储编写 adapter，不强行使用 LangChain VectorStore。

### E-03 API 与 SSE 合约实验

**目的：** 验证同一个应用服务能同时支持完整 JSON 和浏览器流式状态，且断开可取消。

**做法：**

- 用 fake `QueryApplicationService` 固定产生事件；
- 检查 POST SSE 的事件顺序、Pydantic 序列化、错误事件和最终结果；
- 客户端中途断开，确认生成器和下游任务被取消；
- 比较非流式 `AnswerResult` 与流式 final payload 完全等价。

**推翻条件：** 若真实基线延迟很短、用户测试不需要中间反馈，或取消/代理缓冲带来的复杂度大于收益，则首期只提供 JSON，保留事件接口但不公开 SSE。

### E-04 MCP 兼容与语义一致性实验

**目的：** 验证 MCP Python SDK 能无损映射领域结果，并被目标 host 正确消费。

**做法：**

- 使用 SDK 的 in-memory client 测试 tool input/output schema、`structured_content`、`content`、`is_error` 和 ResourceLink；
- 再用 MCP Inspector 及计划接入的至少一个真实 Agent host 测试；
- 对同一 `QueryRequest` 分别走 Python 直调、FastAPI JSON、MCP，比较 `query_id` 之外的语义字段；
- 验证 host 不支持 ResourceLink 时，仍可从结构化字段或 TextContent 获得证据标题与 URL；
- 显式传递追问上下文，确认不依赖连接 session。

**推翻条件：** 若目标 Agent host 不能稳定读取 structured content，或 MCP 增加的互操作价值尚无真实消费者，则先交付 API，把 MCP 保留为后续 adapter，不影响领域核心。

### E-05 LangSmith 观测与评估实验

**目的：** 验证 LangSmith 是否真正提高诊断与版本比较能力。

**做法：**

- 把 E-01 数据作为小 dataset，运行两个可解释配置实验；
- 至少使用一个确定性结构/引用 evaluator、一个人工标签；LLM judge 仅作辅助；
- 验证 corpus、prompt、retrieval 和 model 版本 metadata 可筛选；
- 关闭网络或 tracing 后重跑，确认核心查询和本地审计完全可用；
- 检查 mask/hide 配置不会上传超出政策范围的数据。

**推翻条件：** 若免费/可接受成本范围内无法支持需要的比较、数据政策不允许、或本地报告已能提供同等证据，则 LangSmith 改为可选演示集成，不作为发布依赖。

## 11. 后续技术设计应形成的决策

完成上述最小实验后，技术方案需要明确而不是预设以下事项：

1. 领域请求、结果、事件和证据对象的版本化 schema；
2. LangChain 在 scope extraction、answer composition 和 retrieval adapter 中的确切边界；
3. 混合检索端口及每个实现的分数、过滤、撤回语义；
4. SSE 是否进入首期，以及是否只流阶段还是也流 answer delta；
5. LangSmith 默认关闭/开启策略、数据最小化和本地审计字段；
6. MCP 首期工具数量、目标 host、协议/SDK 固定版本和兼容测试矩阵；
7. 应用级连续追问上下文的显式结构和过期规则。

## 12. 推荐结论与推翻原则

当前推荐为：

> 用项目自有的 `QueryApplicationService` 和领域结果作为稳定核心；LangChain 负责可替换的模型编排与生态适配，FastAPI 负责网页/HTTP，MCP 负责 Agent 协议映射，LangSmith负责可关闭的 trace 与实验。所有入口共享同一条领域问答链和同一份审计记录。

该推荐不是为了确保技术栈名称出现在项目里。任何组件只要在最小实验中不能证明它降低复杂度、提高可观测性、支持互操作或改善可验证性，就应缩小使用范围或移除。反过来，只有当普通应用服务已不足以表达经验证的长流程、恢复或循环需求时，才引入更重的编排框架。

## 13. 一手来源索引

### LangChain / LangSmith

- [Runnable API](https://reference.langchain.com/python/langchain-core/runnables/base/Runnable)
- [Runnable 官方源码与组合说明](https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/runnables/base.py)
- [LangChain Models](https://docs.langchain.com/oss/python/langchain/models)
- [Structured output](https://docs.langchain.com/oss/python/langchain/structured-output)
- [Vector store integrations](https://docs.langchain.com/oss/python/integrations/vectorstores)
- [Retriever integrations](https://docs.langchain.com/oss/python/integrations/retrievers/index)
- [LangSmith Observability concepts](https://docs.langchain.com/langsmith/observability-concepts)
- [Trace with LangChain](https://docs.langchain.com/langsmith/trace-with-langchain)
- [LangSmith Evaluation](https://docs.langchain.com/langsmith/evaluation)
- [Evaluation concepts](https://docs.langchain.com/langsmith/evaluation-concepts)
- [Manage datasets](https://docs.langchain.com/langsmith/manage-datasets)
- [Evaluate an LLM application](https://docs.langchain.com/langsmith/evaluate-llm-application)
- [Mask inputs and outputs](https://docs.langchain.com/langsmith/mask-inputs-outputs)

### FastAPI

- [Custom Response / StreamingResponse](https://fastapi.tiangolo.com/advanced/custom-response/)
- [Server-Sent Events](https://fastapi.tiangolo.com/tutorial/server-sent-events/)
- [Stream JSON Lines](https://fastapi.tiangolo.com/tutorial/stream-json-lines/)

### Model Context Protocol / Python SDK

- [MCP 2026-07-28 official release](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
- [MCP 2025-11-25 Server overview](https://modelcontextprotocol.io/specification/2025-11-25/server/index)
- [MCP 2025-11-25 Schema](https://modelcontextprotocol.io/specification/2025-11-25/schema)
- [MCP Resources](https://modelcontextprotocol.io/specification/2025-11-25/server/resources)
- [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [MCP Python SDK client contract](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/client/index.md)

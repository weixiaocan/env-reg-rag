# E-06E 只读 MCP 与真实 Agent host 联调

## 1. 验收结论

M6 的 MCP 能力已通过。项目使用官方 Python SDK `mcp==2.2.0`，通过 stdio 暴露三个只读工具：

- `answer_with_evidence`：复用正式语料 `QueryApplicationService`；
- `search_evidence`：复用实验语料的 `source_locator_only` 查询路径；
- `get_document_evidence`：通过应用层 `EvidenceSourceService` 按稳定 evidence ID 读取已发布证据。

MCP adapter 不包含检索算法、Qdrant 查询、提示词、适用性规则或引用校验。HTTP 与 MCP 共用 `AnswerResultDto`，单条证据读取也共同经过应用层边界。

## 2. 协议输出

每个工具都声明 `readOnlyHint=true`、`destructiveHint=false`、`idempotentHint=true` 和 `openWorldHint=false`。问答工具返回：

1. 对象根 `structuredContent`，无损映射 `AnswerResult`；
2. 供只读取文本的宿主使用的简短 `TextContent`；
3. 每条证据对应的 `evidence://<evidence_id>` ResourceLink。

MCP 同时注册 `evidence://{evidence_id}` 资源模板。资源返回证据文字、文档版本、条款路径、物理页、使用策略和公开来源 URI；因此即使某份实验文档尚无已核验公开 URL，ResourceLink 也不会变成不可读链接。

## 3. 三入口一致性实跑

2026-09-08 对问题“采用用水量折算法时，原文公式在哪里？”执行：

```powershell
.venv\Scripts\python.exe scripts\run_m6_mcp_consistency.py
```

脚本分别调用应用服务、运行中的 FastAPI 和独立 stdio MCP 子进程。三者完整结构的 SHA-256 均为：

```text
bc377c1c7b8607489f060563446e197b6b129c61e9b1d2357657b8bbab308a07
```

三路均返回 `search_only` 和 `ev_58337104c115f4ddb491ad736f859ae7`。硬门槛同时确认：工具集合精确匹配、全部只读、MCP 调用成功、结果非空、文本与 ResourceLink 同时存在、ResourceLink 可由 stdio 客户端读取。机器结果保存在 `data/eval_results/m6-mcp-consistency-v1.json`。

## 4. 真实 Agent host

使用 OpenAI Codex `0.152.1` 的 `exec --ephemeral` 模式和只读 sandbox 作为真实宿主。宿主只启用 `search_evidence` 与 `get_document_evidence`，提示要求它自行选择工具、不得猜测公式内容。

观察到的调用：

```text
mcp: drainage_rag/search_evidence started
mcp: drainage_rag/search_evidence (completed)
```

Agent 返回 T/CECS 1764-2024 第 5.2.4 条、PDF 物理第 19 页、稳定 evidence ID，并明确要求打开原页人工核验，没有生成未经批准的公式转写。结构化记录保存在 `data/eval_results/m6-codex-agent-host-v1.json`。

Codex 的 stdio MCP 配置采用 `command`、`args`、`cwd`、`enabled_tools`、启动超时和工具超时；配置字段依据官方 OpenAI Codex 配置参考验证。

## 5. 测试与 review

- MCP/HTTP/EvidenceSource 聚焦测试：18 项通过；
- 全仓回归：123 项通过；
- Python 编译检查通过；
- 独立只读 reviewer 首轮提出 1 个 P1 和 2 个 P2，均已修复：增加应用层 `EvidenceSourceService`、实读 locator ResourceLink、加强一致性脚本硬门槛并移除写死的 Python 路径；
- 修复后重新执行聚焦测试、全仓回归和真实 stdio 一致性测试，均通过；评估运行的查询 trace 写入系统临时目录，避免与运行中服务共享同一追踪文件。

## 6. 已知限制

- 当前发布的是本机 stdio MCP，不是带认证的远程 Streamable HTTP 服务；
- 每个本地 MCP 进程会加载一份 BGE 模型，尚未做多宿主并发和资源上限测试；
- ResourceLink 指向 MCP 证据资源，公开 PDF URL 位于资源的 `source_uri` 字段；实验文档没有已核验 URL 时仍需从本地文档入口人工核验；
- MCP annotations 是宿主提示，真正的安全边界来自服务只注册查询工具且没有写入工具；
- 本次真实 Agent host 只验证了公式定位的 `search_only` 路径；回答生成路径已有确定性契约测试，但未额外消耗模型费用重复做 Agent E2E。

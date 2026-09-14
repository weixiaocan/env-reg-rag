# 排水规范证据助手——技术方案

## 文档控制

- 状态：Approved v1.2
- 修订日期：2026-09-05
- 产品基线：`docs/PRODUCT_SPEC.md` Approved v1.0
- 设计方法：先确定稳定业务契约，再用小样本实验批准具体组件；旧架构、ADR、配置和代码只作候选参考
- 调研输入：`docs/research/INGESTION_STACK_RESEARCH_2026-09-03.md`、`RETRIEVAL_STORAGE_RESEARCH_2026-09-03.md`、`APP_INTEGRATION_RESEARCH_2026-09-03.md`

## 1. 方案结论

本项目采用“**可审计语料生命周期 + 一个证据查询核心 + 多个入口适配器**”的总体结构。

```text
公开来源 PDF
  -> 登记与来源核验
  -> 解析/OCR 与质量门控
  -> 项目自有 Canonical Document
  -> 条款/表格证据单元
  -> 不可变语料版本
  -> 可重建的检索索引

网页 / REST API / 后续 MCP
  -> QueryApplicationService
  -> 条件识别与追问
  -> 精确 + 混合检索
  -> 适用性标注与 EvidencePack
  -> 受证据约束的回答与引用校验
  -> AnswerResult
```

首期技术策略：

1. 文档业务真相由原始文件、不可变解析产物和 SQLite 控制记录共同保存；检索库只是可重建投影。
2. PyMuPDF/PyMuPDF4LLM 进入文字层预检和原生解析主路径；PaddleOCR PP-StructureV3 只处理无可用文字层、局部扫描或质量不合格页面；Docling 作为结构恢复对照实验。
3. 项目确定开源。PyMuPDF 的 AGPL/商业双许可证不再构成 POC 排除理由，但仓库最终许可证和发布方式必须经过兼容性确认；本方案不提供法律结论。
4. Qdrant 作为首期唯一检索数据库：用 `exact=true` 的全扫描查询建立 dense 精确基线，用默认 ANN 查询验证服务性能，用 multilingual BM25/sparse 与 RRF 建立词法和混合检索基线。FAISS 和 Weaviate 不进入正式依赖；若中文词法能力在 Golden Set 上不达标，再评估 Elasticsearch + SmartCN。
5. 项目自己的领域类型和 `QueryApplicationService` 是稳定核心。LangChain 有意义但窄范围地用于模型结构化输出、局部 Runnable 编排、`Document` 生态适配和实验观测。
6. FastAPI 是首个人工/API 服务入口；SSE 在延迟和取消实验通过后启用。MCP 是核心稳定后的只读适配器，不是内部总线。
7. LangSmith 可用于 trace 和实验比较，但本地审计、评测集和复现能力不能依赖 LangSmith。
8. 首期不引入 LangGraph、分布式集群、复杂权限系统和多供应商插件体系。

这是一份企业式 POC 技术方案，不宣称上述候选已经达到生产可用；组件批准以本项目实验记录为准。

## 2. 设计驱动因素与约束

### 2.1 核心产品压力

1. **证据可核验**：回答主张必须能定位到原文、条款、页码和文档版本（R-03、R-07、R-14）。
2. **适用性不能猜测**：地域、时间、对象、文件层级和效力状态必须可见；条件不足时追问或拒答（R-04、R-06、R-13、R-15）。
3. **数值上下文不能丢失**：统计时段、单位、表头、注释和适用条件需要与数值共同保存和返回（R-05）。
4. **数据持续更新**：新增、更新、隔离、发布、撤回和回滚必须围绕明确语料版本工作（R-08、R-12、NFR-02、NFR-07）。
5. **人工与 Agent 复用同一能力**：网页、API 和后续 MCP 不得拥有互相偏离的检索与引用逻辑（R-11、R-14、R-16）。
6. **效果可解释地改进**：数据、检索、回答、拒答和系统运行分别评估，所有优化与同一评测基线比较（NFR-04）。

### 2.2 已确认约束

- 首期聚焦排水管网雨污混接、入流入渗、监测评估及紧密相关依据。
- 初始语料是小规模公开 PDF；只有来源和版本确认的材料允许进入正式语料及发送给公有云模型。
- 开发和演示以 Windows 单机为起点；当前机器为 GTX 1660 SUPER 6 GB 显存，默认方案不得依赖大显存模型。
- 项目用于求职面试，必须产生真实、可复现的证据，不虚构企业部署、用户数量、同行验收或效果数字。
- LangChain 必须解决真实问题，但不能侵入领域事实、适用性、引用校验和发布规则。
- 当前 23 个 PDF 包含 OCR 需求、来源缺失、重复、预览版和征求意见稿，不能整体视为正式语料。

## 3. 决策状态

| ID | 决策 | 当前状态 | 批准或推翻条件 |
| --- | --- | --- | --- |
| TD-01 | 页面级组合路由：PyMuPDF 原生、PaddleOCR OCR 降级、Docling 定向结构恢复 | 40 页实验完成并批准；非 approved 页面禁止发布 | 40 个代表页面具备唯一决定、Canonical 映射和 bbox 定位证据；继续以质量账本约束新增页面 |
| TD-02 | 文件产物 + SQLite 作为 POC 控制面事实源 | 原则批准 | 出现多人并发、服务化写入或复杂事务时迁移 PostgreSQL |
| TD-03 | Qdrant 统一承担精确 dense、ANN、multilingual BM25/sparse、RRF 和结构化过滤 | 组件选型已批准；能力仍须逐项验收 | 以同一语料和 Golden Set 验证召回、中文词法、过滤、alias、snapshot 恢复、资源和实现复杂度；中文词法不达标时评估 Elasticsearch/SmartCN |
| TD-04 | 条款树与表格为稳定证据单元，检索块是其派生视图 | 原则批准 | 页面、条款、表格和父子上下文必须能无损回查 |
| TD-05 | `QueryApplicationService` 统一查询、证据和结果状态 | 原则批准 | 网页、直接调用和后续 MCP 必须通过同一契约测试 |
| TD-06 | LangChain 用于模型编排和生态适配，不拥有领域状态机 | 原则批准 | 最小实验需证明结构化输出、组合或观测收益；否则继续缩小使用范围 |
| TD-07 | FastAPI 先行；SSE 实验后启用；MCP 后置 | 已同意 | SSE 需证明体验收益和取消正确；MCP 需有真实 Agent host 兼容测试 |
| TD-08 | 单机 Docker Compose 部署，版本化备份和恢复演练 | 实验候选 | 锁定组件后验证一键启动、数据卷、健康检查、备份和回滚 |

“原则批准”表示架构边界可进入实现设计；“实验候选”表示不得在简历或面试中表述为已经验证的最终选型。

## 4. 文档与语料生命周期

### 4.1 生命周期

```text
文档准入：待审核 -> 仅限实验 | 可进入正式语料 | 隔离
                    仅限实验 -> 可进入正式语料 | 隔离 | 撤回
              可进入正式语料 -> 隔离 | 撤回
                          隔离 -> 待审核

处理运行：未处理 -> 处理中 -> 质量通过 | 处理失败
                              处理失败 -> 处理中（新一次或重试运行）

语料版本：草稿 -> 已构建 -> 已发布 -> 已撤回
```

- 三类状态分别回答“允许怎么用”“这次处理是否成功”“哪一版语料对外服务”，不得合并成一条状态链。
- 登记前计算文件 SHA-256；相同字节只产生一个原始文件资产，重复内容不得伪装成两个文档版本。
- 来源、版本、文种、地域、发布日期和效力状态存在未知项时显式记录 `unknown`，不自动按“有效”处理。
- “仅限实验”材料可以用于解析和定位实验，但不能进入正式回答使用的语料版本；“隔离”表示暂停一切下游使用并等待复核。
- 每类状态变化分别记录操作者角色、时间、原因、输入版本和产物 ID；同一登记或处理请求应幂等。
- 已发布证据 ID 不原地改写。新解析器、新模型或元数据修正产生新 processing run 和新语料版本。

### 4.2 事实源和可重建投影

| 数据 | POC 存储 | 性质 |
| --- | --- | --- |
| 原始 PDF、来源快照、SHA-256 | 版本化文件目录 | 不可变事实 |
| 文档登记、版本关系、处理状态、语料发布记录 | SQLite | 控制面事实 |
| 解析器原始 JSON、Canonical Document、质量报告 | 版本化文件目录 + SQLite 索引 | 不可变/可审计事实 |
| chunk 文本、dense/sparse vector、查询 payload | Qdrant collection | 可删除并重建的检索投影 |
| 查询输入、条件、语料版本、候选、引用和最终结果 | SQLite + 可选 LangSmith 副本 | 本地审计事实 |
| Golden Set、评估配置与报告 | Git 版本化文件 | 评测事实 |

这里的“事实源”指：即使删除全部向量索引，仍能用原始 PDF、解析产物、配置和 SQLite 记录还原某个语料版本，并重新构建索引。检索库只保存为了加速查询而生成的数据，不负责决定某份文件是否有效、是否已审核或当前发布的是哪个业务版本。进入多人并发阶段后，保持领域接口不变并把控制面 adapter 换为 PostgreSQL。

### 4.3 Canonical Document

解析器输出先归一化为项目自有模型，再构造检索块和 LangChain `Document`：

```text
DocumentAsset
  asset_id, sha256, source_uri, document_version_id
  parser_name, parser_version, model_ids, config_hash, processing_run_id
Page
  page_index, display_page_label, width, height, rotation
  extraction_route: native | hybrid_ocr | full_ocr | docling_recovery | skip_blank
  parser_name, parser_profile, decision_status, publishable
  raw_artifact_ref, decision_reasons, coordinate_normalizations
Element
  element_id, type, text, normalized_text
  page_index, bbox, coordinate_origin, reading_order
  heading_path, clause_path, parent_id, children_ids, confidence
Table
  element_id, caption, html, markdown
  cells[row, col, row_span, col_span, text, bbox]
  unit_context, footnotes
```

同时保存原始解析器 JSON、模型/包版本、每页路由、质量门控原因和坐标变换。Markdown 是阅读派生产物，不能作为唯一解析事实。

### 4.4 页面级解析路由

1. 有效文字层且质量检查通过：PyMuPDF/PyMuPDF4LLM 原生路径，保留 blocks/JSON、页码和 bbox。
2. 部分文字、部分图片文字或局部乱码：按页面或区域降级，不对整份 PDF 无条件 OCR。
3. 扫描页、文字层不可用或关键表格失败：PaddleOCR PP-StructureV3 专项路径。
4. Docling 仅作为结构恢复对照；若没有可测增益，不进入正式依赖。
5. 条款层级、表格上下文或坐标仍不可信：人工隔离，不以“抽到部分文字”为由发布。

路由阈值由代表页面实验形成，不在设计阶段拍脑袋确定。

### 4.5 语料发布与回滚

```text
1. 固定已审核 document_version 集合，生成 corpus manifest
2. 构建 canonical/chunk 产物和新的 chunks_<corpus_version> collection
3. 完成数量、hash、必填字段、抽样定位和离线检索检查
4. 创建 collection snapshot，保存模型、tokenizer、参数和产物清单
5. 原子切换 corpus_current alias
6. 运行线上冒烟测试并记录发布结果
7. 失败时 alias 切回上一 collection，保留失败版本供诊断
```

Qdrant snapshot 不包含 alias；恢复时必须根据 release manifest 重建 alias，并通过实际恢复演练证明可用。

## 5. 证据与检索设计

### 5.1 稳定证据与派生检索块

- `EvidenceUnit` 具有稳定 ID、原文定位和明确使用策略：`answer_and_citation` 可支持回答主张，`source_locator_only` 只允许返回原文入口和质量提示。
- `RetrievalChunk` 是为召回优化的派生视图，可以包含标题路径、父条款摘要、表头或相邻上下文，但必须引用一个或多个 `EvidenceUnit`。
- 解析隔离不等于搜索隐藏。文档页码和主题定位可信但公式/表格转写不可信时，建立 `source_locator_only` 单元；只有无法建立可靠来源定位时才完全排除。
- M3 v1 对批准代表页采用“一份 EvidenceUnit 对应一份 RetrievalChunk”的可解释基线；当前离散页面的标题关系标记为 `page_local_inferred`，不冒充跨页完整条款树。
- v1 只在 Canonical 元素边界处分段，600 字上限是可版本化实验配置，必须由后续 Golden Set 结果批准或调整。
- 生成模型只能引用 `EvidencePack` 中使用策略为 `answer_and_citation` 的证据 ID，不能引用临时向量结果、`source_locator_only` 转写或自己生成的伪条款号。
- 数值表格块必须携带标题、表头、单位、脚注和统计时段；不能只索引单个单元格数值。

### 5.2 检索路径

```text
QueryRequest
  -> 显式定位解析（文档号/标准号/条款号）
  -> scope 识别（地域/时间/对象/文件层级/统计时段）
  -> 必要时 needs_clarification
  -> 结构化过滤
  -> dense + 中文词法候选召回
  -> RRF（基线候选）
  -> 可选 cross-encoder rerank
  -> 适用性标注与分组
  -> EvidencePack
```

首期保留三个可比较基线：dense-only、BM25-only、dense+BM25 RRF。RRF 和 reranker 不预设一定提升，必须使用同一语料版本和 Golden Set 对比。

地方文件不简单乘一个固定加分。系统先判断地域、时间、对象和文件性质，再分别展示适用的地方依据与补充的国家依据；不能把“地方优先”错误实现成无条件排序常量。

### 5.3 Qdrant 单库基线与模块边界

- Qdrant collection 保存 named dense/sparse vector、稳定 chunk ID 和结构化 payload。精确 dense 基线使用 `exact=true` 全扫描，默认 ANN 作为上线性能模式；两者必须使用相同向量和距离度量进行对照。
- multilingual BM25/sparse、过滤、RRF、alias 和 snapshot 直接通过 `qdrant-client` 验证，不强行塞进 LangChain `VectorStore` 的最小接口。
- 项目只提供一个小而稳定的 Qdrant 检索模块，对上暴露索引构建和按模式/条件查询；collection、named vector、Query API 和过滤表达式留在模块内部。在出现第二个真实后端之前，不为 FAISS/Weaviate 预建通用 adapter 层。
- NumPy 可在自动测试中对极小样本计算 cosine 作为结果校验器，但不保存生产索引，也不构成第二套检索系统。
- 中文 BM25 必须显式配置和测试，不能照搬默认英文分词。若标准号、条款号、专业词和中文混合检索在 Golden Set 上不能达标，再评估 Elasticsearch + SmartCN，而不是继续堆提示词。
- 原方案引入 FAISS `IndexFlat`，目的是用精确全量比较隔离 ANN 近似误差，并非认为 FAISS 的 cosine/dot/L2 计算天然更准确。确认 Qdrant 可执行精确全扫描后，为减少重复索引、依赖和运维路径，TD-03 于 2026-09-05 收敛为 Qdrant 单库方案。

## 6. 在线查询与回答契约

### 6.1 公共应用接口

```python
class QueryApplicationService:
    async def execute(self, request: QueryRequest) -> AnswerResult: ...
    async def stream(self, request: QueryRequest) -> AsyncIterator[QueryEvent]: ...
```

`QueryRequest` 至少包含问题、显式 `conversation_context`、具体或 current 的 `corpus_version`、`request_id` 和 `caller_type`。

`AnswerResult` 至少包含：

```text
query_id
status: answered | needs_clarification | search_only | conflicting_sources | refused
resolved_scope + missing_conditions
answer.claims[claim_id, text, evidence_ids]
evidence[evidence_id, quote, document_version, locator, applicability, source_uri]
corpus_version
warnings
```

网页 DTO、REST JSON 和 MCP structured content 只能无损映射该结果。FastAPI `Request`、MCP context、LangChain `RunnableConfig` 和 LangSmith run ID 不进入领域实体。

### 6.2 受控分支

- `answered`：证据充分且关键条件明确。
- `needs_clarification`：缺少决定性地域、时间、对象或统计时段；返回结构化缺失条件，不生成假定答案。
- `search_only`：存在相关文档，但证据不足以形成可靠综合回答；包括命中 `source_locator_only` 时返回文档、页码、原文入口和质量提示，对应界面的证据卡片降级状态。
- `conflicting_sources`：文件要求存在差异；逐份展示，不替用户作合规判断。
- `refused`：问题越界、语料无依据或要求系统直接作专业合规结论。

结果状态由核心服务生成，前端不得通过解析回答文本猜测。

### 6.3 引用校验

最终结果发布前至少执行确定性检查：

1. 每个规范性 claim 至少绑定一个当前 `EvidencePack` 中的 evidence ID。
2. evidence 的文档版本属于本次具体 `corpus_version`。
3. 引用定位包含页码或等价位置；未知字段显式暴露。
4. 数值 claim 的证据同时包含单位、表头/条件和必要注释。
5. 回答中出现的条款号、文档名和数字能在绑定证据中找到或由可审计规则导出。

语义支持性由人工评测为准，LLM judge 只能辅助发现疑似问题。

## 7. 框架与入口边界

### 7.1 LangChain

首期采用：

- `ChatModel.with_structured_output(Pydantic, include_raw=True)`：条件识别和回答草稿；
- `Runnable`：少量模型子流程的组合、重试边界、事件和 tracing；
- `Document`：解析/检索生态边界的数据交换；进入核心前立即转成项目证据对象；
- 可选 `BaseRetriever` adapter：只有实际需要接入 Runnable/LangSmith 时才包装。

首期不采用：

- 用 LangChain `Document.metadata` 充当权威文档模型；
- 用一条巨型 LCEL 链隐藏查询状态机；
- 让 LangChain 管理语料发布、适用性或引用不变量；
- 在没有真实循环、持久化恢复和人工中断需求时使用 LangGraph。

### 7.2 FastAPI 与流式体验

- `POST /api/v1/queries` 返回完整、已校验 `AnswerResult`。
- `POST /api/v1/queries:stream` 是实验接口，返回阶段事件和最终结果；默认不把未校验 token 当正式答案展示。
- 流式最终 payload 必须与非流式结果语义等价；客户断开需验证取消能传递到模型和检索调用。
- 若真实延迟较短或取消/代理缓冲复杂度高于体验收益，首期只发布 JSON。

### 7.3 MCP

核心稳定后增加三个只读候选能力：`search_evidence`、`answer_with_evidence`、`get_document_evidence`。

MCP adapter 返回对象根的结构化结果、简短文本兼容内容和证据 ResourceLink；它只做协议映射，不直接访问向量库，不另写一套提示词和回答逻辑。追问条件由调用方显式传递或使用应用级 handle，不依赖连接内隐状态。SDK/协议版本在实现 MCP 增量时锁定并验证，当前方案不依赖尚未做兼容测试的特性。

### 7.4 LangSmith

- 可选启用开发 trace 和离线实验比较；记录 corpus、prompt、retrieval、parser 和 model 版本。
- 权威 Golden Set 保存在仓库；本地查询记录是审计事实源。
- 公开来源已确认的材料可按产品约束进入云端实验；未知来源和未来内部手册默认不上传。
- 关闭网络或 LangSmith 后，核心查询、测试和本地复现必须仍能运行。

## 8. 目标模块边界

| 深模块 | 公共接口 | 隐藏的复杂性 |
| --- | --- | --- |
| `CorpusLifecycle` | register、process、build、publish、withdraw、rollback | 文档状态、幂等、解析路由、质量门控、多产物一致性、alias 与恢复 |
| `EvidenceQuery` | execute/stream，返回一种 `AnswerResult` | 条件识别、精确/混合检索、适用性、证据充分性、受控分支和引用校验 |
| `EvidenceSource` | 按稳定 evidence ID 获取原文与定位 | 条款树、父子上下文、表格、页码/坐标、来源与文档版本 |

Parser、Embedding、Reranker、VectorStore 先作为上述深模块的内部 adapter。只有出现第二个真实实现且调用方确实需要替换时，才提升为公共 seam。

```text
src/domain/          # 文档、证据、查询、结果状态和不变量
src/application/     # CorpusLifecycle、QueryApplicationService、验证与 ports
src/adapters/        # parser、qdrant、llm/langchain、sqlite、fastapi、mcp、langsmith
src/evaluation/      # 数据/检索/回答/拒答/运行评估
```

该结构是职责图，实施时不机械地一次创建所有目录。

## 9. 验证框架

### 9.1 五层评估

| 层级 | 核心问题 | 主要方法 |
| --- | --- | --- |
| 数据与解析 | 文字、层级、表格、页码和坐标是否可信 | 人工锚点 + 确定性质量检查 + 可视化叠加 |
| 检索 | 正确证据是否进入候选并排在前面 | Recall@k、MRR/nDCG、按问题类型切片 |
| 回答与引用 | 重要主张是否被绑定证据直接支持 | 规则校验 + 人工复核 + 辅助 LLM judge |
| 追问与拒答 | 条件不足、无覆盖、冲突和越界是否走对分支 | 场景标签、状态准确性、人工判断 |
| 系统与运维 | 是否可复现、可恢复、资源可接受 | trace、耗时、错误、成本、发布/回滚/恢复演练 |

不得用单一 Ragas 总分替代这五层证据；Ragas 如使用，只是回答/检索评估的一个可替换工具。

### 9.2 批准技术栈前的最小实验

| 实验 | 样本 | 必须保存的证据 |
| --- | --- | --- |
| E-01 解析与定位 | 6～8 份文档、30～50 页、20 个层级锚点、10 个表格片段 | parser/model/config、输出 JSON、人工标注、bbox 图、耗时/内存、失败原因 |
| E-02 中文混合检索 | Golden Set v1；Qdrant 的 exact dense、multilingual BM25/sparse、ANN 与 RRF | 相同 embedding/chunk/查询口径，语料版本、token/命中、候选与分数、分类型指标、资源和失败案例 |
| E-03 发布与恢复 | corpus v1/v2 | manifest、alias 切换、回滚、snapshot 恢复、冒烟结果 |
| E-04 LangChain 边界 | 10～20 个正常/缺条件/无覆盖问题 | Pydantic 解析、raw/error、trace、领域对象转换无损性 |
| E-05 API/SSE/MCP 一致性 | 同一批固定请求 | 直接调用/JSON/流式 final/后续 MCP 的语义一致结果及取消测试 |

阈值在人工基线和最小基线测出后确定，不在设计阶段伪造准确率或 P95。

## 10. 部署、观测与恢复

POC 目标为单机运行：应用服务、SQLite、原始 PDF、解析产物和评估报告均使用明确的持久化目录；Qdrant 用 Docker Compose 启动并使用明确的数据卷。开发测试可以使用 Qdrant local/in-memory 形态，但部署与恢复结论必须来自实际服务进程。模型 API 密钥使用环境变量或 secret，不写入 Git。

每次查询记录 `query_id`、输入问题、显式会话条件、具体语料版本、实际 collection、parser/chunk/embedding/BM25/reranker/prompt/model 版本、阶段候选 ID/分数、过滤/降级原因、最终证据、claim 映射、状态、时延、token/成本和错误。

每次发布记录 manifest、collection snapshot、SQLite/文件备份和恢复说明。只有成功演练“新版本发布—回滚—从备份恢复”后，才能声称支持回滚和恢复。

## 11. 中等粒度实施计划

| 任务 | 可观察结果与覆盖需求 | 主要接口、依赖与排除项 | 验收证据 |
| --- | --- | --- | --- |
| M1 文档登记与首批样本确认 | 6～8 份代表文档被登记、分级并组成可复现实验样本清单 v0；覆盖 R-08、R-12、NFR-01/02/07 | `CorpusLifecycle.register` 与 SQLite/文件产物；依赖来源判断；不做全文解析和向量化 | schema/迁移测试、SHA-256 去重例子、状态迁移测试、样本清单、来源与隔离理由 |
| M2 解析/OCR 与 Canonical Document | 代表页面按质量选择 PyMuPDF 原生路径或 PaddleOCR 降级，Docling 只作结构对照；覆盖 R-05、R-07 | `CorpusLifecycle.process`、`EvidenceSource` 的定位基础；依赖 M1；不做最终检索优化 | E-01 原始输出、归一化 JSON、质量报告、bbox 叠加图、资源记录和 TD-01 结论 |
| M3 证据单元与检索基线 | 条款/表格可稳定回查；Qdrant exact dense、中文词法、ANN 和 RRF 在同一口径比较；覆盖 R-01/02/04/05 | `EvidenceSource` 与内部 Qdrant retrieval module；依赖 M2 和 Golden Set v1；不生成回答 | E-02 分类型指标与失败案例、过滤测试及 Qdrant alias/snapshot 恢复记录 |
| M4 证据查询核心 | `QueryApplicationService` 返回五种受控状态、EvidencePack 和通过校验的引用；覆盖 R-03/04/06/08/13/15/16 | `EvidenceQuery.execute/stream`、LangChain 模型子流程；依赖 M3；不做独立入口业务逻辑 | 契约测试、缺条件/冲突/无覆盖场景、claim-evidence 校验、Golden Set 首轮回归和 E-04 |
| M5 证据工作台与 FastAPI | 连续追问、内联引用、右侧原文与 search-only 降级通过同一核心工作；覆盖 R-14、R-18 | REST/可选 SSE adapter；依赖 M4 和已确认原型；不新增另一套检索逻辑 | API/E2E 测试、代表场景录屏或截图、直接调用与 JSON/stream final 一致性及取消实验 |
| M6 评估、运行与 Agent 适配 | 五层评估、单机运行、发布恢复和 MCP 只读复用形成可复现证据；覆盖 R-11、R-12、NFR-04/05/06 | 部署与 MCP adapter；依赖 M1～M5；不宣称未测的生产规模 | 版本化评估报告、启动与恢复演练、限制说明、至少一个真实 Agent host 的契约一致性测试 |

每个里程碑按“规格/验收例子 → 实现 → AI 自检 → 人工 review → 回归证据”推进，不拆成按文件逐项创建的微型 ticket。

## 12. 需求追踪

| 产品需求 | 主要设计位置 | 主要验证 |
| --- | --- | --- |
| R-01、R-02 | 5.2 精确 + 混合检索 | E-02 Golden Set 检索切片 |
| R-03、R-05、R-07 | 4.3、5.1、6.3 | E-01 表格/定位 + claim-evidence 规则与人工复核 |
| R-04、R-13、R-15 | 5.2、6.1、6.2 | 缺条件、地域和历史时间场景测试 |
| R-06、R-16 | 6.2 受控分支 | 状态测试 + search-only UI 验收 |
| R-08、R-12 | 4.1、4.2、4.5、10 | 发布、回滚、恢复和历史查询复现 |
| R-10 | EvidencePack 按文档分组 | M4 后的跨文档增强测试 |
| R-11 | 6.1、7.3 | API/MCP 语义一致性测试 |
| R-14、R-18 | 6.1、7.2、M5 | 连续追问与证据工作台验收 |
| NFR-01、NFR-02、NFR-07 | 4、10 | 文档隔离、版本复现、发布/撤回与重建测试 |
| NFR-03、NFR-08 | 6.2、7.4、10 | 越界拒答、公开来源门控、secret 与外传边界检查 |
| NFR-04、NFR-05、NFR-06 | 9、10 | 五层评估、资源、时延和成本报告 |

## 13. 当前需要人工参与的事项

实现过程中不需要手工编程，但以下领域判断不能完全外包给 AI：

1. 已完成：确认 8 份 M2 解析实验文档。该批准只授予实验资格，是否进入正式种子语料仍由逐份来源和文件一致性核验决定。
2. 与 AI 一起标注约 20 个条款/层级定位锚点和 10 个表格/数值片段；AI 可以预填，再由人工确认或纠正。
3. 为初版 Golden Set 增补约 8～15 个真实或接近真实的业务问题，并标明哪些来自本人经验、哪些由 AI 生成。
4. 在开源发布前确认仓库许可证与 PyMuPDF 等依赖的兼容方式；这项检查不阻塞 M1～M3 的本地实验，但阻塞公开发布。

## 14. 评审结论

本轮已把此前讨论收敛为以下方案：

1. 项目开源，PyMuPDF 作为文字层主路径；许可证兼容性在公开发布前单独检查。
2. 原始文件、不可变处理产物和 SQLite 控制记录保存业务事实；Qdrant 是可删除重建的查询加速索引，不是文档准入和语料发布的事实源。
3. Qdrant 已被选为首期唯一检索数据库；以 `exact=true` 建立 dense 精确标尺，并在同库验证 ANN、中文词法、RRF、过滤、发布与恢复。FAISS/Weaviate 不进入正式依赖，中文词法验收失败时再评估 Elasticsearch + SmartCN。
4. LangChain 窄用、FastAPI 先行、MCP 后置；先用 6～8 份文档完成闭环，再扩充语料。

技术方案已于 2026-09-03 获批准，2026-09-04 明确拆分文档准入、处理运行和语料版本三类独立状态，并于 2026-09-05 将 TD-03 从多候选实验收敛为 Qdrant 单库方案。组件选定不等于效果已经验收；M3 仍须用 Golden Set v1 保存精确 dense、ANN、中文词法、RRF、过滤和恢复证据。后续若改变产品范围或架构边界，应记录原因和验证影响，不静默覆盖已批准结论。

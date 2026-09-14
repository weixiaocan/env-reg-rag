# 检索与存储方案调研

> 调研日期：2026-09-03  
> 适用项目：排水规范证据助手  
> 来源边界：仅使用截至调研日可核验的官方文档、官方 GitHub 仓库和官方许可证。本文不引用测评文章，不根据营销表述推断性能。

> **决策更新（2026-09-05）**：本文保留的是选型过程和当时的候选比较。项目后续确认 Qdrant 可用 `exact=true` 提供全扫描精确 dense 基线，因此不再为该基线单独引入 FAISS；TD-03 已收敛为 Qdrant 单库方案。当前有效实施决策以 `docs/TECH_SPEC.md` v1.2 为准，本文后续的“FAISS + 多候选”内容仅作为历史决策依据。

## 1. 结论

没有可靠证据证明 Qdrant 是“当前使用最多”的向量数据库。GitHub star、云厂商客户数、下载量和问卷口径都不能直接代表企业实际使用量，因此本项目不以流行度作为选型结论。Qdrant 是服务型实验候选之一，不是行业排名第一或已经胜出的声明。

### POC 实验建议

**先用 FAISS `IndexFlat` 建立 dense 精确基线，再让 Qdrant 与 Weaviate 在同一语料、chunk、embedding、问题集和评价口径下竞争服务型检索库；若中文词法是主要瓶颈，以 Elasticsearch + SmartCN 替换 Weaviate。** 原始 PDF、文档登记和不可变处理产物不存进任何候选检索索引作为唯一事实。

建议的首期形态：

```text
原始 PDF + 校验和 + 文档/语料版本清单（事实源）
                    │
          Canonical Document / chunks
                    │
       ┌────────────┴────────────┐
       ▼                         ▼
FAISS IndexFlat           服务型候选对比
dense 精确基线        Qdrant / Weaviate / 条件式 Elastic
       └────────────┬────────────┘
                    ▼
       同一检索评测 → 选型与失败案例
                    ▼
      应用检索器 → LangChain 子流程 → 回答/API/MCP
```

Qdrant 值得进入实验，不等于它性能最好。本调研没有足够的一手基准证明这一点。它的候选价值在于一个较轻的单节点服务同时提供本项目需要验证的 dense+sparse、服务器端 BM25、多语言 tokenizer、RRF、结构化 payload 过滤、原子 collection alias、collection snapshot、Python 客户端和 LangChain 集成。官方本地快速开始只需一个带持久化卷的 Docker 容器。[Qdrant Local Quickstart](https://qdrant.tech/documentation/quick-start/)

**中文 BM25 是有条件推荐，不是已经验收。** Qdrant 官方 BM25 文档明确指出默认英文处理不适合其他语言，并提供 `multilingual` tokenizer 和中文示例；官方教程又说明 core BM25 可运行在任意 Qdrant 实例。[Qdrant Full-Text Search](https://qdrant.tech/documentation/search/text-search/full-text-search/)、[Qdrant Multi-Representation Search](https://qdrant.tech/documentation/tutorials-search-engineering/multi-representation-search/) 但 LangChain 示例使用的 `FastEmbedSparse("Qdrant/bm25")` 路径不能直接当成中文方案：FastEmbed 当前官方源码列出的 BM25 语言中没有中文，且 Qdrant 文档说明 FastEmbed 不支持服务器端示例中的 tokenizer 选项。[FastEmbed BM25 source](https://github.com/qdrant/fastembed/blob/main/fastembed/sparse/bm25.py)、[Qdrant LangChain integration](https://qdrant.tech/documentation/frameworks/langchain/)

因此测试 Qdrant 时应直接用 `qdrant-client` 调用服务器端 BM25/multilingual 与 Query API，再在应用边界按需包装为 LangChain Retriever；不要为了“体现 LangChain”而牺牲可验证的中文检索行为。

### 候选角色

1. **FAISS：dense 精确质量基线。** 用于回答“嵌入模型与 chunk 在不受 ANN 近似影响时能达到什么召回”，不独自承担 hybrid、过滤和服务运维。
2. **Qdrant：服务型候选 A。** 验证 multilingual BM25、hybrid、过滤、发布切换与恢复。
3. **Weaviate：服务型候选 B。** 用同一口径验证 BM25 + vector hybrid、metadata filter、备份和版本切换。
4. **Elasticsearch：条件式词法候选。** 当中文专业词、标准号和条款号检索成为主要瓶颈时进入实验。
5. **PostgreSQL + pgvector：未来统一平台候选。** 当多人事务、关系模型和统一数据平台的重要性明显上升时复评。
6. **Chroma：教学型 dense 原型候选。** 当前不承担本项目正式检索底座。

### 补充候选：为什么没有直接选择其他常见向量数据库

| 候选 | 与本项目相关的能力 | 当前没有排在 Qdrant 前面的原因 |
| --- | --- | --- |
| Milvus | 开源、支持 dense/sparse、BM25、hybrid 和 RRF，也有 Lite/Standalone/Distributed 三种形态 | 更偏大规模向量基础设施；官方 Standalone 最低 8 GB、建议 16 GB RAM，当前开发机资源和小语料下收益不足以抵消运维与资源成本。若未来达到百万级向量或已有 Milvus 平台，应重新评估 |
| Weaviate | 开源、自托管 Docker，原生 BM25 + vector hybrid、metadata filter 和备份 | 是真实可行的第二候选，但其融合、中文 tokenization、语料版本原子切换需要用本项目问题验证；当前没有发现相对 Qdrant multilingual BM25 + RRF + atomic alias 的明确优势 |
| Pinecone | 托管服务，支持 dense、sparse、全文和 metadata filter | 不提供本项目需要的本地自托管开源部署，增加云依赖、成本和数据外发；适合公司已经采用 Pinecone 的情形 |
| FAISS | MIT 许可证，CPU/GPU 高效 dense 相似度搜索，支持精确和多种近似索引，也可序列化索引文件 | 它主要是向量搜索库而不是完整检索服务；metadata 通常需要外部映射，动态过滤主要依赖 ID selector，BM25/hybrid、并发服务、发布 alias 和审计需由应用补齐。适合作为 dense 精确基线或嵌入式方案，不单独承担本项目检索数据库职责 |
| Elasticsearch | 强全文检索、SmartCN、hybrid 和复杂搜索能力 | 对 23 份种子 PDF 运维偏重，且目标发行版的 RRF 功能层级和许可证要额外核对；若中文词法实验失败，它是优先替代方案 |
| PostgreSQL + pgvector | 事务、关系数据和向量统一，便于审批、版本与审计 | PostgreSQL core 中文 FTS 和 RRF 需要项目自己补齐；当公司已有 PostgreSQL 平台或强事务要求上升时更有吸引力 |
| Chroma | 嵌入式、本地启动最简单，LangChain 教程多 | 本地 OSS 的高级 hybrid/RRF/版本分支能力弱于其 Cloud 产品；适合 dense-only 教学原型，不符合当前发布与回滚压力 |

因此，下一步不是直接“定死 Qdrant”：先用 FAISS `IndexFlat` 建立不受 ANN 近似影响的 dense 精确基线，再在同一小语料上比较 Qdrant 与一个服务型第二候选。第二候选优先选 Weaviate；如果中文精确词法是主要瓶颈，则改选 Elasticsearch。Milvus 留在未来规模化复评清单。

## 2. 项目设计压力

来自已批准产品规格的关键约束是：

- 自然语言证据检索之外，还要精确命中文档名、标准号、条款号和数值术语；
- 必须按地域、发布机关、文件层级、日期、版本和效力状态约束候选证据；
- 每次查询要绑定明确语料版本并可复现；
- 新语料应先构建、检查，再发布；失败版本不能污染当前检索；
- 已发布版本能够撤回或回滚；
- 单机 POC 要能运行和演示，但不能把不可演进的玩具路径伪装成上线方案；
- LangChain 要被实际使用，但领域元数据、发布状态与引用逻辑不能被框架默认值吞掉。

据此，比较重点不是单独的 ANN 能力，而是“中文词法召回 + dense + 融合 + 过滤 + 版本发布 + 恢复”的完整链路。

## 3. 总体比较

说明：表中的“事实”来自后文官方来源；“项目判断”是针对本项目作出的推断，不代表产品官方承诺。

| 维度 | Qdrant | Elasticsearch | PostgreSQL + pgvector | Chroma |
|---|---|---|---|---|
| 中文 BM25 / 词法 | **事实：**服务器端 BM25 支持 `multilingual` tokenizer，并有中文示例；必须显式覆盖默认英文配置 | **事实：**BM25 是默认 similarity；官方 Smart Chinese 插件支持简体中文和中英混合分词 | **事实：**PostgreSQL 有 FTS 与排名，但不是原生 BM25；core 只有一个内置 parser，中文质量需要额外验证或外部分词/扩展 | **事实：**内置 BM25 使用 `snowballstemmer`；官方没有给出中文适配保证；本地 legacy FTS 是 contains/regex 过滤，不等于 BM25 排序 |
| dense+sparse hybrid | **事实：**同一 point 支持 named dense/sparse vectors；Query API 可组合多路召回 | **事实：**一个请求可组合 full-text 与 vector search | **事实：**pgvector 官方建议与 PostgreSQL FTS 组合；融合 SQL/应用自己负责 | **事实：**Cloud Search API 支持 sparse+dense；单节点当前不支持该 Search API |
| RRF / 融合 | **事实：**Query API 原生 RRF、weighted RRF、DBSF | **事实：**原生 RRF retriever；官方推荐用于 hybrid；功能层级需按具体发行版/订阅核对 | **事实：**pgvector 文档建议 RRF 或 cross-encoder，但没有提供数据库内置一键 RRF 接口 | **事实：**Cloud Search API 原生 RRF；单节点暂无同等官方接口 |
| metadata filtering | **事实：**payload filter 与 payload index | **事实：**mapping + keyword/date/numeric + filter context | **事实：**关系列、B-tree/GIN、JSONB/JSONPath | **事实：**`where` 支持比较、in、and/or 与数组；不支持嵌套数组 |
| 结构化元数据 | JSON payload，适合检索侧反规范化副本 | JSON document/mapping，搜索建模强 | 关系约束 + JSONB，最适合作为规范化事实库 | record metadata，结构和嵌套能力相对有限 |
| 本地持久化 | Docker volume；也有 Python local on-disk 模式 | 索引持久化；单节点可 Docker/本机部署 | 数据库原生持久化与 WAL | `PersistentClient` 自动落盘 |
| 版本发布 / alias | **事实：**collection alias 可原子切换 | **事实：**index alias 支持单次原子 add/remove | **项目实现：**语料版本列 + current pointer/view，在事务中切换 | **事实：**Cloud 有 collection fork；单节点不支持 fork；未找到官方本地原子 alias 方案 |
| backup / rollback | collection/full snapshot；注意 collection snapshot 不含 alias | snapshot repository、SLM、restore | `pg_dump`、base backup、WAL/PITR；也可应用层切换语料版本 | 本地可复制可重建数据，但截至调研日未找到与前三者等价的官方单节点 snapshot/restore 机制 |
| Python / LangChain | 官方 Python client；`langchain-qdrant` 支持 dense/sparse/hybrid，但中文 BM25 路径需绕开默认 FastEmbed | 官方 Python client；`langchain-elasticsearch`/ElasticsearchStore | `langchain-postgres`；新版 `PGVectorStore` 可接表结构，但复杂 hybrid 仍可能需要自定义 SQL Retriever | `langchain-chroma`；本地和 Cloud 均可接入，但本地高级 hybrid 能力受限 |
| 自托管许可证 | Apache-2.0 | 源码默认 AGPLv3/SSPLv1/ELv2 三选一，X-Pack 为 ELv2；默认发行版与功能层级需额外核对 | PostgreSQL License；pgvector 同许可证 | Apache-2.0 |
| 单机运维判断 | **中低：**一个专用搜索容器、卷、快照脚本 | **中高：**JVM 服务；中文需安装插件并重启；许可证/安全/快照配置更多 | **中：**一个通用数据库，但 schema、中文词法与融合 SQL 由项目负责 | **低：**嵌入式最简单；但补齐本项目发布与 hybrid 需求会把复杂度转移到应用层或 Cloud |

“运维判断”是相对本项目 POC 的项目推断，不是厂商性能或成本排名。

## 4. 候选核验

### 4.1 Qdrant

#### 已核验事实

- collection 中的 point 可同时保存多个 named vector，包括 dense 与 sparse；payload 可保存 JSON 结构化元数据并参与过滤。[Qdrant Overview](https://qdrant.tech/documentation/overview/)、[Payload](https://qdrant.tech/documentation/concepts/payload/)
- Query API 支持多路 prefetch、RRF 和 DBSF；RRF 可融合 dense 与 sparse 结果。[Hybrid Queries](https://qdrant.tech/documentation/search/hybrid-queries/)
- Qdrant 提供服务器端 BM25。非英语数据需要覆盖默认英文处理；`multilingual` tokenizer 支持非拉丁字母和不以空格分词的语言，官方示例使用中文人名。[Full-Text Search](https://qdrant.tech/documentation/search/text-search/full-text-search/)
- 官方教程明确说 core BM25 可在任意 Qdrant 实例运行；自托管 dense embedding 则应由客户端生成。[Multi-Representation Search](https://qdrant.tech/documentation/tutorials-search-engineering/multi-representation-search/)
- payload 字段可建立索引；官方建议对频繁过滤字段建立 payload index。[Qdrant Fundamentals](https://qdrant.tech/documentation/faq/qdrant-fundamentals/)
- collection alias 可在后台构建新 collection 后原子切换，官方直接把它用于向量版本升级场景。[Collections and Aliases](https://qdrant.tech/documentation/manage-data/collections/)
- collection snapshot 包含 collection 配置、points 和 payload，但**不包含 alias**；恢复版本兼容也有限制，因此 alias 状态必须另行记录。[Snapshots](https://qdrant.tech/documentation/operations/snapshots/)
- Docker 挂载目录可持久化数据；默认实例没有加密或认证，不能把开发配置直接暴露到网络。[Local Quickstart](https://qdrant.tech/documentation/quick-start/)
- 官方 LangChain partner package 支持 dense、sparse、hybrid，本地磁盘和服务端连接。[LangChain integration](https://qdrant.tech/documentation/frameworks/langchain/)
- Qdrant OSS 使用 Apache License 2.0。[Qdrant GitHub](https://github.com/qdrant/qdrant)

#### 项目推断

- 它最适合承担“可重建检索索引”，不应单独承担文档来源审批、复杂版本关系和查询审计的唯一事实库。
- payload 应保存用于过滤和证据显示的反规范化字段，例如 `document_id`、`document_version_id`、`corpus_version`、`jurisdiction_code`、`authority`、`document_type`、`validity_status`、`effective_from/to`、`article_path`、`page_start/end`、`source_url` 和 checksum。
- 每次语料发布创建新 collection，而不是直接覆写在线 collection；评估通过后切换 `corpus_current` alias。回滚就是把 alias 原子切回上一个 collection。
- snapshot 是灾难恢复手段，alias 回切是应用发布回滚手段，两者不能互相替代。

#### 风险

- 中文 BM25 multilingual tokenizer 的实际召回质量尚未在本项目法规术语、标准号、条款号和数字单位上验证。
- LangChain 的现成 Qdrant 示例容易让人误用 FastEmbed BM25 默认英文路径。该限制必须由集成测试固定下来。
- 若后续需要复杂布尔查询、短语邻近、同义词治理、可解释 highlight 和多字段 BM25 调权，Qdrant 不是专用全文搜索引擎；Qdrant 官方也说明其不会发展完整 query analyzer/NLP 工具链。[Qdrant Fundamentals](https://qdrant.tech/documentation/faq/qdrant-fundamentals/)

### 4.2 Elasticsearch

#### 已核验事实

- Elasticsearch 的 text 字段默认使用 BM25 similarity。[Similarity settings](https://www.elastic.co/docs/reference/elasticsearch/index-settings/similarity)
- Smart Chinese Analysis 插件使用 Lucene Smart Chinese，为简体中文和中英混合文本分词；必须在每个节点安装并重启，且其 analyzer 不可配置。[Smart Chinese Analysis plugin](https://www.elastic.co/docs/reference/elasticsearch/plugins/analysis-smartcn)
- Elasticsearch 可在同一请求中组合全文和向量检索，官方推荐 RRF 进行 hybrid 融合。[Hybrid search](https://www.elastic.co/docs/solutions/search/hybrid-search)、[RRF reference](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion)
- mapping 支持 text、keyword、date、numeric 等结构化字段；filter context 适合地域、状态和日期等精确过滤。[Field data types](https://www.elastic.co/docs/reference/elasticsearch/mapping-reference/field-data-types)、[Query and filter context](https://www.elastic.co/guide/en/elasticsearch/reference/current/query-filter-context.html)
- index alias 支持在一个原子操作中 add/remove，可用于无停机切换新索引。[Aliases](https://www.elastic.co/guide/en/elasticsearch/reference/current/aliases.html)
- snapshot/restore 支持备份运行中集群；自托管需要先配置 snapshot repository，可用 SLM 自动执行和保留。[Snapshot and restore](https://www.elastic.co/docs/deploy-manage/tools/snapshot-and-restore)、[Manage repositories](https://www.elastic.co/docs/deploy-manage/tools/snapshot-and-restore/manage-snapshot-repositories)
- Elastic 官方提供 ElasticsearchStore、self-query retriever 和 ElasticsearchRetriever 的 LangChain 集成路径。[LangChain integration](https://www.elastic.co/search-labs/integrations/langchain)
- Elasticsearch 源码默认是 AGPLv3、SSPLv1、ELv2 三许可证，X-Pack 仅 ELv2；默认发行版继续使用 ELv2。官方订阅表把 RRF 单列为功能项，因此不能只看 API 文档就假定目标部署免费可用，应按锁定版本与许可证验证。[Repository LICENSE](https://github.com/elastic/elasticsearch/blob/main/LICENSE.txt)、[Licensing FAQ](https://www.elastic.co/pricing/faq/licensing/)、[2026-05 subscription matrix](https://www.elastic.co/pdf/subscriptions-2026-05-05.pdf)

#### 项目推断

- 如果首期评测证明中文词法召回是主要瓶颈，或者需要精细 analyzer、短语、同义词、highlight、多字段权重和复杂查询解释，Elasticsearch 应上升为首选。
- 对 23 份左右的种子 PDF，Elastic 的能力余量不是采用它的充分理由。SmartCN 插件、JVM 服务、证书/账号、快照库和许可证核对都会增加 POC 的非业务工作。
- 如果 native RRF 在选定发行版中不满足免费使用条件，可分别执行 BM25 与 kNN，再由应用实现并测试 RRF；但这会失去部分单请求原生便利，必须记录为自研排序逻辑。

### 4.3 PostgreSQL + pgvector / 全文检索

#### 已核验事实

- PostgreSQL core 提供 `tsvector`、`tsquery`、`ts_rank`/`ts_rank_cd`、GIN 全文索引和可配置 parser/dictionary；这些事实不等于提供 BM25。[Controlling Text Search](https://www.postgresql.org/docs/current/textsearch-controls.html)、[Preferred Index Types](https://www.postgresql.org/docs/current/textsearch-indexes.html)
- PostgreSQL 当前只有一个内置全文 parser；自定义 parser 需要底层函数并要求 superuser。官方文档没有给出等价 SmartCN 的 core 中文分词保证。[Parsers](https://www.postgresql.org/docs/current/textsearch-parsers.html)、[CREATE TEXT SEARCH PARSER](https://www.postgresql.org/docs/current/sql-createtsparser.html)
- pgvector 支持 exact search、HNSW、IVFFlat、vector/halfvec/bit/sparsevec，并明确建议与 PostgreSQL FTS 组合做 hybrid，再用 RRF 或 cross-encoder 融合；RRF 不是 pgvector 提供的单独原生检索 API。[pgvector README](https://github.com/pgvector/pgvector)
- 过滤可以使用关系列及普通索引；JSONB 支持 GIN、包含和 JSONPath 查询。[JSONB indexing](https://www.postgresql.org/docs/current/datatype-json.html)
- pgvector 官方提醒：近似索引配合过滤时可能返回较少结果，0.8.0 起的 iterative scan 可继续扫描候选。[pgvector README](https://github.com/pgvector/pgvector)
- PostgreSQL 提供逻辑备份、base backup、WAL 连续归档与 PITR。[Continuous Archiving and PITR](https://www.postgresql.org/docs/current/continuous-archiving.html)
- `langchain-postgres` 是官方 LangChain 组织的独立包，提供 PGVectorStore；仓库同时提醒旧 `PGVector` 已弃用，应迁移到新版 PGVectorStore。[langchain-postgres](https://github.com/langchain-ai/langchain-postgres)
- PostgreSQL 和 pgvector 均使用宽松的 PostgreSQL License。[PostgreSQL License](https://www.postgresql.org/about/licence/)、[pgvector LICENSE](https://github.com/pgvector/pgvector/blob/master/LICENSE)

#### 项目推断

- PostgreSQL 最适合统一保存规范化文档登记、版本关系、处理状态、评估记录和查询审计；关系约束与事务是它相对专用向量库的核心优势。
- 若只使用 PostgreSQL，要先选择并验证中文词法路线，例如应用层稳定分词后写入 `tsvector`、采用可审核扩展，或自行实现 BM25 候选排序。任何路线都比 Qdrant multilingual BM25 或 Elasticsearch SmartCN 多一层项目责任。
- 发布可采用不可变 `corpus_version` + 一行 `current_release` 指针，并在事务中切换；这可以满足原子发布，但属于本项目 schema，不是 pgvector alias 功能。
- 若团队已经运营 PostgreSQL，且语料发布、权限、审计和其他业务数据强依赖事务一致性，那么“一库完成控制面与检索”可能比新增 Qdrant 更简单。

### 4.4 Chroma

#### 已核验事实

- Chroma 可用 `PersistentClient` 自动在本地落盘；LangChain 的 `langchain-chroma` 同时支持内存、本地持久化、服务端和 Cloud。[Chroma Clients](https://docs.trychroma.com/docs/run-chroma/cloud-client?lang=typescript)、[LangChain Chroma integration](https://docs.langchain.com/oss/python/integrations/vectorstores/chroma)
- 本地 legacy `query()` 提供 dense 相似度，并支持 metadata `where` 过滤；`where_document` 的所谓全文搜索是大小写敏感的 contains/regex 过滤，不是 BM25 排名。[Query and Get](https://docs.trychroma.com/docs/querying-collections/query-and-get)、[Full Text Search](https://docs.trychroma.com/docs/querying-collections/full-text-search)
- Chroma 的高级 Search API 支持 sparse vector、BM25、RRF 和复杂 ranking，但官方明确写明 **Chroma Cloud only**，单节点支持仍是未来计划。[Search API Overview](https://docs.trychroma.com/cloud/search-api/overview)、[Hybrid Search with RRF](https://docs.trychroma.com/cloud/search-api/hybrid-search)
- 官方内置 BM25 helper 使用 `snowballstemmer`；文档未声明中文分词质量或中文语言支持。[Chroma BM25](https://docs.trychroma.com/integrations/embedding-models/chroma-bm25)
- collection fork 可做写时复制和版本分支，但官方明确限定 Chroma Cloud；单节点 storage engine 不支持 fork。[Collection Forking](https://docs.trychroma.com/cloud/features/collection-forking)
- Chroma OSS 使用 Apache License 2.0。[Chroma GitHub](https://github.com/chroma-core/chroma)

#### 项目推断

- Chroma 很适合几行代码完成 dense-only 实验，但本项目把中文词法召回、版本发布、回滚和可审计语料作为首期设计压力；选择本地 Chroma 会迫使应用自己补齐这些能力。
- 选择 Chroma Cloud 可以获得官方 hybrid/RRF/fork，但这改变了“单机自托管 POC”的比较前提，并引入云区域、价格和外部依赖，不应与 OSS PersistentClient 混为同一方案。

## 5. 推荐 POC 的具体边界

### 5.1 数据职责

1. **原始事实源**：PDF 原文件、来源 URL、下载时间、SHA-256、人工登记字段和解析产物必须可以脱离向量库保存并重建。
2. **检索索引**：Qdrant 保存 chunk 文本、dense/sparse vector 和查询所需的反规范化 payload。
3. **控制记录**：首期可用 SQLite 或项目清单记录文档审批、语料版本、collection 名、alias 目标、embedding/BM25 配置和发布结果；出现多人并发、复杂事务或服务化后迁移 PostgreSQL。
4. **查询证据**：每次查询记录 `query_id`、原问题、规范化条件、`corpus_version`、实际 collection、检索参数、候选 ID/分数、rerank 结果和最终引用。

这里加入 SQLite 不是把它列为检索候选，而是避免让专用索引承担它不擅长的审批与审计事实源。若项目很快需要多用户并发，则直接使用 PostgreSQL 控制面。

### 5.2 发布与回滚

```text
1. 创建 chunks_<corpus_version>，禁止覆盖旧 collection
2. 写入 dense + BM25 sparse + payload
3. 做数量、字段、抽样定位和离线检索评估
4. 创建 collection snapshot
5. 记录 release manifest：文档集合、hash、模型、tokenizer、参数、snapshot
6. 原子切换 corpus_current alias
7. 线上冒烟测试
8. 若失败，alias 切回 previous collection；保留失败版本供诊断
```

由于 Qdrant collection snapshot 不包含 alias，恢复脚本必须从 release manifest 重建 alias。不能把“已经有 snapshot”写成“已经能一键恢复”的证明；恢复流程必须实际演练。

### 5.3 LangChain 的位置

- LangChain 用于 Document/metadata 传递、Retriever 接口、上下文组装、Runnable/链式编排和后续观测接入。
- Qdrant ingestion、server-side BM25 multilingual、hybrid Query API、alias 与 snapshot 直接使用官方 `qdrant-client`。
- 在项目内提供一个小而稳定的 `EvidenceRetriever` 适配器，把 Qdrant 结果转换成 LangChain `Document`，同时保留 chunk ID、语料版本、原始分数和定位元数据。
- 不把 LangChain VectorStore 的最小公共接口误当作本项目完整领域接口；发布、回滚和证据定位属于应用服务。

## 6. 必须先做的验证，不做性能承诺

选型批准前后应完成同一小语料上的可复现实验：

1. **中文 BM25 分词检查**：用 `雨污混接`、`入流入渗`、`20毫米`、`GB 50014`、`第5.2.3条`、发布机关和山西地域词，保存实际 token/命中结果。
2. **三个基线**：dense-only、BM25-only、dense+BM25 RRF；统一候选数和语料版本。
3. **过滤正确性**：地域、效力状态、日期、文档版本、正式/隔离状态不得越界。
4. **精确定位**：标准号、文号、条款号和数字单位问题单独统计，不被自然语言平均指标掩盖。
5. **发布演练**：构建 v2，alias 从 v1 切到 v2，再切回 v1；并验证进行中的查询没有读到混合版本。
6. **恢复演练**：从 snapshot 恢复新 collection，再根据 manifest 重建 alias。
7. **资源记录**：在同一机器记录索引时间、索引体积、内存、P50/P95 查询耗时，但只报告本项目实测值，不外推厂商性能。

通过门槛应在 golden set 初版完成后确定，不在本调研中编造 Recall@K 或时延数字。

## 7. 推翻当前推荐的条件

出现以下任一条件，应重新开选型决策，而不是维护既定结论：

| 推翻条件 | 优先重新评估 |
|---|---|
| Qdrant multilingual BM25 在本项目中文黄金问题上，标准号/条款号/专业词召回明显不足，且通过字段化精确检索仍不能解决 | Elasticsearch + SmartCN，或经审核的专用中文词法组件 |
| 需求增长为复杂布尔、短语邻近、同义词治理、多字段 BM25 权重、highlight 与检索解释 | Elasticsearch |
| 文档审批、版本关系、权限、查询审计必须与大量关系业务数据保持强事务一致，且团队希望只运维一个数据库 | PostgreSQL + pgvector；接受并验证中文 FTS/RRF 实现成本 |
| 首期目标退化为一次性 dense-only 演示，不要求 hybrid、alias、snapshot 和回滚 | Chroma PersistentClient |
| 必须使用 Chroma Cloud 已提供的 Search API/fork，且云依赖、区域与费用已获接受 | Chroma Cloud |
| 公司已有成熟 Elasticsearch 或 PostgreSQL 平台、监控、备份与人员经验 | 优先复用现有平台，再用相同评测集验证 |
| 单节点在实测语料量、并发或恢复目标下不满足要求 | 根据瓶颈评估 Qdrant 分布式/托管、Elastic 集群或 PostgreSQL HA；不能仅凭“数据变多”预选 |
| 许可证或采购政策不接受某候选的实际发行版/功能层级 | 排除该部署形态；特别重新核对 Elasticsearch native RRF 的目标版本许可 |

## 8. 决策状态

- **当前建议：**FAISS `IndexFlat` 固定为 dense 精确基线；Qdrant 与 Weaviate 作为服务型候选进入小样本验证，必要时将第二候选换成 Elasticsearch + SmartCN。
- **已解决：**POC 控制面使用文件产物 + SQLite；检索索引可以删除并由事实数据重建。出现多人并发和复杂事务后再评估 PostgreSQL。
- **尚未解决：**中文 BM25 实测、dense embedding、reranker、切分与表格索引策略，以及最终服务型检索库。
- **下一道门：**使用同一 Golden Set 完成 dense 精确、词法、hybrid、过滤和恢复实验后，才记录最终服务选型；任何候选失败均按第 7 节替换，而不是为既定组件调低标准。

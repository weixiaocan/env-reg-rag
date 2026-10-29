# 法规文档 RAG 智能问答系统

> 面向环境行业，以城镇排水与防涝领域的法规、政策、标准和技术资料为落地场景。

这是一个面向法规与标准类 PDF 的本地 RAG 系统，当前以环境行业中的排水领域为语料。项目从 PDF 开始，完成来源登记、原生解析与 OCR、结构化装配、分块、混合检索和证据约束回答，并把每条检索结果关联回原文页码与页面区域。

它适合用来搭建可追溯的专业资料问答原型，也可以作为 PDF 数据处理、混合检索和引用约束生成的工程参考。当前形态是单机或自托管 POC，重点放在数据链路、证据边界和可评估性。

在线试用：[https://weixiaocan.com/rag/](https://weixiaocan.com/rag/)

## 项目解决什么问题

普通 PDF 问答往往只提取连续文本。排水标准里的多级表头、公式、图注、条款层级和页码关系会在这个过程中丢失，模型最后给出的答案也很难回到原文核对。

这个项目把处理过程拆成三个彼此独立的层次：

- **资料治理**：记录文件来源、版本、效力状态、重复关系和发布版本。
- **文档结构化**：按页处理正文、表格、公式和图片，保留物理页码、坐标与处理状态。
- **证据问答**：使用向量和关键词混合检索，回答中的每句事实都必须标注来源，并能回到原文页面区域。

处理完成、内容质量通过、允许正式回答是三种不同状态。系统不会因为 OCR 成功返回，就自动把内容标记为正确或现行有效。

## 一份 PDF 如何变成可引用答案

```mermaid
flowchart LR
    A[原始 PDF] --> B[来源登记与文件审计]
    B --> C[逐页版面分析]
    C --> D[正文 表格 公式 图片]
    D --> E[page-intermediate 页面制品]
    E --> F[V2 Canonical 结构化文档]
    F --> G[结构感知分块]
    G --> H[中文向量与 BM25 索引]
    H --> I[Qdrant 混合检索]
    I --> J[上下文补全]
    J --> K[带来源标注的回答生成]
    K --> L[引用校验与原文定位]
```

这条链路的关键点是：检索块只是查询投影，V2 Canonical 元素和原 PDF 才是可回查的事实来源。

1. **来源登记**通过文件 SHA-256 识别内容，记录重复副本、同版本候选和来源核验状态。
2. **统一 PDF 处理**逐页执行版面分析。原生文字层合格时直接提取正文，表格、公式和图片仍继续处理；扫描页进入本地 OCR。
3. **V2 Canonical 装配**把页面结果整理为有顺序的元素流。每个元素保存类型化内容、页码、坐标、来源和关联关系。对照原文的核对结果在这一步按原识别内容的哈希绑定，对不上原内容时装配直接失败：公式与文字更正登记在 `data/registry/source-corrections.json`（119 个计算公式经负责人核对，91 个行内单位/化学式片段经 agent 对照原页核对），表格逐格核对登记在 `data/registry/table-checks.json`（239 张）。核对过的元素回答时不带“未核对”提示。
4. **结构感知分块**优先沿标题、条款和结构化元素边界切分，再按 token 预算处理过长正文。
5. **混合检索**同时使用 `BAAI/bge-small-zh-v1.5` 中文向量和 Qdrant BM25 稀疏检索，通过 RRF 合并排序。
6. **上下文补全**在命中表格或公式时，把同一条款的引导句、“式中”变量说明和注释一起交给模型。
7. **带来源标注的生成**让模型写一段自然的回答，句末用编号标注来源；应用层把编号映射回 evidence ID 并逐句校验，引用不存在的来源时整次回答作废。

## 核心能力

| 部分 | 当前能力 |
| --- | --- |
| PDF 处理 | 原生 PDF 与扫描件路由，正文、表格、公式、图片四类区域处理 |
| 结构化数据 | V2 Canonical 文档、确定性 ID、页码与 bbox 回查、元素关系 |
| 语料治理 | 文件清单、来源证据、重复关系、候选版本和正式版本指针 |
| 分块与索引 | 标题/条款感知分块，bge-small-zh dense 向量，BM25 sparse 索引 |
| 检索 | Qdrant dense + BM25 + RRF，支持地域、资料类型、效力和日期过滤 |
| 问答 | FastAPI、浏览器工作台、整段回答加句内来源编号、分情况作答、受控拒答、冲突证据和引用校验 |
| 原文查看 | 按文件编号的引用；点击后在整份文档中定位并高亮引用区域，可前后翻页、缩放；来源区域裁剪图；不提供 PDF 文件下载 |
| 可观测性 | 查询阶段耗时、模型用量、语料版本和检索配置记录 |
| 评估 | Canonical、表格、公式、区域检测、检索契约和 HTTP 接口测试 |

## 快速开始

运行查询服务需要 Python 3.11 和 Docker。只有在重建 PDF 处理结果时才需要 OCR 重型依赖。

### 1. 创建 Python 环境

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env` 中的 LLM 配置只影响回答生成。未配置有效密钥时，服务仍能启动，提问会直接返回找到的原文，并提示 AI 回答暂不可用。

同一代码库支持两种运行模式：

- `APP_PROFILE=self_hosted`（默认）：GitHub 发布形态。部署者使用 Release 中的现成资料库（见下文“载入现成资料库”）或自己的 PDF，并配置自己的模型 API Key；限流可以按其环境调整。
- `APP_PROFILE=demo`：仓库维护者出资的公开面试演示。必须启用应用限流，默认每个 IP 每天 10 次、每分钟 3 次 AI 回答，全站每天 20 次；额度耗尽后提问自动改为只返回相关原文，逐页原文浏览继续可用。

不要让公开网页收集访客 API Key；自托管部署者应把自己的 Key 写入服务器 Secret 或本地 `.env`。

### 公网演示

不想本地部署，可以直接打开 [https://weixiaocan.com/rag/](https://weixiaocan.com/rag/) 试用（每人每天 10 次 AI 回答，用完后只返回原文）。线上语料为 `corpus-b6e4850ef830`（4,263 个检索块），模型为智谱 GLM-5.2；2026-10-29 应用代码更新至 `55df929`，公网 ready、G001 定义题和 G009 图注题真实回答已烟测。已发布语料的既有表格与公式有逐项核对登记，新补的公式（6）（8）由负责人对照原页确认；扫描件正文是机器识别，引用它的结论会标注"未核对"，提示对照原文页。当前诊断黄金集 v8 仍有 4 条人工待核标记，不能把部署或烟测当成内容质量通过或法规效力的权威核验。

### 2. 载入现成资料库

仓库的 [Releases](https://github.com/weixiaocan/env-reg-rag/releases) 附带当前发布语料的数据包，不需要 OCR 或重新入库：

| 文件 | 内容 |
| --- | --- |
| `data-corpus-<版本>.tar.gz` | `data/registry`（语料登记与当前指针）和 `data/raw`（原文 PDF，用于逐页显示和高亮） |
| `qdrant-corpus-<版本>.snapshot` | 向量库快照（向量、BM25、条款路径与原文坐标） |
| `SHA256SUMS` | 校验和 |

把三个文件下载到同一目录，在仓库根目录运行（Windows 用 Git Bash 或 WSL）：

```bash
bash deploy/restore_release_data.sh <下载目录>
```

脚本会校验文件、解压 PDF 和登记数据、启动 Qdrant、载入快照并把查询入口 `corpus_v2_live` 指向它。

> 数据包中的原始 PDF 仅供学习与研究使用，版权归各自权利人所有，请勿用于商业用途或再次分发；正式使用请以官方发布版本为准。

### 3. 启动应用

```powershell
docker compose up -d --build
docker compose ps
```

应用地址：

- 工作台：`http://127.0.0.1:8000/`
- 存活检查：`http://127.0.0.1:8000/api/v1/health`
- 就绪检查：`http://127.0.0.1:8000/api/v1/ready`
- Qdrant：`http://127.0.0.1:6333`

检查服务：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/ready
```

`health` 只表示应用进程存活。`ready` 还会检查运行查询入口等于 pointer 中的 alias、alias 实际目标等于 pointer 目标、目标 collection 存在且 point 数等于 pointer 的 chunk 数。首次启动的新 Qdrant volume 没有索引：载入 Release 数据包，或按下一节从已有制品入库。

## 用已有 V2 制品建立索引

如果 `data/canonical/v2/<corpus-version>/` 已经存在，可以跳过 PDF 解析，直接分块、向量化和入库。

入库脚本离线加载 `BAAI/bge-small-zh-v1.5`。首次运行前，需要让模型出现在本机 Transformers 缓存中：

```powershell
.venv\Scripts\python.exe -c "from transformers import AutoModel, AutoTokenizer; m='BAAI/bge-small-zh-v1.5'; AutoTokenizer.from_pretrained(m); AutoModel.from_pretrained(m)"
```

Docker 镜像会在构建时固定并下载同一个模型，但容器缓存不会自动写入宿主机缓存。

读取当前语料版本并入库：

```powershell
$corpus = (Get-Content data\registry\corpus-current.json -Raw | ConvertFrom-Json).corpus_version
.venv\Scripts\python.exe scripts\ingest_corpus_v2.py --corpus-version $corpus --publish
```

当前入库仍采用版本内整库重建：脚本重新分块、重新嵌入并构建不可变 staging collection；通过验证后，`--publish` 原子切换查询 alias `corpus_v2_live`。它还没有实现按文档或 chunk 的增量 upsert。

## 从原始 PDF 重建语料

这条路径适合新增资料、修改处理规则，或者从 `data/raw/` 完整重建数据制品。OCR、版面、表格和公式模型都在本地运行。

### 1. 安装 OCR 依赖

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-ocr.txt
```

### 2. 登记并审计资料

```powershell
.venv\Scripts\python.exe scripts\data_inventory.py --raw-dir data/raw --out data/registry/inventory.csv
.venv\Scripts\python.exe scripts\audit_pdf_sources.py
.venv\Scripts\python.exe scripts\audit_pdf_sources.py --write
```

审计只记录本地文件能够证明的来源、版本和完整性信息。它不会自动联网补齐官方来源，也不会切换正式语料。

### 3. 构建页面级制品

```powershell
.venv\Scripts\python.exe scripts\build_corpus.py
```

`build_corpus.py` 会刷新清单、复用未变化页面的阶段缓存、运行统一 PDF 处理，并生成：

- `data/canonical/<corpus-version>-documents.jsonl`
- `data/registry/<corpus-version>.json`
- `data/registry/corpus-candidate.json`

脚本还会准备公式识别缓存，并在输出中打印 `formula_run_id`。保存这个值，装配阶段需要使用同一批公式缓存。

只检查待处理资料，不运行 OCR：

```powershell
.venv\Scripts\python.exe scripts\build_corpus.py --plan-only --skip-inventory-refresh
```

### 4. 装配 V2 Canonical

```powershell
$candidate = (Get-Content data\registry\corpus-candidate.json -Raw | ConvertFrom-Json).corpus_version
$manifest = Get-Content "data\registry\$candidate.json" -Raw | ConvertFrom-Json
$formulaRun = $manifest.formula_region_gate.run_id
.venv\Scripts\python.exe scripts\assemble_corpus_v2.py --corpus-version $candidate --formula-run-id $formulaRun
```

装配阶段只重组页面制品，不重新调用 PDF 或 OCR 引擎。缺少公式缓存、ID 不一致、bbox 越界或其他 Canonical 不变量失败时，脚本会直接报错。

发布前还需对照原 PDF 的文字层或独立 OCR 检查逐页覆盖和结构引用，并核对 `data/canonical/v2/<corpus-version>/completeness.json`：

```powershell
$cleanRun = 'ocrv6-c454d03f07c7e5c4'
$rawRun = 'ocrv6-ef8e83933c6bd4f3'
.venv\Scripts\python.exe scripts\audit_completeness.py --corpus-version $candidate --independent-ocr-run-id $cleanRun --second-ocr-run-id $rawRun --fail-on-open
```

审计只检查本地候选，不切换正式语料。确认为独立 OCR 误读或原 PDF 自身编号不一致的项目登记在 `data/registry/completeness-exceptions.json`，真实漏行和漏检公式不能用例外掩盖。

截至 2026-10-27，正式语料 `corpus-b6e4850ef830` 已完成 21 份文档、1078 页的 V2 装配（0 失败）；独立原页完整性审计为缺失行 0、开放结构问题 0。多普勒标准物理页 12 的公式（6）和物理页 13 的公式（8）已补为可回查公式元素，负责人对照原页确认当前转写后，两式的质量状态为 `passed`。本地及公网 Qdrant 索引均有 4263 个 point，`corpus_v2_live` 已切换，回退记录已写入；公网原文定位及页图已验证。这不代表其他语料内容或法规效力获得权威核验。

### 5. 入库并发布

```powershell
docker compose up -d qdrant
.venv\Scripts\python.exe scripts\ingest_corpus_v2.py --corpus-version $candidate --publish
```

只有 chunk、向量和 Qdrant point 数量一致，并且内置检索烟测返回结果时，`--publish` 才会更新 `data/registry/corpus-current.json`。

## 问答链路

一次提问经过以下步骤。代码入口为 `src/application/query_service.py`，运行时组合见 `src/server/runtime.py`。

| 步骤 | 做什么 | 代码 |
| --- | --- | --- |
| 0. 追问改写 | 有前文时，用最近 3 轮问答把追问改写成独立完整的问题（如“计算公式在哪里”→“夜间最小流量法的计算公式在哪里？”），用改写结果检索，网页在回答上方显示“检索：…”。新对话的第一个问题不改写；改写失败时按原问题检索。对话记录由浏览器保存并随请求发送，服务器不保存 | `src/adapters/langchain_question_rewriter.py`、`src/application/question_rewriting.py` |
| 1. 检索 | bge-small-zh 向量与 BM25 并行检索，RRF 合并，取前 10 条。只检索 `answer_and_citation` 与 `source_locator_only` 证据，`excluded` 不参与 | `src/adapters/qdrant_evidence_retriever.py` |
| 2. 截取 | 前 5 条进入生成上下文 | `generate_context_limit=5` |
| 3. 上下文补全 | 命中表格或公式时，补上同一条款前面的引导句，以及后面的“式中”变量说明、单位和注释，到下一条款为止；命中“见表 1”“按下式计算”这类引导句时，补上它指向的表格或公式。最多补 8 块 | `QdrantContextExpander` |
| 4. 生成 | 证据以 JSON 列表交给模型，每条带短编号（S1、S2…）、原文、文件名、页码、条款路径、效力与日期等元数据，以及是否经人工核对。模型输出一段完整回答，含事实的句子末尾标注 [S1] 这样的编号；只有不同文件的要求在同一适用范围内无法同时成立时，才另行列出冲突 | `src/adapters/langchain_answer_generator.py` |
| 5. 拆分与校验 | 按编号把回答切成逐句的结论，编号映射回 evidence ID。引用了未提供的编号时整次回答作废；回答中没有任何编号时按“证据不足”处理；冲突只能由两份不同文件的已核对证据构成 | `split_cited_answer`、`QueryApplicationService` |
| 6. 标记 | 引用了机器识别、未经人工核对证据的句子标记为 `verified=false`，页面提示“部分引用来自机器识别，请对照原文页” | 同上 |

提示词只要求模型依据资料、先给结论、说明出处；结论取决于问题没说明的条件（如统计时段）时分情况说明；涉及公式时写出公式和各变量含义。最近 3 轮问答作为“之前的对话”放在本轮问题前，只用于理解问题，事实仍只能来自本次资料；历史不作为模型的回复消息重放，否则模型会模仿纯文本回复而丢掉要求的 JSON 格式。公式要求写成纯文本。系统不做重排（E1 实验未达采纳门槛）。

### 返回状态

| 状态 | 含义 |
| --- | --- |
| `answered` | 回答通过了引用校验 |
| `conflicting_sources` | 不同文件在同一适用范围内的要求无法同时成立，分别列出，不替用户裁决 |
| `refused` | 资料不能支持回答，系统不拼凑答案 |
| `search_only` | 没有生成回答，只返回检索到的原文：模型未配置、演示额度用完，或调用方指定了 `source_lookup` |
| `needs_clarification` | 保留在接口枚举中；当前版本不再由规则追问，网页链路不会返回该状态 |

### HTTP 接口

```text
POST /api/v1/queries
GET  /api/v1/documents
GET  /api/v1/documents/{document_version_id}
GET  /api/v1/documents/{document_version_id}/pages/{physical_page}
GET  /api/v1/documents/{document_version_id}/pages/{physical_page}/image
GET  /api/v1/evidence/{evidence_id}
GET  /api/v1/regions/{region_id}/image
```

`POST /api/v1/queries` 的 `mode` 默认为 `answer`。网页只使用这一种方式；`mode=source_lookup` 只检索、不调用模型，保留给检索测试和评估脚本：

```powershell
$body = @{
  question = "GB 50014-2021 关于雨水管渠设计重现期的原文在哪里？"
  conversation_context = @{}
  mode = "source_lookup"
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/api/v1/queries `
  -Method Post `
  -ContentType "application/json; charset=utf-8" `
  -Body $body
```

`mode=answer` 会把生成上下文中的证据片段发送给 `.env` 配置的外部模型，产生模型费用，证据片段也会离开本机。涉及受限资料时，先确认数据发送边界。

页面信息接口返回页面的点数尺寸，阅读器据此把 PDF 坐标换算到页面图上画高亮框。

## 测试

运行完整自动化测试：

```powershell
.venv\Scripts\python.exe -m pytest -q
```

检查当前发布版本：

```powershell
$current = Get-Content data\registry\corpus-current.json -Raw | ConvertFrom-Json
$manifest = Get-Content $current.manifest -Raw | ConvertFrom-Json
$current
$manifest.structure_processing
```

检查在线索引数量：

```powershell
Invoke-RestMethod http://127.0.0.1:6333/collections/corpus_v2_live
```

判断结果时要分开看三类证据：

- **工程契约**：单元测试和集成测试证明 schema、状态机、过滤、引用和接口符合代码约定。
- **运行烟测**：就绪检查、实际 Qdrant point 数和在线查询证明当前服务可运行。
- **内容质量**：固定问题集、原页金标准和人工坏例复核衡量 OCR、表格、公式、召回和答案质量。

截至 2026-10-27，本地及公网 `data/registry/corpus-current.json` 均指向 `corpus-b6e4850ef830`：正式选择 21 份主文本、1078 页、11979 个结构区域、4263 个检索块，结构处理 0 个执行失败；两处查询入口 `corpus_v2_live` 与指针目标一致，Qdrant 实测 4263 个 point。后续始终以各环境的 pointer、对应 manifest 和 Qdrant 实际状态为准。

## 项目结构

| 目录 | 职责 |
| --- | --- |
| `src/domain/` | V2 Canonical、chunk、查询、回答和证据领域模型 |
| `src/application/` | 文档装配、校验、分块、语料更新、查询和生成编排 |
| `src/ingestion/` | 原生 PDF、OCR 路由、版面、表格、公式和图片处理 |
| `src/retrieval/` | bge-small-zh 编码、Qdrant 索引和混合检索 |
| `src/adapters/` | Qdrant、LLM、文档目录、查询日志和区域图片适配 |
| `src/server/` | FastAPI、浏览器工作台、运行时组合和就绪检查 |
| `src/evaluation/` | 表格、公式、区域检测和结构质量评估 |
| `scripts/` | 资料审计、语料构建、装配、入库和 manifest 维护入口 |
| `tests/` | 单元测试、契约测试和 Qdrant 服务集成测试 |
| `data/raw/` | 原始 PDF，不进入 Git，随 Release 数据包分发 |
| `data/registry/` | 文件清单、来源记录、文档关系和语料发布指针 |
| `data/canonical/` | 页面级制品与 V2 Canonical 文档，本地可重建 |
| `data/model_runtime/` | OCR、版面、表格、公式模型缓存与区域图片 |
| `data/observability/` | 查询 trace 和本地运行日志 |

`data/raw/`、`data/canonical/`、`data/model_runtime/`、日志和 Qdrant volume 属于体积较大的本地运行制品，不进入 Git。原始 PDF 的许可和再分发边界见 [DATA_NOTICE.md](DATA_NOTICE.md)。

## 设计与数据约束

- **原文可回查**：检索增强文本不能替代源元素。引用必须能够回到文件、物理页和页面区域。
- **质量状态独立**：执行完成不自动升级为内容正确，也不自动获得回答准入。
- **未核对内容带标记**：未经核对的识别结果（目前主要是扫描件正文）可以支持回答，但对应结论标记“未核对”，页面提示对照原文；`excluded` 内容不参与检索。
- **来源关系显式登记**：同标准号或相似文件名不会触发自动合并，不同排版的页码不会自动互换。
- **本地文档处理**：PDF 解析和 OCR 都在本地运行。唯一例外是表格核对：经负责人授权，表格裁剪图发给智谱 GLM-4.5V 读表，与本地识别结果逐格比对。
- **生成引用受控**：模型只能使用本次提供的来源编号，编号映射回 evidence ID 后经应用层逐句校验。

更详细的模块边界、状态语义和延期范围见：

- [架构说明](docs/ARCHITECTURE.md)
- [当前范围与延期项](docs/SCOPE_AND_DEFERRED.md)
- [V2 Canonical Schema 设计](docs/v2-canonical-schema-design.md)
- [数据资料声明](DATA_NOTICE.md)
- [服务器部署](deploy/README.md)
- [代理协作约束](AGENTS.md)

## 评估结果（diagnostic 口径）

以下是 2026-10-24 在当时正式语料 `corpus-ed28791afb8a` 上运行的完整诊断报告。黄金集 `eval/golden_v5.jsonl` 共 50 题（46 道内容题 + 2 道前提错误题 + 2 道应拒答题），题目状态为 `reviewed`、尚未冻结；语义判定由 agent 对照标准事实复核，不是人工裁定。

2026-10-27 新版 `corpus-b6e4850ef830` 的检索诊断见 `eval/runs/diagnostic-b6e4850ef830-v6/`：50 题、必中证据前 5 条 50/76、前 10 条 58/76。真实智谱 GLM-5.2 HTTP 诊断见 `eval/runs/diagnostic-e2e-20261027-b6-r2/`：45 回答、5 拒答，估算费用 1.69 元；G001、G009、G023、G049 等仍有内容坏例，详见 `eval/audits/diagnostic-e2e-20261027-b6-review.md`。`golden_v6.jsonl` 的 79 条证据有 19 条待人工重锚，故不能把这些数字用于严格新旧比较或正式验收。

2026-10-29 的修订使用同一 b6 语料，见 `eval/golden_v7.summary.md`。`golden_v7.jsonl` 有 50 题、80 条证据，18 条仍待重锚。原始索引检索诊断 @5 为 53/77、@10 为 61/77；它直接查询 Qdrant，不经过应用层针对定义、多标准对照和图号的补充检索。真实智谱 HTTP 诊断见 `eval/runs/diagnostic-e2e-20261029-b6-v7/`：48 回答、2 拒答、trace 无缺失，本次估算 1.902488 元；G001、G009、G023 已回答。G049 的图名与表格数值可分别定位，但图内数字仍未完整转成 Canonical 文本。这些是诊断结果，48 个回答并不等于 48 题内容质量通过；应用代码已在公网演示更新，G001/G009 在线烟测通过。

同日继续对原 PDF 逐页复核并形成 `eval/golden_v8.jsonl`：50 题、83 条证据，82 条通过严格锚点匹配，4 条仍有人工待核标记。v8 原始索引诊断 @5 为 53/80、@10 为 61/80；详情和剩余四题见 `eval/golden_v8.summary.md`。v8 只改评估锚点和预期事实，问法不变。四道在 v7 HTTP 运行中答错或漏答的题（G004/G020/G026/G046）经定向检索修复和智谱 GLM-5.2 复测后均回答了目标事实，见 `eval/audits/diagnostic-e2e-20261029-b6-v7-review.md`；其余回答尚未完成逐题语义验收。

检索（本地，`eval/runs/diagnostic-retrieval-20261024-ed28/`）：

| 指标 | 结果 |
| --- | --- |
| 必中证据进入前 5 / 前 10 | 54/76 / 60/76 |
| 命中正确文件（前 5） | 99.5% |

端到端（从 HTTP 查询入口到最终响应，智谱 GLM-5.2，`eval/runs/diagnostic-e2e-20261024-ed28/`）：

| 指标 | 结果 |
| --- | --- |
| 46 道内容题：完整答对 / 部分答对 / 拒答 | 33 / 11 / 2 |
| 前提错误题与应拒答题处理正确 | 4/4 |
| 119 条回答主张带引用且引用落在送模型证据内 | 119/119（其中 16 条带“未核对”标记） |
| 端到端 p50 / p95（检索阶段 p50） | 5.8 s / 26.6 s（62 ms） |
| 50 题 token / 估算费用 | 126k 输入 + 25k 输出 / 约 1.71 元 |

部分答对多是送入模型的 5 条证据漏了某个事实，模型如实说明“资料中未给出”，没有编造。完整口径、逐题判定和剩余风险见 [评估总报告](eval/FINAL_EVAL_REPORT.md)。

历史实验：本地交叉编码重排（E1）把 Recall@5 从 54/77 提到 57/77，但 CPU 重排 p50 约 3.3 秒，并且新增了可引用证据降级，未达采纳门槛，没有接入在线路径（`eval/runs/diagnostic-exp-e1-rerank-20261006-r2/`）。

## 已知限制

- 当前服务面向单机或自托管使用，没有多租户、账号权限、计费和高可用部署。
- 内容质量评估仍以自动测试、固定样例和人工复核为主，尚未形成全库自动放行体系。
- 来源和效力信息没有持续联网核验，`unknown` 与 `needs_review` 必须按原状态展示。
- 新增 PDF 可以复用未变化页面的处理缓存；分块、嵌入和 Qdrant 入库仍是整库重建。
- OCR 对复杂表格、低质量扫描页和公式参数仍可能出错，隔离内容需要回看原页。
- 问题中点名文件时，文件名关键词可能压过真正的条文（如 G001）；图片内容类问题回答不稳定。
- 检索固定返回前 10 条，没有相关度门槛；不重排。追问改写每次多一次模型调用；额度用完退回只查原文时不改写，追问会按原话检索。
- “先给结论”“写一次出处”等写法要求只在提示词中约束，代码只校验引用。

## 许可

项目自行编写的代码和文档采用 [GNU AGPL v3.0 only](LICENSE)。第三方依赖见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。第三方 PDF 及其派生内容遵循各自权利状态，具体边界见 [DATA_NOTICE.md](DATA_NOTICE.md)。

# 法规文档 RAG 智能问答系统

> 面向环境行业，以城镇排水与防涝领域的法规、政策、标准和技术资料为落地场景。

这是一个面向法规与标准类 PDF 的本地 RAG 系统，当前以环境行业中的排水领域为语料。项目从 PDF 开始，完成来源登记、原生解析与 OCR、结构化装配、分块、混合检索和证据约束回答，并把每条检索结果关联回原文页码与页面区域。

它适合用来搭建可追溯的专业资料问答原型，也可以作为 PDF 数据处理、混合检索和引用约束生成的工程参考。当前形态是单机或自托管 POC，重点放在数据链路、证据边界和可评估性。

## 项目解决什么问题

普通 PDF 问答往往只提取连续文本。排水标准里的多级表头、公式、图注、条款层级和页码关系会在这个过程中丢失，模型最后给出的答案也很难回到原文核对。

这个项目把处理过程拆成三个彼此独立的层次：

- **资料治理**：记录文件来源、版本、效力状态、重复关系和发布版本。
- **文档结构化**：按页处理正文、表格、公式和图片，保留物理页码、坐标与处理状态。
- **证据问答**：使用向量和关键词混合检索，只允许经过回答准入的证据支持结论。

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
    I --> J[范围判断与证据筛选]
    J --> K[引用约束生成]
    K --> L[答案与原文定位]
```

这条链路的关键点是：检索块只是查询投影，V2 Canonical 元素和原 PDF 才是可回查的事实来源。

1. **来源登记**通过文件 SHA-256 识别内容，记录重复副本、同版本候选和来源核验状态。
2. **统一 PDF 处理**逐页执行版面分析。原生文字层合格时直接提取正文，表格、公式和图片仍继续处理；扫描页进入本地 OCR。
3. **V2 Canonical 装配**把页面结果整理为有顺序的元素流。每个元素保存类型化内容、页码、坐标、来源和关联关系。
4. **结构感知分块**优先沿标题、条款和结构化元素边界切分，再按 token 预算处理过长正文。
5. **混合检索**同时使用 `BAAI/bge-small-zh-v1.5` 中文向量和 Qdrant BM25 稀疏检索，通过 RRF 合并排序。
6. **证据约束生成**先解析问题范围，再筛选允许回答的证据。每条生成主张必须绑定本次查询返回的 evidence ID。

## 核心能力

| 部分 | 当前能力 |
| --- | --- |
| PDF 处理 | 原生 PDF 与扫描件路由，正文、表格、公式、图片四类区域处理 |
| 结构化数据 | V2 Canonical 文档、确定性 ID、页码与 bbox 回查、元素关系 |
| 语料治理 | 文件清单、来源证据、重复关系、候选版本和正式版本指针 |
| 分块与索引 | 标题/条款感知分块，bge-small-zh dense 向量，BM25 sparse 索引 |
| 检索 | Qdrant dense + BM25 + RRF，支持地域、资料类型、效力和日期过滤 |
| 问答 | FastAPI、浏览器工作台、条件追问、受控拒答、冲突证据和引用校验 |
| 原文查看 | 单页原文图、证据详情、物理页码、来源区域裁剪图；不提供整本 PDF 接口 |
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

`.env` 中的 LLM 配置只影响回答生成。未配置有效密钥时，服务仍能启动，`source_lookup` 原文检索可以继续使用，`answer` 模式会受控拒答。

同一代码库支持两种运行模式：

- `APP_PROFILE=self_hosted`（默认）：GitHub 发布形态。部署者自行提供 PDF、Qdrant 数据和模型 API Key；限流可以按其环境调整。
- `APP_PROFILE=demo`：仓库维护者出资的公开面试演示。必须启用应用限流，默认每个 IP 每天 10 次、每分钟 3 次 AI 回答，全站每天 20 次；额度耗尽后 `source_lookup` 和逐页原文浏览继续可用。

两种模式都默认设置 `PUBLIC_DIAGNOSTIC_ROUTES_ENABLED=false`。不要让公开网页收集访客 API Key；自托管部署者应把自己的 Key 写入服务器 Secret 或本地 `.env`。

### 2. 启动 Qdrant 和应用

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

`health` 只表示应用进程存活。`ready` 还会检查运行查询入口等于 pointer 中的 alias、alias 实际目标等于 pointer 目标、目标 collection 存在且 point 数等于 pointer 的 chunk 数。首次启动的新 Qdrant volume 没有索引，此时需要完成下一节的入库。

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
.venv\Scripts\python.exe scripts\enrich_corpus_v2_payloads.py --corpus-version $corpus
.venv\Scripts\python.exe scripts\regenerate_corpus_manifest.py
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

### 5. 入库并发布

```powershell
docker compose up -d qdrant
.venv\Scripts\python.exe scripts\ingest_corpus_v2.py --corpus-version $candidate --publish
.venv\Scripts\python.exe scripts\enrich_corpus_v2_payloads.py --corpus-version $candidate
.venv\Scripts\python.exe scripts\regenerate_corpus_manifest.py
```

只有 chunk、向量和 Qdrant point 数量一致，并且内置检索烟测返回结果时，`--publish` 才会更新 `data/registry/corpus-current.json`。

## 使用问答服务

浏览器工作台提供问答、证据列表、原文信息和区域图片入口。HTTP API 的主入口是：

```text
POST /api/v1/queries
```

### 只检索原文

`source_lookup` 不调用 LLM，适合检索测试和人工查阅：

```powershell
$body = @{
  request_id = "source-demo-001"
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

### 生成带引用回答

把 `mode` 改为 `answer` 后，系统会把允许回答的检索证据发送给 `.env` 中配置的外部模型。调用会产生模型费用，也意味着证据片段会离开本机。涉及受限资料时，需要先确认数据发送边界。

查询可能返回以下状态：

| 状态 | 含义 |
| --- | --- |
| `answered` | 生成了通过引用校验的回答 |
| `needs_clarification` | 缺少统计时段、地域等必要条件 |
| `search_only` | 只提供原文定位，不生成结论 |
| `conflicting_sources` | 不同文档版本存在无法同时成立的要求 |
| `refused` | 没有足够证据，或生成模型未配置 |

每条回答主张都带有 evidence ID。服务还提供文档、证据和区域图片接口：

```text
GET /api/v1/documents/{document_version_id}
GET /api/v1/documents
GET /api/v1/documents/{document_version_id}/pages/{physical_page}/image
GET /api/v1/evidence/{evidence_id}
GET /api/v1/regions/{region_id}/image
```

公式列表和复核接口属于诊断能力，不是主问答链路的前置依赖。

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

截至 2026-09-29，当前工作区 pointer 指向 `corpus-0b9a1fd84e3d`：正式选择 23 份主文本、1144 页和 4232 个检索块；查询入口为 `corpus_v2_live`。后续始终以 `corpus-current.json`、对应 manifest 和 Qdrant 实际状态为准。

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
| `data/raw/` | 随仓库管理的原始 PDF |
| `data/registry/` | 文件清单、来源记录、文档关系和语料发布指针 |
| `data/canonical/` | 页面级制品与 V2 Canonical 文档，本地可重建 |
| `data/model_runtime/` | OCR、版面、表格、公式模型缓存与区域图片 |
| `data/observability/` | 查询 trace 和本地运行日志 |

`data/canonical/`、`data/model_runtime/`、日志和 Qdrant volume 属于体积较大的本地运行制品，不进入 Git。原始 PDF 的许可和再分发边界单独记录。

## 设计与数据约束

- **原文可回查**：检索增强文本不能替代源元素。引用必须能够回到文件、物理页和页面区域。
- **质量状态独立**：执行完成不自动升级为内容正确，也不自动获得回答准入。
- **低置信内容受限**：未经复核的结构化数字、公式和表格可以用于定位原文，不能直接支持结论。
- **来源关系显式登记**：同标准号或相似文件名不会触发自动合并，不同排版的页码不会自动互换。
- **本地文档处理**：PDF、页面和裁剪图默认不上传外部视觉服务。
- **生成引用受控**：模型只能引用本次提供的 evidence ID，输出仍会经过应用层校验。

更详细的模块边界、状态语义和延期范围见：

- [架构说明](docs/ARCHITECTURE.md)
- [当前范围与延期项](docs/SCOPE_AND_DEFERRED.md)
- [V2 Canonical Schema 设计](docs/v2-canonical-schema-design.md)
- [数据资料声明](DATA_NOTICE.md)
- [公开部署准备](docs/PUBLIC_DEPLOYMENT_PREP.md)
- [代理协作约束](AGENTS.md)

## 评估结果（diagnostic 口径）

评估使用 50 题黄金集（46 道内容题 + 4 道应拒答题，`eval/golden_v3.jsonl`）和 16 道不点名文件的自然问法题（`eval/golden_natural.jsonl`）。题目状态为 `reviewed`，尚未冻结，因此以下数字均为诊断结果，不是正式验收成绩。

当前正式语料 `corpus-7411b3a05a07`，生成模型为智谱 GLM，经生产查询入口实测（`eval/runs/diagnostic-gen-20261013-7411-staging/REPORT.md`）：

| 指标 | 结果 |
| --- | --- |
| 正确证据排第 1 / 前 3 / 前 5 | 23/46 / 37/46 / 38/46 |
| 自然问法正确证据进入前 5 | 15/16 |
| 黄金集回答 / 拒答 / 出错 | 43 / 7 / 0 |
| 应拒答题正确拒答 | 3/4 |
| 总延迟 p50 / p95 | 10.6 s / 36.8 s |

端到端评估（2026-10-15，从 HTTP 查询入口到最终响应，`eval/runs/diagnostic-e2e-20261015-7411/`）：

| 指标 | 结果 |
| --- | --- |
| 46 道内容题：完整答对 / 部分答对 / 拒答（agent 对照标准事实复核） | 29 / 12 / 4（另有 1 题疑似黄金集标注错误，待人工裁定） |
| 82 条回答主张带引用且引用落在送模型证据内 | 82/82（其中 39 条带“未核对”标记） |
| 端到端 p50 / p95（检索阶段 p50） | 10.5 s / 40.1 s（64 ms） |
| 50 题 token / 估算费用 | 137k 输入 + 50k 输出 / 约 2.49 元 |

完整口径、失败分类和剩余风险见 [评估总报告](eval/FINAL_EVAL_REPORT.md)。

历史实验：本地交叉编码重排（E1）把 Recall@5 从 54/77 提到 57/77，但 CPU 重排 p50 约 3.3 秒，并且新增了可引用证据降级，未达采纳门槛，没有接入在线路径（`eval/runs/diagnostic-exp-e1-rerank-20261006-r2/`）。

## 已知限制

- 当前服务面向单机或自托管使用，没有多租户、账号权限、计费和高可用部署。
- 内容质量评估仍以自动测试、固定样例和人工复核为主，尚未形成全库自动放行体系。
- 来源和效力信息没有持续联网核验，`unknown` 与 `needs_review` 必须按原状态展示。
- 新增 PDF 可以复用未变化页面的处理缓存；分块、嵌入和 Qdrant 入库仍是整库重建。
- OCR 对复杂表格、低质量扫描页和公式参数仍可能出错，隔离内容需要回看原页。
- 问题中点名文件时，文件名关键词可能压过真正的条文（如 G001）；图片内容类问题回答不稳定。

## 许可

项目自行编写的代码和文档采用 [GNU AGPL v3.0 only](LICENSE)。第三方依赖见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。第三方 PDF 及其派生内容遵循各自权利状态，具体边界见 [DATA_NOTICE.md](DATA_NOTICE.md)。

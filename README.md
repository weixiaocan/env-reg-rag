# 排水法规标准智能问答系统

面向排水领域法律法规、政策、标准和技术资料的开源 RAG 系统。系统不仅返回答案，还提供文档、条款、页码和原文定位；当条件不完整或证据不足时，会追问或拒绝生成结论。

## 核心能力

- 管理资料来源、版本、效力、处理质量和语料发布状态。
- 对原生 PDF 直接解析，对扫描件按页路由至 OCR，并保留表格与坐标。
- 将原文构造成可回查的证据单元和面向召回的检索块。
- 使用 Qdrant、中文 dense 向量、BM25 和 RRF 完成混合检索。
- 通过 Evidence Pack 和 Evidence ID 白名单约束模型引用。
- 提供网页工作台、HTTP API 和只读 MCP，支持连续追问、拒答和原文跳转。

## 架构

```text
PDF → 来源登记 → 解析/OCR → 证据单元 → 检索块
    → Qdrant 混合检索 → Evidence Pack → 回答/追问/拒答
    → Web / HTTP API / MCP
```

模块边界和数据流见 [架构说明](docs/ARCHITECTURE.md)。

## 快速开始

### Docker Compose

准备 Docker Desktop，复制环境变量模板并填写至少一个模型 API Key：

```powershell
Copy-Item .env.example .env
docker compose up --build -d
```

服务就绪后访问：

- 网页工作台：<http://127.0.0.1:8000/>
- 公式原文与参数说明：<http://127.0.0.1:8000/formula-review>（原文截图、已核验参数及前后页阅读线索；只读，不提供计算）
- 全量数学区域对照：<http://127.0.0.1:8000/formula-review>（分类筛选、分页、原始转写与PDF原页；不提供批准或计算）
- OpenAPI 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/v1/health>
- 就绪检查：<http://127.0.0.1:8000/api/v1/ready>

公式页面直接展示原PDF公式截图和邻近说明，支持查看当前完整页及前后页；LaTeX代码默认折叠，仅用于开发核查。参数表来自单独的人工原图核验记录，未经核验的原生提取或已有OCR文字仅作为原文阅读线索，不自动关联参数、推断单位或计算条件。页面显示文字处理路线及基础质量隔离状态，扫描文字可能错字或缺符号，必须对照原图。接口不运行OCR；识别运行报告不随Git发布，新环境缺失报告时提示不可用，需要先生成并挂载报告。开发核验样例保留在 `/formulas`。识别、展示和正式回答准入是不同状态，不自动更新回答数据库。

公式页面支持按用途、标准名称和参数关键词搜索。详情优先展示原始公式截图，并集中展示同页“式中”参数说明；自动提取的说明保留原文字、页码和坐标，明确区分于已对照原图的参数表。六个固定样例已建立来源绑定的参数记录，包含污泥龄法及缺氧区容积等跨页说明，并展示公式编号和各参数的来源页。未登记的跨页说明可查看前后完整原页，不自动猜测对应关系。

停止服务：

```powershell
docker compose down
```

### 本地运行

需要 Python 3.11 和 Docker：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
docker compose up -d qdrant
.venv\Scripts\python.exe scripts\ensure_qdrant_indexes.py
.venv\Scripts\python.exe -m uvicorn src.server.main:app --host 127.0.0.1 --port 8000
```

只有在处理扫描件、复杂表格或执行 OCR 管线时，才安装可选重型依赖：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-ocr.txt
```

## 更新资料库

把新 PDF 放入 `data/raw/` 后，先查看本次更新计划：

```powershell
.venv\Scripts\python.exe scripts\update_corpus.py --plan-only
```

可以先离线审计来源依据和完整性疑点，再保存按文件哈希关联的记录：

```powershell
.venv\Scripts\python.exe scripts\audit_pdf_sources.py
.venv\Scripts\python.exe scripts\audit_pdf_sources.py --write
```

审计不自动下载官方资料，也不发布索引。旧记录保留为历史声明，缺少依据不会冒充新核验；“未发现疑点”不等于版本完整。记录格式和适用边界见 [PDF 数据处理说明](docs/PDF_PROCESSING.md)。

查看完全重复组及同版本候选：

```powershell
.venv\Scripts\python.exe scripts\review_document_relations.py
```

不同哈希的文件只有在 `data/registry/document_relations.json` 中登记全文比对确认后才合并处理，优先选择已核验官方主文本。候选不会减少入库范围，旧文件与引用身份保留映射；不同排版的页码不自动互换。登记格式见 [PDF 数据处理说明](docs/PDF_PROCESSING.md)。

构建候选语料；命令会刷新清单、按 SHA-256 和已确认版本关系去重，每页进行版面分析，并统一处理正文、表格、公式和图片。未变化且配置匹配的阶段复用缓存，新增、修改PDF及处理配置变化重跑受影响阶段：

```powershell
.venv\Scripts\python.exe scripts\update_corpus.py
```

候选全部主文本页及检测区域执行完整，且全量来源与制品完整性核对通过时，可以构建不可变 Qdrant collection 并切换 `corpus_current`。旧集合及回退记录保留：

```powershell
.venv\Scripts\python.exe scripts\update_corpus.py --publish
```

默认启用统一本地处理；有文字层也不跳过结构分派，扫描文字质量不足时使用本地OCR。公式和图片保留原PDF裁剪图，图片仅用原图注及明确图号引用建立本地检索文本，不调用外部视觉模型。低置信正文及未核验结构仅用于原文定位，不升级正式回答准入。`--text-only` 为旧文字流程诊断模式，其 `--ocr-layout`、`--ocr-tables` 选项不能替代统一四类处理。官方来源发现不是自动化步骤；旧审核表保留，新字段依据维护在 `data/registry/source_evidence.jsonl`，后续建库按哈希读取。

## 测试

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

当前公开版本的检索、回答、引用、接口和单机运行结果见 [评估结果](docs/EVALUATION.md)。

## MCP

Agent host 可以通过 stdio 启动只读 MCP：

```powershell
.venv\Scripts\python.exe -m src.mcp_adapter.main
```

服务暴露 `answer_with_evidence`、`search_evidence` 和 `get_document_evidence` 三个只读工具，以及 `evidence://{evidence_id}` 资源。

## 数据资料

`data/raw/` 保存随仓库发布的排水领域法规、政策、标准和技术资料，`data/registry/inventory.csv` 记录文件哈希、页数和重复关系。资料可以按相同的准入、解析、评估和发布机制持续扩展。

当前数据快照包含 27 个 PDF 文件，其中 23 份为原候选资料、4 份为官方核验副本；按 SHA-256 合并后是 22 个独立内容、5 个完全重复副本。物理文件合计 1252 页。进一步合并一组已确认的同版本不同文件后，当前正式版本 `corpus-37456321a968` 处理 21 份主文本、1078 页：1078 页完成版面分析和四类状态记录，11977 个检测区域均有执行结果。5116 个检索块中，3822 个可用于正文回答，1294 个仅用于原文定位。其中包含 638 个公式、239 个表格和 78 个图片区域；自动结构和关联的质量状态仍单独保留，不因执行完成而视为已核验。

当前默认入口对新增及修改 PDF 直接执行版面、OCR、表格、公式和图片处理，不要求另跑公式批次或补跑表格脚本。`--formula-run-id` 只用于引用既有固定公式批次的诊断兼容。完整性核对、执行覆盖、内容质量和具体未解决项见 [PDF数据处理说明](docs/PDF_PROCESSING.md)。

公开渠道可取得不等于已经取得再分发授权。第三方资料及其派生摘录不受项目 AGPL 许可覆盖；权利边界、更正和下架流程见 [数据资料声明](DATA_NOTICE.md)。

## 项目结构

```text
src/domain/          领域模型与业务状态
src/application/     查询、检索和资料生命周期编排
src/ingestion/       PDF 原生解析与 OCR
src/retrieval/       Qdrant、向量和数值检索
src/adapters/        数据库、模型与外部服务适配
src/server/          FastAPI 与网页工作台
src/mcp_adapter/     只读 MCP 接口
data/                原始资料、登记信息和运行所需语料
scripts/             数据构建、索引和验证命令
tests/               自动化测试
.codex/skills/       本项目采用的规格、设计和构建评审工作流
```

## 已知限制

- 当前是经过验证的单机 POC，不是生产级高可用系统。
- 尚未验证多用户并发、远程 MCP 认证和异地灾备。
- 模型生成仍可能遗漏补充信息，最终结论应回查引用原文。
- OCR 对复杂表格和低质量扫描件仍需质量门控与人工复核。

## 许可证

项目自行编写的代码和文档采用 [GNU AGPL v3.0 only](LICENSE)。第三方依赖见 [Third-party notices](THIRD_PARTY_NOTICES.md)；第三方资料遵循各自权利状态，详见 [数据资料声明](DATA_NOTICE.md)。

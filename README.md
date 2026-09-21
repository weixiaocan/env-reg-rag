# 排水法规标准智能问答系统

面向排水领域法律法规、政策、标准和技术资料的开源 RAG 系统。系统对 PDF 资料做原生解析与 OCR 处理，构造元素归属的 V2 Canonical 结构化文档，再分块、向量化并入库 Qdrant，支持混合检索与原文定位（文档、条款、页码、坐标）。

## 核心能力

- 管理资料来源、版本、效力、处理质量和语料发布状态。
- 对原生 PDF 直接解析，对扫描件按页路由至 OCR，并保留表格、公式、图片与坐标。
- 将每页 OCR 结果装配为元素归属的 V2 Canonical 文档（文本 / 表格 / 公式 / 图片四类元素，带 `source_spans`、`provenance` 与 `links`）。
- 按 heading / clause / 结构化单元边界做结构感知分块，保留元素到原文页码与坐标的回查链路。
- 使用 Qdrant、中文 dense 向量（bge-small-zh）与 BM25 稀疏检索，RRF 融合完成混合检索。
- 来源审计与文档关系登记按文件哈希关联，不同排版的同版本文件经全文比对确认后才合并处理。

## 架构

```text
PDF → 来源登记 → 解析/OCR（unified_pdf 四类处理）
    → V2 Canonical（元素归属，含表格/公式/图片）
    → 结构感知分块（v2_chunker）
    → 向量化（bge-small-zh）
    → Qdrant 混合检索（dense + BM25 + RRF）
```

模块边界和数据流见 [架构说明](docs/ARCHITECTURE.md)。

## 快速开始

需要 Python 3.11 和 Docker：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
docker compose up -d qdrant
```

只有在处理扫描件、复杂表格或执行 OCR 管线时，才安装可选重型依赖：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-ocr.txt
```

### 装配 V2 Canonical 语料

把 21 份主文本的 page-intermediate OCR 缓存装配为 V2 Canonical 文档（只读复用既有 OCR 结果，不重新跑 OCR）：

```powershell
$env:PYTHONPATH="src"
.venv\Scripts\python.exe scripts\assemble_corpus_v2.py
```

### 入库 Qdrant

分块、向量化并写入持久化 Qdrant collection `corpus_v2`：

```powershell
.venv\Scripts\python.exe scripts\ingest_corpus_v2.py
```

### 验证检索

对单个文档做 chunk 溯源与检索验证：

```powershell
.venv\Scripts\python.exe scripts\verify_v2_chunk_trace.py --sha <sha前缀>
```

停止 Qdrant：

```powershell
docker compose down
```

## 更新资料库

把新 PDF 放入 `data/raw/` 后，先离线审计来源依据和完整性疑点，再保存按文件哈希关联的记录：

```powershell
.venv\Scripts\python.exe scripts\audit_pdf_sources.py
.venv\Scripts\python.exe scripts\audit_pdf_sources.py --write
```

审计不自动下载官方资料，也不发布索引。旧记录保留为历史声明，缺少依据不会冒充新核验；“未发现疑点”不等于版本完整。记录格式和适用边界见 [PDF 数据处理说明](docs/PDF_PROCESSING.md)。

查看完全重复组及同版本候选：

```powershell
.venv\Scripts\python.exe scripts\review_document_relations.py
```

不同哈希的文件只有在 `data/registry/document_relations.json` 中登记全文比对确认后才合并处理，优先选择已核验官方主文本。候选不会减少入库范围，旧文件与引用身份保留映射；不同排版的页码不自动互换。

新 PDF 的版面分析、OCR、表格 / 公式 / 图片识别由 `src/ingestion/` 与 `src/application/unified_pdf.py` 统一处理，每页产出四类元素与坐标；未变化且配置匹配的阶段复用缓存，新增、修改 PDF 及处理配置变化重跑受影响阶段。处理结果写入 page-intermediate OCR 缓存，再由 `scripts/assemble_corpus_v2.py` 装配为 V2 Canonical，最后经 `scripts/ingest_corpus_v2.py` 分块入库。公式和图片保留原 PDF 裁剪图，图片仅用原图注及明确图号引用建立本地检索文本，不调用外部视觉模型。低置信正文及未核验结构仅用于原文定位，不升级正式回答准入。

记录格式、完整性核对、执行覆盖、内容质量和具体未解决项见 [PDF数据处理说明](docs/PDF_PROCESSING.md)。

## 测试

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

检索、分块与单机运行结果见 [评估结果](docs/EVALUATION.md)。

## 数据资料

`data/raw/` 保存随仓库发布的排水领域法规、政策、标准和技术资料，`data/registry/inventory.csv` 记录文件哈希、页数和重复关系。资料可以按相同的准入、解析、装配和入库机制持续扩展。

正式版本 `corpus-37456321a968` 处理 21 份主文本、1078 页：1078 页完成版面分析和四类状态记录，23095 个元素（文本 22568 / 表格 239 / 公式 210 / 图片 78）。自动结构和关联的质量状态单独保留，不因执行完成而视为已核验。

公开渠道可取得不等于已经取得再分发授权。第三方资料及其派生摘录不受项目 AGPL 许可覆盖；权利边界、更正和下架流程见 [数据资料声明](DATA_NOTICE.md)。

## 项目结构

```text
src/domain/          V2 Canonical 文档、chunk 与来源证据领域模型
src/application/     Canonical 装配、校验、分块、PDF 处理与来源审计
src/ingestion/       PDF 原生解析与 OCR（版面、表格、公式、图片）
src/retrieval/       Qdrant 混合检索与中文向量编码
src/adapters/        清单与文档目录适配
src/evaluation/      公式、表格与区域识别质量评测
data/                原始资料、登记信息与 V2 Canonical 语料
scripts/             装配、入库、审计与验证命令
tests/               自动化测试
```

## 已知限制

- 当前是经过验证的单机 POC，不是生产级高可用系统。
- V2 检索 HTTP API / MCP 接口尚未提供；当前通过脚本与 Qdrant 直接检索。
- 直接 PDF→V2 Canonical 路径（不经 page-intermediate 缓存）为后续工作。
- 模型生成仍可能遗漏补充信息，最终结论应回查引用原文。
- OCR 对复杂表格和低质量扫描件仍需质量门控与人工复核。

## 许可证

项目自行编写的代码和文档采用 [GNU AGPL v3.0 only](LICENSE)。第三方依赖见 [Third-party notices](THIRD_PARTY_NOTICES.md)；第三方资料遵循各自权利状态，详见 [数据资料声明](DATA_NOTICE.md)。

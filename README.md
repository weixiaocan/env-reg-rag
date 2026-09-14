# 环保法规规范证据助手

面向环保法律、法规、标准和技术规范的开源证据型 RAG。系统不仅返回答案，还提供文档、条款、页码和原文定位；当条件不完整或证据不足时，会追问或拒绝生成结论。

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
- OpenAPI 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/v1/health>
- 就绪检查：<http://127.0.0.1:8000/api/v1/ready>

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

`data/raw/` 保存随仓库发布的环保法规规范资料，`data/registry/inventory.csv` 记录文件哈希、页数和重复关系。资料可以按相同的准入、解析、评估和发布机制持续扩展。

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

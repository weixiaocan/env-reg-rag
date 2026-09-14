# 环保法规规范证据助手

面向环保领域法律、法规、标准和技术规范的开源证据型 RAG 项目。系统从持续扩展的资料库中检索可定位、可回查的证据，并在证据充分时生成带引用回答；它不替代工程师、审查人员或法务作出专业判断。

项目已完成 M1–M6，包括数据登记、PDF 解析/OCR、检索基线、RAG 问答核心、网页工作台、分层评估、单机部署/恢复和 MCP 联调。首版正式语料包含 3 份官方文档、29 个检索块；当前进入 P7 开源与面试交付。M6 只在“可演示、可复现、可进入开源准备的单机 POC”范围内通过，不把已知回答波动或单机部署冒充为生产准确率和高可用能力。

## 当前有效文档

- [项目任务看板](docs/PROJECT_BOARD.md)
- [产品规格](docs/PRODUCT_SPEC.md)
- [技术方案](docs/TECH_SPEC.md)
- [领域术语](docs/DOMAIN_LANGUAGE.md)
- [AI 协作开发流程](docs/AI_COLLAB_WORKFLOW.md)
- [开源发布边界与复现说明](docs/OPEN_SOURCE_RELEASE.md)
- [数据资料声明与下架机制](DATA_NOTICE.md)
- [M6 总评估报告](docs/evaluation/M6_FINAL_EVALUATION_REPORT.md)
- [M2 PDF 解析评价计划](docs/evaluation/M2_PARSING_EVAL_PLAN.md)
- [E-01A PyMuPDF 原生解析基线报告](docs/evaluation/E01_PYMUPDF_NATIVE_BASELINE.md)
- [E-01B PaddleOCR 降级路径 Pilot](docs/evaluation/E01B_PADDLEOCR_PILOT.md)
- [E-01C OCR 轻重路径对照](docs/evaluation/E01C_OCR_PROFILE_COMPARISON.md)
- [E-01D 24 页路由 OCR 基线](docs/evaluation/E01D_ROUTED_OCR_BASELINE.md)
- [E-01E Docling 结构恢复小样本对照](docs/evaluation/E01E_DOCLING_STRUCTURE_COMPARISON.md)
- [E-01F 40 页解析路由账本](docs/evaluation/E01F_PAGE_ROUTE_LEDGER.md)
- [E-01G Canonical Document 与页面定位验收](docs/evaluation/E01G_CANONICAL_DOCUMENT.md)
- [E-02A 证据单元与检索块基线](docs/evaluation/E02A_EVIDENCE_UNITS.md)
- [M3 Golden Set v1 审核记录](docs/evaluation/M3_GOLDEN_SET_REVIEW.md)
- [E-02B BGE 中文 Dense 精确检索基线](docs/evaluation/E02B_DENSE_RETRIEVAL_BASELINE.md)
- [E-02C Qdrant Dense、BM25、ANN 与 RRF 检索对照](docs/evaluation/E02C_BM25_RRF_COMPARISON.md)
- [E-03 Qdrant 过滤、发布与恢复演练](docs/evaluation/E03_QDRANT_FILTER_RELEASE_RECOVERY.md)
- [E-04A 智谱真实 RAG 冒烟记录](docs/evaluation/E04A_ZHIPU_REAL_RAG_SMOKE.md)
- [技术与界面调研](docs/research/)
- [界面原型](docs/prototypes/rag-evidence-workbench-prototype.html)
- [LLM Provider 接入调研](docs/research/LLM_PROVIDER_INTEGRATION_2026-09-07.md)

所有活跃项目文档统一维护在 `docs/` 下。仓库根目录不再存放重复的 PRD、架构稿、面试稿或阶段性说明。

## 当前阶段

1. 产品规格已批准。
2. 技术方案 Approved v1.1 已批准；文档准入、处理运行和语料版本采用三类独立状态。
3. M1“文档登记与首批样本确认”已完成实验范围验收：23 份现有 PDF 已完成机器盘点，8 份 M2 实验样本已批准并登记到 SQLite。
4. M2“解析/OCR 与 Canonical Document”已完成代表页验收。
5. M3“证据单元与检索基线”、M4“RAG 问答核心”、M5“网页工作台”和 M6“评估、部署与 MCP”均已完成；当前进入 P7 开源与面试交付。

M1 数据登记产物集中在 `data/registry/`：

当前公开仓库在 `data/raw/` 携带 27 个 PDF 文件、共 1252 页：23 份原候选语料和 4 份官方核验副本。按 SHA-256 去重后是 22 个独立文件内容，5 个完全重复副本为保留来源与核验关系而继续保留。公开携带不代表全部文件已通过文档准入或进入正式回答语料；正式语料仍以版本化发布清单为准。

- `inventory.csv`：可重新生成的机器盘点结果，包括 SHA-256、页数、文字层覆盖率和文件关系。
- `document_reviews.csv`：人工维护的官方来源、日期、效力和本地文件核验记录。
- `experiment-sample-v0.json`：已批准用于 M2 解析实验的 8 份样本，不代表正式问答语料。

M2 当前已固定实验输入：

- `m2-page-probe.csv` 与 `m2-page-probe.meta.json`：235 页的文字层/图像信号，以及输入 SHA、工具版本和解析警告记录。
- `m2-page-sample-v0.csv`：从 8 份文档中固定的 40 个代表页面；其中的解析路径是待实验验证的假设。
- `docs/evaluation/M2_PARSING_EVAL_PLAN.md`：统一评价维度、硬门控和人工锚点范围。

运行 PyMuPDF 原生解析基线：

```powershell
uv --cache-dir .uv-cache venv .venv
uv --cache-dir .uv-cache pip install --python .venv\Scripts\python.exe -r requirements-m2.txt
.venv\Scripts\python.exe scripts\run_m2_native_baseline.py --artifact-prefix m2-native-local-rerun
```

该基线对固定40页保存原生文本、blocks、rawdict、bbox、输入哈希、耗时和人工锚点字面匹配结果。现有 v1 结果位于 `data/eval_results/m2-native-pymupdf-1.28.2-v1-*`。

运行 PaddleOCR 代表页 pilot：

```powershell
$env:PADDLE_PDX_MODEL_SOURCE = "BOS"
.venv\Scripts\python.exe scripts\run_m2_ocr_baseline.py `
  --sample-id M2-P001 --sample-id M2-P006 `
  --artifact-prefix m2-ocr-local-pilot
```

当前 PaddleOCR 路径使用 PP-StructureV3，并把 OCR 像素坐标换算成 PDF point 坐标。完整表格管线在 CPU 上较重，复杂表格和公式仍需质量门控；详见 E-01B 报告。

重新生成机器盘点表：

```powershell
python scripts/data_inventory.py
```

按已批准清单登记 8 份实验样本：

```powershell
python scripts/register_experiment_corpus.py
```

该命令会校验清单、盘点表、来源复核表和本地 PDF 的 SHA-256，再把文档登记为“仅限实验”。默认数据库为 `data/registry/corpus_registry.sqlite3`，它是本地运行产物，不提交 Git；重复执行不会重复登记或重复写入准入事件。

> 数据资料边界：这些 PDF 来自公开网络渠道，用于学习、研究、工程验证和非商业演示；公开可取得不等于已经取得再分发授权，非商业用途声明也不能替代授权。第三方 PDF 不受项目 AGPL 许可覆盖，权利、使用限制和下架流程见[数据资料声明](DATA_NOTICE.md)。

公开仓库排除从 PDF 派生的全文、证据块、检索块、OCR 页面结果、原页截图和运行数据。克隆仓库后无需另行下载当前 27 个 PDF，但仍需按[开源发布边界与复现说明](docs/OPEN_SOURCE_RELEASE.md)重建派生产物。

运行 M1 测试：

```powershell
python -m unittest tests.test_data_inventory tests.test_corpus_lifecycle -v
```

依赖与运行命令会在第一个可执行技术实验中按实际版本重新生成；旧 Chroma/BM25 演示依赖不再作为当前方案。

## M4 回答模型配置

M4 通过 LangChain 的 `ChatOpenAI` 连接 OpenAI-compatible Chat Completions，当前默认供应商为智谱。复制 `.env.example` 中的相关配置到本地 `.env`，至少填写：

```dotenv
LLM_PROVIDER=zhipu
LLM_MODEL=glm-5.2
ZAI_API_KEY=your-real-key
```

安装 M4 依赖：

```powershell
uv --cache-dir .uv-cache pip install --python .venv\Scripts\python.exe -r requirements-m4.txt
```

密钥只保存在被 Git 忽略的 `.env` 中。后续切换 OpenAI 或 DeepSeek 时仍复用同一 `AnswerGenerator` 和引用校验，不改查询核心；具体模型名称须以切换时的官方文档和项目评测为准。

填写智谱 Key 后，可执行一次会产生 API 费用的模型接入冒烟测试：

```powershell
.venv\Scripts\python.exe scripts\run_m4_zhipu_model_smoke.py
```

该脚本只验证“LangChain → 智谱 → Pydantic → 引用校验”的回答链路，使用固定测试证据；它不代表 Qdrant 生产检索适配已经接通。

完整的 `Qdrant RRF → EvidencePack → 智谱 → 引用校验` 冒烟命令：

```powershell
.venv\Scripts\python.exe scripts\run_m4_real_rag_smoke.py
```

两条命令都会产生真实模型费用。完整冒烟的已验证结果和限制见 E-04A；一次成功不代表已完成批量回答评估。

## M6 Docker Compose 运行

准备好被 Git 忽略的 `.env` 后，构建 CPU-only 应用镜像并启动完整栈：

```powershell
docker build -t drainage-rag-app:local .
docker compose up -d --no-build
docker compose ps
```

Compose 会依次启动 Qdrant、幂等索引初始化任务和 FastAPI。进程存活与业务就绪分开检查：

```powershell
Invoke-WebRequest http://127.0.0.1:8000/api/v1/health
Invoke-WebRequest http://127.0.0.1:8000/api/v1/ready
```

普通停止使用 `docker compose down`，Qdrant 索引保留在 `drainage-rag-qdrant-storage`。不要把 `docker compose down --volumes` 当作日常停止命令，因为它会删除可重建但需要重新计算的向量投影。构建、冷启动和重启持久化的实测证据见 E-06C。

正式语料的卷外快照、恢复副本发布与 alias 回滚演练：

```powershell
.venv\Scripts\python.exe scripts\run_m6_formal_recovery_rehearsal.py
```

卷外备份写入被 Git 忽略的 `data/backups/qdrant/`。该本地备份用于验证恢复流程，不替代正式环境的异地复制、加密、访问控制和保留策略；实测证据见 E-06D。

## M6 只读 MCP

安装 M6 依赖后，由 Agent host 以 stdio 启动服务；不要在普通终端中把它当成交互式命令使用：

```powershell
uv --cache-dir .uv-cache pip install --python .venv\Scripts\python.exe -r requirements-m6.txt
.venv\Scripts\python.exe -m src.mcp_adapter.main
```

服务提供 `answer_with_evidence`、`search_evidence` 和 `get_document_evidence` 三个只读工具，并提供 `evidence://{evidence_id}` 资源。Codex 等宿主应把 Python 命令、`-m src.mcp_adapter.main` 和项目根目录分别配置为 MCP 的 `command`、`args` 和 `cwd`，同时把工具 allowlist 限定为上述三项；具体字段以宿主当前文档为准。

直调、HTTP、stdio MCP 一致性测试：

```powershell
.venv\Scripts\python.exe scripts\run_m6_mcp_consistency.py
```

实测结果和真实 Codex Agent host 调用记录见 E-06E。当前交付是本机 stdio MCP，不宣称已经具备远程认证、多租户或生产并发能力。

## License

项目自行编写的代码和文档按 GNU Affero General Public License v3.0 only（`AGPL-3.0-only`）发布，以匹配当前 PyMuPDF 开源许可路线。第三方 PDF、模型权重和第三方运行组件不属于该许可授权范围；详见 [LICENSE](LICENSE)、[数据资料声明](DATA_NOTICE.md)、[第三方许可清单](THIRD_PARTY_NOTICES.md)和[开源发布边界](docs/OPEN_SOURCE_RELEASE.md)。

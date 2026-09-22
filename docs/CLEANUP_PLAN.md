# 仓库清理规划

> 日期：2026-09-22  
> **状态：已按「无用直接删」执行一轮（见文末执行记录）。**  
> 原则：只留 V2 主链路还要用的；法律声明勿动。

## 0. 主链路（清理时必须保住）

分块前：`build_corpus` →（`recognize_corpus_formulas`）→ `assemble_corpus_v2` → V2 Canonical  
分块后：`ingest_corpus_v2` →（可选 `enrich_corpus_v2_payloads` / `regenerate_corpus_manifest`）→ FastAPI + `src/server/static/index.html`  
辅助入口：`data_inventory`、`audit_pdf_sources`（build 文档里的前置步骤）

---

## 1. 根目录 Markdown / 法律文件

| 文件 | 建议 | 理由 |
|------|------|------|
| `README.md` | **保留根目录** | 项目门面；可后改内容，但别塞进 docs 后根目录空掉 |
| `DATA_NOTICE.md` | **保留根目录** | 第三方 PDF 数据权利声明，开源仓库惯例放根目录 |
| `THIRD_PARTY_NOTICES.md` | **保留根目录** | 同上，第三方告知 |
| `LICENSE` | **保留根目录** | AGPL，不可挪走当「过期文档」删 |
| `docs/*.md` | 见下节 | 技术文档归 docs |

**结论：不要把 DATA_NOTICE / THIRD_PARTY_NOTICES 挪进 docs 后删根目录副本；最多 docs 里放链接说明。**

---

## 2. docs/

| 文件 | 建议 | 理由 |
|------|------|------|
| `SCOPE_AND_DEFERRED.md` | **保留** | 刚钉死的范围 |
| `CLEANUP_PLAN.md` | **保留** | 本清理规划 |
| `ARCHITECTURE.md` | **保留或瘦身** | 有用；若与代码不符，按代码改文档 |
| `v2-canonical-schema-design.md` | **保留** | V2 schema 设计底稿 |
| `EVALUATION.md` | **核对后决定** | 若只描述旧评估且已过期 → `_archive/docs/` |
| `PDF_PROCESSING.md` | **高概率归档** | 很长的旧「修正方案」，易与当前 V2 口径冲突 |

---

## 3. data/

| 目录 | 建议 | 理由 |
|------|------|------|
| `data/raw/` | **保留** | 源 PDF；DATA_NOTICE 覆盖对象 |
| `data/registry/` | **保留** | corpus 指针、inventory、金标准、审计 |
| `data/canonical/` | **保留** | V2 / 中间产物，ingest 依赖 |
| `data/model_runtime/` | **保留（可 gitignore 体积）** | 本地模型缓存，代码有引用 |
| `data/review/` | **暂留或归档复核产物** | 少量引用；非主问答必经 |
| `data/observability/` | **保留** | 运行追踪输出位 |
| `data/backups/` | **可清内容** | 代码几乎无引用 → `_archive/data-backups/` |
| `data/evidence/` | **空目录可删或留占位** | 当前 0 文件 |
| `data/retrieval/` | **空目录可删或留占位** | 当前 0 文件 |
| `data/regulations/` | **核对后决定** | 几乎无代码引用 |
| `data/golden/` | **合并或归档** | 与 `data/registry/*gold*` 并存，先查谁被用 |

若存在 `data/corpus_formulas/`：**保留**（assemble 公式缓存主路径）。

---

## 4. scripts/

### A. 主链路 — 禁止删
- `build_corpus.py`
- `recognize_corpus_formulas.py` / `recognize_page_formulas.py`
- `assemble_corpus_v2.py`
- `ingest_corpus_v2.py`
- `enrich_corpus_v2_payloads.py`
- `regenerate_corpus_manifest.py`
- `data_inventory.py`
- `audit_pdf_sources.py`
- `local_table_worker.py`

### B. 诊断 / 抽检 — 先搬到 `scripts/diag/`（搬，不删）
- `recognize_pdf_formula.py` / `recognize_pdf_tables.py`
- `evaluate_formula_acceptance.py` / `evaluate_region_detection.py` / `evaluate_table_cells.py`
- `discover_pdf_regions.py`
- `audit_formula_context.py` / `audit_unified_pdf.py`
- `triage_corpus_formulas.py`
- `render_formula_review_sheets.py`
- `review_document_relations.py`
- `verify_v2_canonical_single.py` / `verify_v2_chunk_trace.py`

### C. 归档候选
- `assemble_cecs758_v2.py`（旧旁路；主路径是 `assemble_corpus_v2.py`）

---

## 5. tests/

不要因「看起来多」批量删。先跑 `pytest`，绿的默认保留；仅当目标模块已废弃再删对应测试。

---

## 6. tmp/ 与其它

| 路径 | 建议 |
|------|------|
| `tmp/`（约 660 个 patch/debug/旧 clone） | 整目录移 `_archive/tmp-YYYYMMDD/` |
| `.pytest_cache` / `__pycache__` | 可删；gitignore |
| `compose.yaml` | **保留**（当前主要起 Qdrant） |
| `Dockerfile` | **保留** |
| 缺失的公式对照/复核 html | 非本轮必补；可选去掉死链 |

---

## 7. 执行顺序

1. 只归档不永删：建 `_archive/`，移入 `tmp/`、过期 docs、旁路脚本  
2. diag 归类并修正引用  
3. 空 data 目录处理  
4. 跑 pytest + 主脚本冒烟  
5. 第二轮再 git rm 确认无用的归档  
6. 勿动 LICENSE / DATA_NOTICE / THIRD_PARTY_NOTICES / 已发布 corpus

## 8. 非目标

不实现 P3/P6；不补公式对照页；不改检索业务逻辑。


---

## 9. 执行记录（2026-09-22）

已删除：
- 本地 	mp/（本就不进 git）
- docs/PDF_PROCESSING.md、docs/EVALUATION.md
- 无主链路引用的诊断/旁路脚本 14 个（含 ssemble_cecs758_v2.py 与 evaluate/recognize_pdf/verify/audit/triage 等）
- 仅服务上述脚本的 6 个测试文件
- 空目录 data/evidence、data/retrieval；data/golden/；data/backups/qdrant
- 修正 ssemble_corpus_v2 / 	est_canonical_assembly 中对旧脚本的文案引用

未删：
- LICENSE / DATA_NOTICE.md / THIRD_PARTY_NOTICES.md / README.md
- 主链路 scripts 与 src/
- data/raw、
egistry、canonical（canonical 本就 gitignore）
- data/model_runtime/：本地仍在，**从未被 git 跟踪**（已在 gitignore），保留供本地 embedding 使用

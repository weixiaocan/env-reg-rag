# R2d 交付报告（部分完成：代码已验证，rollout 被本地数据缺失阻断）

## 结论

R2d 的通用规则实现、聚焦测试、自审、临时 worktree 清理检查和仓库根目录完整 pytest 已完成；23 文档 rollout **未能重跑**。阻断原因不是额度耗尽，也不是规则运行失败，而是当前工作区的 `data/raw/`、`data/model_runtime/`、`data/canonical/` 均为 0 个文件；任务所需 `data/canonical/corpus-0ae8a9423e2f-documents.jsonl` 不存在，父目录 `D:\huangxh\AI_Projects_100` 内也未找到同名候选、目标源 PDF 或对应独立 OCR run。

因此本报告不声称 R2d 已通过全量验收，不声称金标准、正文重号、相邻重复、正文缺号或 A2 短块未回退，也不把仓库中遗留的 R2c rollout 产物当作本轮结果。本轮没有 ingest/publish，没有修改 `data/registry/corpus-current.json`，没有调用外部 LLM，没有下载模型，也没有删除任何源数据或 registry/eval 文件。

## 已完成实现

1. 删除 `k0`/`tx` 白名单，改为通用的“变量符号 + 双破折号 + 中文说明”证据规则。支持 Latin/Greek 字母、数字与下划线变量；只有完整来源后缀与合并结果严格归一化相等时才恢复前缀。`共N个` 自洽恢复规则保留。
2. 目录规则识别“标题 + 连续至少 4 个引导点 + 页码”；命中后整页标记为 `toc_navigation_only`，Canonical 保留但 chunker 不投影到正文。普通正文省略号有反例测试。
3. 封面/扉页使用通用结构判断：仅在首个“前言/目次/1 总则”之前，且 PP 标签为 `doc_title`/`header` 或区域满足居中大字几何条件时标记为 `front_matter`；不读取文件名作判断。Canonical 保留，chunker 不投影到可回答正文。
4. 只含 `(n)` 或 `(x.x.x-n)` 的紧邻 text 元素，在同页、紧随公式且几何上位于公式右侧或下方并相邻时并入公式：编号写入公式内容，原编号 bbox 作为 context source span 保留，独立 text 不再重复成块。远距离同一行反例不会合并。
5. 引导句与列项使用相邻元素、同 section path、条文以冒号结束、后项具有通用列项标记等条件配对：组合在 `token_budget` 内时两者及其 element IDs 同处一个 chunk；超限时引导句先保留自身 chunk，后续每个列项分片通过局部 section 前缀携带“条号 + 引导句”。没有按 GB50268 文件名、页码或条号特判。
6. `TextRole` 增加 `toc_navigation_only`、`front_matter`，并保持处理状态、质量状态与检索投影角色分离。
7. `.claude/tmp-r2c-baseline` 在本轮开始时已不存在且没有登记 worktree；执行了 `git worktree prune`，随后 `git worktree list --porcelain` 只列出当前主 worktree。

## 改动文件

- `src/application/canonical_assembly.py`
- `src/application/v2_chunker.py`
- `src/domain/canonical_document.py`
- `src/ingestion/block_assembly.py`
- `src/ingestion/ocr_region_text.py`
- `tests/test_middle_layer_v3.py`
- `tests/test_v2_chunker.py`
- `.claude/handoff/reports/R2d.md`

工作区原有未跟踪 `data/observability/` 与 `data/quality/` 文件未修改、未删除、未纳入提交。

## 执行命令与原始结果

### 聚焦测试

```powershell
.venv\Scripts\python.exe -m pytest -q tests/test_middle_layer_v3.py tests/test_v2_chunker.py tests/test_canonical_document_v2.py tests/test_canonical_validation.py
```

JUnit 计数：`138 passed, 0 failed, 0 errors, 0 skipped`。

覆盖通用变量定义、目录及正文省略号反例、无文件名封面判断、公式编号相邻/远距离反例、角色过滤、短列表同块、超长列表主干前缀，以及 Canonical role/schema 契约。

```powershell
git diff --check
```

结果：退出码 0；仅输出 Git 的 LF/CRLF 工作区提示，无 whitespace error。

### Rollout

```powershell
.venv\Scripts\python.exe eval\audits\middle_layer_v3\run_rollout.py
```

结果：退出码 1，在读取输入前失败：

```text
FileNotFoundError: data/canonical/corpus-0ae8a9423e2f-documents.jsonl
```

只读核对：

```text
data/raw files=0
data/model_runtime files=0
data/canonical files=0
```

并在 `D:\huangxh\AI_Projects_100` 递归精确查找候选文件、`城镇内涝防治技术规范.pdf` 及两个已知 OCR run manifest，均无结果。没有尝试伪造、降级或以旧 chunks 代替本轮 rollout。

### 完整 pytest（仓库根目录）

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m pytest -q --tb=no
```

精确 JUnit 计数：`1255 tests：1172 passed, 33 failed, 31 errors, 19 skipped`，耗时 `37.717s`（计数复跑）。典型错误均为缺少真实语料制品，例如：

```text
FileNotFoundError: data/canonical/corpus-37456321a968-documents.jsonl
KeyError: <gold evidence doc_file>  # Canonical 文档集合为空
```

受影响测试组为 `test_canonical_assembly.py`、`test_fact_evidence_reconciliation.py`、`test_golden_dataset.py`、`test_t09_formula_explanation_binding.py`。没有修改测试来掩盖这些失败。

## Rollout 指标状态

R2c 可复现基线为：金标准 27/30、正文 missing 27、正文 duplicate 4、相邻重复 19（流水线引入 0）、A2 短块 101/3821（2.6433%）。这些数字仅是 R2c 历史结果。

R2d “after” 数字均为 **未生成/未验证**：

| 指标 | R2c before | R2d after |
| --- | ---: | --- |
| 金标准 | 27/30 | 未重跑 |
| 正文 missing | 27 | 未重跑 |
| 正文 duplicate | 4 | 未重跑 |
| 相邻重复 / 流水线引入 | 19 / 0 | 未重跑 |
| A2 短块 | 101/3821（2.6433%） | 未重跑，无法分类剩余短块 |
| Canonical 校验 | 23/23 | 未重跑 |

## 产物与哈希

以下 rollout 文件仍是 **R2c 遗留产物，未被本轮更新**：

| 路径 | SHA-256 |
| --- | --- |
| `eval/audits/middle_layer_v3/rollout/metrics.json` | `b41af77884e96c0b2d53631c1e6d1600921aa851efa605dc54f2a9a8519147fd` |
| `eval/audits/middle_layer_v3/rollout/exam.json` | `47c8993ec23d9cad7f7580fdeaff51a41ce1793fa789975b6be91526b97ba46c` |
| `eval/audits/middle_layer_v3/rollout/duplication_audit.json` | `7aed126b808dde219d4b1973769e952480b7ce638d28490e9132648312597da8` |
| `eval/audits/middle_layer_v3/rollout/chunks.jsonl` | `fac22eb0557f835811cbba4a56875fcf0c752997ce562f354b9bf94e3c5721d0` |

发布指针保持 `corpus-0b9a1fd84e3d`，`data/registry/corpus-current.json` SHA-256 为 `dc76605fdf8af71211817471261fa9af2939311962a0c99ce04b99989e7034c7`。

## 原图复测与质量边界

协议要求数据内容改动至少给出 3 个“原图文字 / 旧文本 / 新文本”对照。本轮因 `data/raw/` 为 0 个文件且无法在父目录找到源 PDF，无法执行 R2d 新实现对应的原图复测；R2c 报告中的旧对照不能证明本轮规则结果。故本轮没有任何新增人工内容质量结论，代码测试通过也不等于 OCR、目录、封面、公式编号或分块内容已通过人工核验。

## 已知限制与发现但未处理

1. 必须恢复与 registry 哈希一致的源 PDF、`corpus-0ae8a9423e2f-documents.jsonl`、公式/独立 OCR 本地制品后，重新运行 rollout、金标准、连续性/重复审计、A2 短块分类与至少 3 个原图前后对照；在此之前 R2d 不满足全量完成定义。
2. Registry 仍声明候选 page-intermediate SHA-256 `b66a745e5ae6faad51edb5a1459e7b4aa9f9d6e0e4f9be8928f9c679036ca1e3`，但文件不存在；这是运行时数据一致性缺口，本轮未改 registry 口径或重建候选。
3. 当前 `.venv\Scripts\python.exe` 在 traceback 中把含中文的仓库路径显示为乱码；这不影响 138 个聚焦测试，但会降低日志可读性。未在本任务范围内更换解释器或修改全局环境。
4. 现有 rollout 文件与本轮代码不匹配；审核者不得用其哈希或历史 27/30、101/3821 数字判定 R2d 通过。

## 轮次记录

- R2d 第 1 轮：接续未提交工作区，完成通用规则、补足同块/超限与几何反例、自审及聚焦测试；rollout 和依赖真实语料的完整测试被本地数据目录为空阻断。按任务卡“额度/阻断时先提交可验证部分并如实说明”的原则提交本轮可验证部分。

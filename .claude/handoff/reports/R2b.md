# R2b 完成报告

## 结论

R2b 已完成代码、两套本地 OCR 证据、23 文档 rollout 与完整测试。没有执行 ingest/publish，没有修改 `data/registry/corpus-current.json`，没有重建 Qdrant，也没有调用外部 LLM 或下载模型。

工程执行完成不等于内容质量全部通过：最终金标准为 27/30，TXT-07 已由本轮修复；TXT-06 的截断问题已修复，但三源多数仍把人工金标准中的“监测”识别成“检测”，所以仍未通过；TXT-15、TXT-16 是既有未通过项。本报告不把自动 OCR 投票或 Canonical 校验表述为人工内容核验。

## 实现

### 1. 伪原生扫描页通用路由

- `src/ingestion/native_pdf.py` 新增通用判据：单张页面图像覆盖率 `>= 0.80`、存在文字层、且文字 trace 全部为 PDF render mode 3（不可见文字），路由为 `scanned_with_text_layer`。
- 反例保持 `native`：页内小图；整页背景图但正文为可见矢量文字。
- 新 PDF 在 `AutomaticPageExtractor` 中进入 quarantine，原因 `scanned_with_text_layer_requires_ocr_vote`。
- 既有 page-intermediate 在 v3 rollout/组装前读取源 PDF 并应用同一规则，不重新提取页面。
- 提取配置号保持 `auto-page-v9`，没有使用上一轮草稿中的 v10，因此不会使无关既有缓存失效。未来新增 PDF 会在正常首次提取时应用新判据；既有候选走组装阶段补标记。

实测通用判据命中：

| 文档 | 背景口径 | 本轮实测 | 最终路由 |
| --- | ---: | ---: | --- |
| 城市排水工程规划规范.pdf | 59/60 | 60/60 | `scanned_with_text_layer` |
| T_CECS_758-2020城镇排水管道混接调查及治理技术规程.pdf | 75/75 | 75/75 | `scanned_with_text_layer` |

任务背景的第一份文档为 59/60，但当前源 PDF 与当前严格判据实际为 60/60；rollout 的逐页 `scanned_text_layer_evidence` 已记录覆盖率、图像数、文字层和不可见文字层证据。本轮采用实测结果，没有为迎合历史数字加入文件名或页码特判。

### 2. 135 页本地 OCR 与三源投票

仅对上述两份文档运行本地 PP-OCRv6：

| 来源 | run id | selected/completed | manifest SHA-256 |
| --- | --- | ---: | --- |
| pp_clean | `ocrv6-232ae585c76c8403` | 135/135 | `53c2b898f5dd8b636ca2628294841b89f7ed801c002b0102b5b01ffcd8df9827` |
| pp_raw | `ocrv6-9873a4b3ba7ce25b` | 135/135 | `bc4df2fa2b7425594305e47cde257d321b6e61c10781d20bef8028d0c985c1ba` |

目标文档使用新 run；其余文档继续使用 R2 的 pp_clean `ocrv6-fe621f3631762fd6`（602/602）和 pp_raw `ocrv6-ef06411e1606ba35`（737/737）。每份文档在 metrics 中记录实际 run id，没有伪造合并 run。

嵌入文字层作为 `primary`，与 pp_raw、pp_clean 进入现有三源投票。修复了一个实际丢行问题：已明确分配 region 的跨边界 OCR 行不再被第二次 `>=50%` bbox 包含检查删除。TXT-06 最后一行“对雨水接入污水管道进行判定。”因此进入最终 chunk。

### 3. 条文连续性口径

- 正文与“条文说明”分别统计；条文说明重复条号不再计入正文 duplicate。
- 公式、表号、图号具有独立排除规则。
- 对真实发现的 OCR 黏连条号作保守统计归一化：仅当三位以上末级编号原样出现在中文正文开头时，去除黏入的尾部列表数字。本轮修正 `4.1.154 -> 4.1.15` 与 `7.5.101 -> 7.5.10` 两处，消除了两个巨型假缺号段。
- 每个缺号包含 `between_pages`，并附候选页 primary/pp_raw/pp_clean 是否可用、是否出现该条号的 `source_evidence`。

最终全库连续性：正文 missing 29、正文 duplicate 100；条文说明 missing 176、duplicate 0。正文 missing 分布：

| 文档 | 正文缺号数 |
| --- | ---: |
| 建筑与小区雨水控制及利用工程技术规范 | 2 |
| 福建省城镇老旧小区改造技术标准 | 1 |
| 武汉市排水管道混错接改造技术规程 | 7 |
| GB50014-2021 室外排水设计标准 | 8 |
| 城市排水工程规划规范 | 2 |
| GB/T 47884-2026 | 1 |
| 多普勒法在线扫描式排水管道流量测量方法标准 | 6 |
| T/CECS 758-2020 | 2 |

逐项 label、页码边界和三源证据位于 `rollout/metrics.json -> documents[].v3.clause_number_continuity.missing[]`。

## Rollout 结果

最终命令：

```powershell
.venv\Scripts\python.exe eval\audits\middle_layer_v3\run_rollout.py
```

- Canonical 校验：23/23 passed；这里只表示工程不变量校验通过。
- 处理 failures：0。
- review sample render failures：0。
- chunks：4703。
- 三源 provenance 缺 run id：0。
- 全库 A2 短正文块：95/4179（2.2733%）→ 92/4262（2.1586%）。

两份目标文档的 A2/结构指标：

| 文档 | text body chunks | 短块数/比例（前） | 短块数/比例（后） | OCR provenance elements（前→后） | 正文 missing/duplicate（前→后） |
| --- | ---: | ---: | ---: | ---: | ---: |
| 城市排水工程规划规范 | 191 | 3 / 1.9868% | 2 / 1.0471% | 0 → 481 | 13/23 → 2/0 |
| T/CECS 758-2020 | 205 | 9 / 5.1724% | 10 / 4.8780% | 0 → 580 | 18/29 → 2/0 |

## 金标准前后

| 指标 | R2 前 | R2b 后 |
| --- | ---: | ---: |
| text pass | 16/20 | 17/20 |
| structure pass | 15/19 | 16/19 |
| table/numeric pass | 10/10 | 10/10 |
| failed anchors | TXT-06, TXT-07, TXT-15, TXT-16 | TXT-06, TXT-15, TXT-16 |

TXT-07 已恢复人工金标准中的“浓度”并通过。TXT-06 最后一行已经恢复，但 exact gold 仍因“监测/检测”单字差异失败；三源结果均未提供可据以自动替换为人工金标准用字的强证据，因此保留失败，不伪造通过。

## 验证

```powershell
.venv\Scripts\python.exe -m pytest -q tests/test_middle_layer_v3.py tests/test_ocr_region_text.py tests/test_clause_continuity.py tests/test_native_pdf_parser.py
# 43 passed

.venv\Scripts\python.exe -m pytest -q
# 1217 passed；2 warnings；0 failed

git diff --check
# passed（仅 Git 的 LF/CRLF 工作区提示）
```

新增/覆盖的关键失败路径：小图反例、可见矢量文字背景图反例、旧 page-intermediate 就地改路由、提取 config 保持 v9、正文/条文说明命名空间、公式/表/图号排除、OCR 黏连条号归一化、显式 region assignment 的跨边界末行保留。

## 边界与未做事项

- 未 ingest、未 publish、未切换 registry 指针、未改正式 Qdrant collection。
- 未执行全量 PDF 重新提取；只读取源 PDF 做路由判定，并对目标 135 页运行两种本地 OCR。
- OCR run 的自动 `verified_passed/manual_review_required` 只是机器比对状态，不是人工审核结论。
- TXT-06、TXT-15、TXT-16 仍未通过人工金标准；内容质量不能宣称全部通过。
- R2c 不包含在本提交中。

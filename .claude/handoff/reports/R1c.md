# R1c 交付报告

## 结论

R1c 范围内实现与 GB 55027-2022 试点重跑已完成。v3 OCR 页不再调用 `independent_ocr_fusion` 的逐行 replace/insert；文字来源改为本地页面预处理后 PP-OCRv6 按 region 取行，主 OCR 只保留无 PP 行纵向覆盖的兜底行。legacy 路径、结构层规则、chunker、准入、ID 计算、registry 指针和发布链路未改。

最终试点的自动证据为：GB 55027 金标 4/4，Canonical 校验通过，正文页面家具 0，句末标题 0，正文面包屑 0，超长 section 标题 0，分项根节点 0，短正文 chunk 0/151，条文编号缺号/重复/非法跳号均为 0。完整 pytest 到 100% 且退出码 0；收集数为 1188。以上仅证明覆盖到的工程契约与固定考试通过，不等于逐页内容质量已经人工通过。

## 做了什么

1. 新增确定性的本地 OCR 图像预处理：浅灰阈值、红色阈值均可配置；浅灰处理保留紧邻深色正文笔画的抗锯齿边缘，算法版本随 run 配置进入哈希。模型目录显式固定为仓库已有的 `data/model_runtime/independent_ocr/rapidocr-3.9.2-ppocrv6`，未下载模型。
2. 扩展 `run_independent_ocr_verification.py`：支持 `--preprocess`、阈值、文档 SHA、物理页及 `--ocr-routed-only`；不可变 run manifest 记录预处理算法和参数，旧的无预处理页面不会被误复用。
3. 新增 PP-first region 文字装配：行中心点进入 region（2 pt 容差），整行水印与单字低分噪声过滤，region 外非噪声 PP 行生成独立 synthetic region，主 OCR 无纵向 PP 覆盖时兜底；同位置主 OCR 的条款编号可修复 PP 漏号/粘连号。
4. Canonical 文本元素新增审计字段 `ocr_text_sources`，逐行记录 `pp_clean` / `primary_fallback`、run_id、manifest SHA-256、预处理参数、行号、置信度和编号保留标志；provenance 注册 `ocr-region-1`。OCR 内容仍为 `needs_review`。
5. 新增条文连续性指标，列出同节 `N.N.k` 的缺号、重复、非法格式和非法跳号，并写入试点 `metrics.json`。
6. 重跑 GB 55027 的 20 页本地 OCR、Canonical、chunks、逐页 review 与 metrics；增加 6 页 × 4 组阈值校准制品。

## 阈值校准

原图样本：GB 55027 第 4、8 页；GB/T 28592 第 1、4 页（第 4 页红章）；GB 50268 第 34、117 页（复印灰底/表格）。`r1c_threshold_calibration.json` 的“潜在漏行”是与同模型、同 2.0 倍渲染、无预处理 run 做归一化匹配后的候选，随后对照原图区分正文和结构化表格；它不是人工权威质量结论。

| 文档页 | 无预处理 潜在漏行/正文误删 | 150 | 120 | 100 |
| --- | ---: | ---: | ---: | ---: |
| GB 55027 p4 | 0/0 | 1/0（孤立“信”噪声） | 1/0（同左） | 1/0（同左） |
| GB 55027 p8 | 0/0 | 0/0 | 0/0 | 0/0 |
| GB/T 28592 p1 | 0/0 | 0/0 | 0/0 | 0/0 |
| GB/T 28592 p4 | 0/0 | 0/0 | 0/0 | 0/0 |
| GB 50268 p34 | 0/0 | 1/0（表格数值 95→96） | 11/0（均在表格） | 40/0（均在表格） |
| GB 50268 p117 | 0/0 | 0/0 | 1/0（表格 `≤∆s`→`LS`） | 0/0 |

选择默认 120：任务卡指定的正文目标在 120 全文 run 上金标 4/4、编号连续性 0 缺陷；150 全文 run 把 p4 的“工程”识别为“I程”，导致 TXT-08 失败；100 对灰底页侵蚀最明显。120 在 GB 50268 表格内的差异不会进入本轮 region 正文，结构化表格继续走既有装配路径，未被本轮替换。此取舍不表示表格 OCR 质量通过。

## R1 问题点前后

| 问题点 | R1 旧结果 | R1c 新结果 | 状态 |
| --- | --- | --- | --- |
| 1.0.1 | 整条缺失 | `1.0.1 为推进生态文明建设和可持续发展…制定本规范。` | 已恢复；原图 p4 复核 |
| 2.1.7 | `2.1.72.1.7…` 重复拼接 | 单一 `2.1.7 城镇污水系统的布局，应符合下列规定：` | 重复前缀已消失 |
| 2.2.10 | `件和主要原2.2.10…` 串条 | 从 `2.2.10 排水工程所用的管材…` 起始 | 串条已消失 |
| 2.2.11 | 中段错乱 | 条文按行连续进入同一 chunk | 顺序已恢复 |
| 2.2.13 | 首行两引擎漏识别 | 首行恢复并形成 `2.2.13` | 部分；仍有 `地下F管`、`安全等级不应低于级` OCR 错漏 |
| 2.3.3 | 首行两引擎漏识别 | `2.3.3 城镇雨水管渠和污水管道应定期进行检测和评估…` | 已恢复；原图 p8 复核 |
| 2.3.5 | 行序错乱并夹入 `金` | 条文顺序恢复，独立 `金` 行被配置化过滤 | 行序/噪声已修；`雨水算` 仍应为原图“雨水箅” |
| 2.3.6 | 行序错乱 | 条文及 1–5 分项连续 | 已恢复；原图 p8 复核 |
| `IZ` | 水印残渣 | 仍出现在既有表 3.4.2 的结构化表格文本 | 未处理；表格逻辑属禁止改动范围 |
| 编号空缺清单 | 3.1.4、3.2.6、3.4.1、4.3.1 等多处缺号，`3.45` | 新 metrics：missing=0、duplicates=0、illegal=0、illegal_jumps=0；`3.4.5`、`4.1 一般规定`、`4.3 污水和再生水处理` 均恢复 | 自动连续性检查通过，仍待逐页原图终审 |

原图复测的具体前/后例：p4 的 1.0.1 从空缺恢复为完整条文；p8 的 2.3.3 从首行缺失恢复为完整两行；p8 的 2.3.5 从错序和独立 `金` 恢复为连续条文且去除 `金`；p8 的 2.3.6 从错序恢复为主句和 1–5 分项顺序。上述为本轮本地原图核对，不替代 Claude 的独立逐页审核。

## R2 运行命令

当前 `corpus-0ae8a9423e2f` 实际有 15 份含 OCR 路由页的文档、602 个 `full_ocr`/`hybrid` 页面，并非任务卡文字中的 10 份。以下命令按实际制品覆盖全部 OCR 路由页，不包含仅因 structure-audit 信号入选的原生页：

```powershell
.venv\Scripts\python.exe scripts\run_independent_ocr_verification.py `
  --corpus-version corpus-0ae8a9423e2f `
  --ocr-routed-only `
  --preprocess `
  --gray-threshold 120
```

本卡未执行该 602 页全量命令。

## 改动文件

- `.claude/handoff/MIDDLE_LAYER_V3_SPEC.md`
- `.claude/handoff/reports/R1c.md`
- `config/middle_layer_v3.json`
- `scripts/run_independent_ocr_verification.py`
- `src/ingestion/ocr_preprocessing.py`
- `src/ingestion/ocr_region_text.py`
- `src/ingestion/block_assembly.py`
- `src/evaluation/clause_continuity.py`
- `src/application/canonical_assembly.py`
- `src/domain/canonical_document.py`
- `tests/test_ocr_region_text.py`
- `tests/test_clause_continuity.py`
- `eval/audits/middle_layer_v3/analyze_r1c_calibration.py`
- `eval/audits/middle_layer_v3/r1c_threshold_calibration.json`
- `eval/audits/middle_layer_v3/run_pilot_gb55027.py`
- `eval/audits/middle_layer_v3/pilot_gb55027/chunks.jsonl`
- `eval/audits/middle_layer_v3/pilot_gb55027/page_review.md`
- `eval/audits/middle_layer_v3/pilot_gb55027/metrics.json`

## 命令与结果

1. 阈值校准：对三份文档各 2 页分别运行无预处理/150/120/100；18 个新预处理页面 run 均由本地既有模型生成。并行运行 100 组时出现两次 OpenCV `ResizeImgError`（资源竞争），随后串行重跑成功；未修改输入与阈值。
2. GB 全文 OCR：
   `.venv\Scripts\python.exe scripts\run_independent_ocr_verification.py --corpus-version corpus-0ae8a9423e2f --file-sha256 60bfe...ca41f --preprocess --gray-threshold 120`
   → selected=20，completed=20，run=`ocrv6-403f2b876d9911e5`。
3. 试点：`.venv\Scripts\python.exe eval\audits\middle_layer_v3\run_pilot_gb55027.py`
   → chunks=151；text/clause 3/3，table/numeric 1/1，failed=[]；Canonical passed。
4. 聚焦测试：
   `.venv\Scripts\python.exe -m pytest -q tests\test_ocr_region_text.py tests\test_clause_continuity.py tests\test_middle_layer_v3.py tests\test_canonical_document_v2.py tests\test_canonical_assembly.py tests\test_independent_ocr_fusion.py`
   → 104 passed，0 failed。
5. 完整测试：`.venv\Scripts\python.exe -m pytest -q`
   → 100%，exit 0，0 failed；2 条既有 warning。`--collect-only` 统计 1188 tests。
6. `git diff --check` → 0 error；只有 Git 对工作区行尾转换的 warning。

## 产物与哈希

| 产物 | SHA-256 |
| --- | --- |
| `data/model_runtime/independent_ocr/runs/ocrv6-403f2b876d9911e5/run_manifest.json` | `94055fa61b60284b0ebdf35d6f83e03421dde7649edf0b8be8e8c6c0b9f8081e` |
| `eval/audits/middle_layer_v3/pilot_gb55027/chunks.jsonl` | `8339622157c3ce6df558c12ae8989c172e70a4573257d2de32e75dd8ee73b89d` |
| `eval/audits/middle_layer_v3/pilot_gb55027/page_review.md` | `f7be369f1fea9e9abf7f58a0c8bcd0fc8eaaf1649614f61e4138ff19fd8ca5e8` |
| `eval/audits/middle_layer_v3/pilot_gb55027/metrics.json` | `32391fba05655e3372072c920e5bec7b681bcaf8cd37904daae38d2ac42cf731` |
| `eval/audits/middle_layer_v3/r1c_threshold_calibration.json` | `a1f1ad24a5865c6e9e4fc87ab85ab77597572695e8328c0f14ebf7b86b042b46` |

## 已知限制

- R1c 自动门槛通过不代表 OCR 全文内容已人工核验。`2 基规定`（原图为“2 基本规定”）、`地下F管`、`安全等级不应低于级`、`雨水算` 等仍是可见 OCR 错漏；保持 `needs_review`，未伪造 passed。
- 既有结构化表 3.4.2 中的 `IZ` 未处理，因为任务卡禁止修改表格结构/装配逻辑；已作为审核坏例保留。
- `data/model_runtime/.../ocrv6-403f2b876d9911e5` 是本地不可变运行制品且被 Git 忽略；提交包含可复跑代码、试点审核制品、run_id 和 manifest 哈希，不包含模型或 run 页面文件。
- 预处理阈值校准只有 6 页；120 是本任务样本和 GB 固定考试上的选择，不能推导为全库内容质量结论。

## 发现但未处理

- 任务卡写“10 份 OCR 文档”，当前候选制品实际为 15 份/602 OCR 路由页；未修改 registry 或缩小文档范围。
- 工作区原有未跟踪的 `data/observability/`、`data/quality/` 文件保持不动，未纳入提交。
- 未 ingest、未 publish、未切换 `corpus-current.json`，未调用外部 LLM，未下载任何模型。

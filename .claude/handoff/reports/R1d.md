# R1d 交付报告

## 结论

R1d 中期审核的 4 项要求已处理完成：

1. 字符级合并改为全行统一坐标对齐，replace 片段只输出一个候选，不再把替换文字追加到行尾。同一来源在同一 region/基线上被拆开的行片段先合并，再进行三源投票。
2. 新增「合并引入长度 >= 4 的相邻重复片段」不变量检查；试点指标 `merge_invariant_violation_count=0`，试点脚本在该值非 0 时返回非零。
3. 生成 `eval/audits/middle_layer_v3/pilot_gb55027/conflicts.md`，编号 1-44：全局对齐后的 41 个实际字符冲突 + 中期审核计数中的 3 个已修复对齐缺陷。每项列出物理页、bbox、三源文本和合并后文本。这个分类是为了不把已消失的对齐伪冲突仍写成实际字符冲突。
4. 增补 3 个审核真实案例的合成化单测、不变量单测，并完成定向测试、GB 55027 试点重跑和完整 pytest。

本次没有 ingest/publish，没有修改 registry 或 `corpus-current.json`，没有调用外部 LLM，没有下载模型。

## 中期审核 3 个案例

| 位置 | PDF 原图 | 修复前 | 修复后 |
| --- | --- | --- | --- |
| 物理页 6，2.1.10 | `乡村污水系统应以县级行政区域为单位实行统一规划，并应因地制宜开展建设和运行。` | `...行政区域为单位实行统一规划，应实行统一规划，...` | `2.1.10 乡村污水系统应以县级行政区域为单位实行统一规划，\n并应因地制宜开展建设和运行。` |
| 物理页 7，2.2.13 | `...主体结构和地下干管，其结构设计工作年限不应低于50年，安全等级不应低于二级。` | `...地下F管，其结构干\n计工作年限...` | `2.2.13 排水工程主要构筑物的主体结构和地下干管，其结构设\n计工作年限不应低于50年，安全等级不应低于二级。` |
| 物理页 8，2.3.5 | `...必须在2h内安放护栏和警示标志，并应在6h内修补恢复。` | `...安放护栏和警示井盖和雨水算缺失或损坏信息后，\n标志...` | `...必须在2h内安放护栏和警示\n标志，并应在6h内修补恢复。` |

原图复测方法：用本地 Poppler 渲染了源 PDF 物理页 6-8，逐页视觉检查上述条文。Poppler 报告 CMap/字体警告，但页面图像可读，上述原文可直接核对。

## 试点结果

- 输出 chunk：151。
- 金标准：4/4（条文/文本 3/3，表格/数值 1/1）。
- 条文连续性：`missing=0` / `duplicates=0` / `illegal=0` / `illegal_jumps=0`。
- 投票统计：整行多数 424，字符合并 66，单源 20（其中 pp_clean 单源 5）。
- 当前全局对齐字符冲突 41；加上 3 个已修复对齐缺陷，审核清单共 44 项。
- 合并不变量违规：0。
- R1c 已修复的重点字串和条文编号没有回退；三个中期审核坏例已从 `chunks.jsonl` 消失。

## 验证命令与结果

1. `.venv\Scripts\python.exe -m pytest tests\test_ocr_region_text.py tests\test_middle_layer_v3.py -q`
   - `20 passed`，退出码 0。
2. `.venv\Scripts\python.exe eval\audits\middle_layer_v3\run_pilot_gb55027.py`
   - 退出码 0；151 chunks；金标准 4/4；条文连续性全 0；不变量违规 0。
3. `.venv\Scripts\python.exe -m pytest -q`
   - 退出码 0，输出进度 100%，0 failures；`.pytest_cache/v/cache/nodeids` 记录 1430 个收集项；2 条警告（Starlette `BlockingPortal` 弃用警告、Qdrant local-mode `search_params` 警告）。
4. `git diff --check`
   - 退出码 0；只有 Git 的 CRLF/LF 工作区提示，无 whitespace error。

## 产物与 SHA-256

| 产物 | SHA-256 |
| --- | --- |
| `eval/audits/middle_layer_v3/pilot_gb55027/chunks.jsonl` | `1c9d53e3f95f7da931d86831acaa59015bdf64a16e88333de991c9c3cf2848ee` |
| `eval/audits/middle_layer_v3/pilot_gb55027/metrics.json` | `8a683702945dd0882e7f7b80f6a818053620abd27b0430f528240a672e617705` |
| `eval/audits/middle_layer_v3/pilot_gb55027/page_review.md` | `e1e68522587027d1c3cae2ae83695937b38b2cd36f5fe54574a328de211acf56` |
| `eval/audits/middle_layer_v3/pilot_gb55027/conflicts.md` | `2b2bc3d4eca85bfe411e4a4436172f32e822945d2df9c7bb441ed718f151f494` |

## 改动文件

- 三源投票与审核修复：`src/ingestion/ocr_region_text.py`、`src/ingestion/block_assembly.py`。
- V3 接线与 provenance/schema：`scripts/assemble_corpus_v2.py`、`src/application/canonical_assembly.py`、`src/domain/canonical_document.py`。
- 试点生成器和产物：`eval/audits/middle_layer_v3/run_pilot_gb55027.py`、`eval/audits/middle_layer_v3/pilot_gb55027/{chunks.jsonl,metrics.json,page_review.md,conflicts.md}`。
- 测试：`tests/test_ocr_region_text.py`。
- 交接材料：`.claude/handoff/tasks/R1d.md`、`.claude/handoff/reviews/R1c.md`、`.claude/handoff/reviews/R1d.md`、`.claude/handoff/reports/R1d.md`。

## 已知限制与未处理项

- 这次只对照原 PDF 复测了中期审核指定的 3 个案例，没有把 44 项机器审计清单写成已逐项人工通过。`conflicts.md` 仍是待审核证据。
- 2.3.5 原图为「雨水箅」，当前三源都为「雨水算」；本轮按审核要求修复的是重复插入，没有超出三源证据手工改字。该内容仍应保持 `needs_review` / locator-only 语义。
- 金标准 4/4、完整 pytest 和不变量通过只证明本轮工程契约与指定试点没有回退，不代表 GB 55027 全文或全库内容质量已获权威/人工核验。
- 工作区中原有的 `data/observability/`、`data/quality/` 未跟踪文件与本任务无关，未修改、未清理、未纳入提交。

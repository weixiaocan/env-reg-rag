# T6a 交付报告（阻断停止）

执行者：Codex

轮次：1/3

日期：2026-10-10

## 结论

T6a 未完成。候选 `corpus-966c89b264cc` 已按现有确定性 mint 逻辑从 `corpus-0ae8a9423e2f` 的同一份 page-intermediate 派生并完成 assemble；但在任何 Qdrant 写入前的离线分块阶段遇到阻断：`src/application/v2_chunker.py` 调用 `_ChunkBuilder._atomic_pieces()` 时传入了不符合当前签名的位置参数，抛出 `TypeError`。按照“遇到阻断先写报告停下”的要求，本轮未修改分块逻辑、未继续 ingest、未生成工作表产物、未跑完整 pytest、未提交。

没有 publish，没有修改 `data/registry/corpus-current.json` 或 alias `corpus_v2_live`，没有创建、写入或删除任何 Qdrant collection。当前正式 collection `corpus_v2__staging__corpus-0b9a1fd84e3d__bi-d70cf457e0cac3fa3abbce0a` 未触碰。

## 已完成部分

1. 读取并遵守 `AGENTS.md`、`.claude/handoff/PROTOCOL.md`、`.claude/handoff/reviews/T5.md`、`.claude/handoff/tasks/T6a.md`、README 与架构/范围文档。
2. 预检确认：公式 run `5d3264515515e228`、独立 OCR run `ocrv6-ef06411e1606ba35`、本地 `BAAI/bge-small-zh-v1.5` 模型和 Qdrant 可用；没有下载模型或重跑 OCR。
3. 使用 `scripts/mint_assembly_candidate.py` 的确定性逻辑，以 assembly profile `t6a-t3-t4-t5-9db83cc` 派生 `corpus-966c89b264cc`。未写 `corpus-candidate.json` 或 `corpus-current.json`。
4. 显式传入公式 run 与独立 OCR run 执行 assemble。结果：23/23 文档成功，0 失败；1,144 页；25,779 元素（text 25,232 / table 251 / formula 210 / figure 86）。assemble 内逐文档运行 Canonical 不变量校验并成功写出 artifact manifest。
5. 生成与当前正式版本 `corpus-0b9a1fd84e3d` 的差异产物。实际运行摘要：semantic changed pages 32；正式版本 25,062 元素，候选 25,779 元素；text 24,516→25,232，table 250→251，formula 210→210，figure 86→86。
6. 按 test-first 增加工作表 v2 位置映射单测与初步生成器代码。红灯为模块不存在；实现后聚焦测试 `13 passed`。由于分块阻断，真实 201+30 工作表和截图尚未生成，这些代码只属于未完成实现，不能视为验收通过。

## 阻断详情

离线命令在 `_collect_chunks()` 内失败，发生于写 `chunk_manifest.json`、计算 build identity 和连接 Qdrant 之前：

```text
Traceback (most recent call last):
  File "<stdin>", line 10, in <module>
  File "scripts/ingest_corpus_v2.py", line 224, in _collect_chunks
    chunks = chunk_document(canonical)
  File "src/application/v2_chunker.py", line 1192, in chunk_document
    builder.emit_structured(element, proj)
  File "src/application/v2_chunker.py", line 688, in emit_structured
    for sub, ws, we in self._split_windows(
  File "src/application/v2_chunker.py", line 532, in _split_windows
    windows = self._pack_windows(body, chunk_size=chunk_size, overlap=overlap)
  File "src/application/v2_chunker.py", line 538, in _pack_windows
    pieces = self._atomic_pieces(body, chunk_size=chunk_size)
  File "src/application/v2_chunker.py", line 628, in _atomic_pieces
    out.extend(self._atomic_pieces(text[s:e], rest,
TypeError: _ChunkBuilder._atomic_pieces() takes 2 positional arguments but 3 positional arguments (and 1 keyword-only argument) were given
```

因此以下验收项无法产生：chunk 数、usage_policy 分布、稳定 build identity、计划 staging collection 名、point 数、staging `source_lookup` 烟测、201+30 工作表、截图哈希、黄金集绑定影响统计和完整 pytest。特别是，任务要求“入库前报告 collection 名”；本轮因 collection 名的确定性输入（chunk manifest/build identity）无法生成，故没有猜测或手工编造名称，也没有执行入库。

## 运行过的命令及结果

1. 本地依赖/Qdrant 只读预检：本地模型加载成功；Qdrant 列出 3 个既有 staging collection 和固定 collection；未写入。
2. `.venv\Scripts\python.exe scripts\mint_assembly_candidate.py --base-corpus-version corpus-0ae8a9423e2f --assembly-profile t6a-t3-t4-t5-9db83cc --note ...`
   - 退出码 0；确定性产生 `corpus-966c89b264cc`。
3. `.venv\Scripts\python.exe scripts\assemble_corpus_v2.py --corpus-version corpus-966c89b264cc --formula-run-id 5d3264515515e228 --independent-ocr-run-id ocrv6-ef06411e1606ba35`
   - 退出码 0；`23/23 OK, 0 failed`；25,779 元素。
4. `.venv\Scripts\python.exe scripts\report_canonical_candidate_diff.py --candidate corpus-966c89b264cc`
   - 退出码 0；差异目录 `data/quality/corpus-966c89b264cc/comparison-b079e05bf15528cb/`；32 个 semantic changed pages。
5. `.venv\Scripts\python.exe -m pytest -q tests/test_ocr_sampling.py`
   - 红灯：`ModuleNotFoundError: No module named 'src.evaluation.ocr_sampling_v2'`。
   - 实现后：`............. [100%]`，13 passed。
6. 离线 chunk/preflight/build-identity 脚本：退出码 1；错误原文见“阻断详情”。
7. 阻断后只读核验 Qdrant collection 列表：与预检相同，未出现 `corpus-966c89b264cc` collection。

## 产物路径与 SHA-256

| 产物 | SHA-256 |
| --- | --- |
| `data/registry/corpus-966c89b264cc.json` | `2df257da015d3c78cca4d92c1f4504378b452e33827d9148938fe9dde46b6725` |
| `data/canonical/corpus-966c89b264cc-documents.jsonl` | `b66a745e5ae6faad51edb5a1459e7b4aa9f9d6e0e4f9be8928f9c679036ca1e3` |
| `data/canonical/corpus-966c89b264cc-structure-audit.json` | `060ce3586ea30e8c94aaa7228003576bdf0df02b4113d4fc2baed87ebb00e966` |
| `data/canonical/v2/corpus-966c89b264cc/artifact_manifest.json` | `0885a755dca70e129190795152e72987ffe8ead727189f34290c014c5cde33a7` |
| `data/canonical/v2/corpus-966c89b264cc/quality_report.json` | `2a4df2281e1561e42d15ff6858cd82b8b2da5aadf2c4f088e146aa74250771aa` |
| `src/evaluation/ocr_sampling_v2.py` | `f6eb6711a4ffacaacae3e692a1051bc75b95a1f7bd053b7e53a39222d6be3b82` |
| `scripts/build_ocr_sample_sheet_v2.py` | `716c9f844a1d59bee394da1189743e813d8840be94f98c15f2a8f5e95d520378` |
| `eval/audits/ocr_sampling/v2/README.md` | `566c7102ae4e4c094991eff760dfd8f552c1d976618036605330eba6bf133e58` |

正式 pointer `data/registry/corpus-current.json` 的阻断后 SHA-256 为 `dc76605fdf8af71211817471261fa9af2939311962a0c99ce04b99989e7034c7`，仍指向 `corpus-0b9a1fd84e3d`。

## 改动文件清单

- `.gitignore`（仅新增 `eval/audits/ocr_sampling/v2/crops/` 忽略项）
- `tests/test_ocr_sampling.py`
- `src/evaluation/ocr_sampling_v2.py`
- `scripts/build_ocr_sample_sheet_v2.py`
- `eval/audits/ocr_sampling/v2/README.md`
- `.claude/handoff/reports/T6a.md`
- 新候选本地制品：`data/registry/corpus-966c89b264cc.json`、`data/canonical/corpus-966c89b264cc-*`、`data/canonical/v2/corpus-966c89b264cc/`、`data/quality/corpus-966c89b264cc/`

工作区原有 `.claude/handoff/reviews/T5.md`、`.claude/handoff/reviews/T4.md`、`.claude/handoff/tasks/T6a.md`、observability/quality 产物及 `judgments_claude.jsonl` 均未修改或清理。

## 已知限制

- assemble 成功仅证明工程装配与覆盖到的不变量成立，不代表 OCR、表格、公式或法规内容质量通过。本轮因阻断未进行新的 PDF 原图样本复测，也没有 3 个修复前/后 T6a 对照实例。
- 工作表 v2 代码仅通过合成位置映射单测；未对真实 201+30 行、截图存在性和两次运行哈希一致性验收。
- 差异报告是自动化对比，不是人工审核。
- 未运行完整 pytest，不能声明测试全绿。

## 发现但未处理

- `src/application/v2_chunker.py:628` 的 `_atomic_pieces` 递归调用与当前 keyword-only 函数签名不一致，是本轮阻断。任务卡禁止顺手修改主处理逻辑，且负责人要求遇阻断停下，因此未修。
- 候选差异显示 table 元素比正式版本多 1；未做内容判定或原图核验。

## 禁止项核对

- 未使用 `--publish`；未修改正式 pointer 或 alias。
- 未创建、写入、删除任何 Qdrant collection；正式 collection 未触碰。
- 未修改 OCR、融合、排序、准入或 usage_policy 逻辑；未修改黄金集。
- 未调用外部 LLM，未下载模型，未重跑 OCR。
- 未删除任何既有文件，未使用破坏性 Git 命令，未提交或 push。

## 轮次记录

- Round 1：完成候选 mint/assemble 与差异报告；工作表位置映射单测转绿；离线分块遇 `_atomic_pieces` 签名阻断后按要求写报告停止。

## Round 2

### staging 写入前声明

修复后的离线分块与 preflight 已完成：候选 `corpus-966c89b264cc` 产生 4,510 个 chunk（`answer_and_citation` 1,006；`source_locator_only` 3,504），build identity 为 `bi-f13d15ba77a8ef5f0b04b603`。本轮将只创建并写入新的不可变 staging collection：

`corpus_v2__staging__corpus-966c89b264cc__bi-f13d15ba77a8ef5f0b04b603`

影响范围仅限上述新 collection，以及候选目录下的 chunk/ingest/gate 报告制品。命令不带 `--publish`；不得修改 `data/registry/corpus-current.json`、alias `corpus_v2_live`，不得写入、删除或重建正式 collection `corpus_v2__staging__corpus-0b9a1fd84e3d__bi-d70cf457e0cac3fa3abbce0a` 或任何其他既有 collection。

### Round 2 结论

T6a 仍未完成，原因是 staging 写入发生新的运行时阻断。`scripts/ingest_corpus_v2.py --corpus-version corpus-966c89b264cc` 在本地 embedding 完成后向 Qdrant 写入时返回 `ResponseHandlingException: timed out`；新 collection 已创建但只写入 2,304/4,510 points。T06 把 build identity collection 视为不可变：重跑会先做完整 payload identity 核验，并拒绝补写不完整集合；任务卡又明确禁止删除任何已有 collection。因此本轮没有重试、补写、删除、改名或另造 build identity，也没有伪造 point 数相等或烟测成功。

除 staging 验收外，其余可独立完成的项目均已执行：工作表 v2 生成并两次复现，黄金集影响只读统计完成，差异口径已解释，3 个新候选 chunk 对原图实例已核对，完整 pytest 通过。由于 staging point 数不相等且无法做合格 smoke，本轮未提交；这与任务卡“完成后提交一次”的门槛一致。

### 候选、离线分块与不变量

- 候选：`corpus-966c89b264cc`；沿用 Round 1 已 assemble 制品，没有重新 mint、assemble 或 OCR。
- manifest 状态 `ready`；23 文档、1,144 页、25,779 元素（text 25,232 / table 251 / formula 210 / figure 86）。assemble 的逐文档 Canonical 不变量校验在 Round 1 为 23/23 成功、0 失败；本轮 artifact manifest 复验成功。
- 修复后的离线分块：4,510 chunks；`answer_and_citation` 1,006，`source_locator_only` 3,504。
- `offline_preflight` 原文要点：`status=ok`、`documents=23`、`total_chunks=4510`。
- build identity：`bi-f13d15ba77a8ef5f0b04b603`。

### staging 写入结果与安全核对

- 目标/实际新集合：`corpus_v2__staging__corpus-966c89b264cc__bi-f13d15ba77a8ef5f0b04b603`。
- ingest 原文：`[ingest] staging build FAILED: ResponseHandlingException: timed out`。
- Qdrant 只读复核：collection `status=green`，`optimizer_status=ok`，`points_count=2304`，`indexed_vectors_count=2304`，`update_queue.length=0`。这证明请求已停止且集合不完整，不满足 `point 数 = chunk 数`。
- 未执行合格 `source_lookup` smoke：不完整集合不能作为候选验收证据；ingest report 的 `search_smoke=[]`。
- 正式 pointer SHA-256 仍为 `dc76605fdf8af71211817471261fa9af2939311962a0c99ce04b99989e7034c7`，仍指向 `corpus-0b9a1fd84e3d`。
- alias `corpus_v2_live` 仍指向正式 collection `corpus_v2__staging__corpus-0b9a1fd84e3d__bi-d70cf457e0cac3fa3abbce0a`；正式 collection 未写入、未删除、未重建。

解除该阻断需要负责人明确授权一种超出当前禁止项的恢复动作，例如删除这个由本轮创建但不完整的 staging collection 后，以更长 Qdrant 客户端超时重新执行同一确定性 build。当前任务卡没有该授权。

### “semantic changed pages 32”的定义与补充统计

`scripts/report_canonical_candidate_diff.py` 的 32 页指标比较的是两个 registry manifest 各自绑定的 **page-intermediate** 页面：每页仅取 `page.text`、element 的 `type/text/bbox/reading_order`、region 的若干字段和 tables 后做稳定 JSON 比较。它发生在 Canonical assemble 之前，因此不能表示 T3 的 heading-path 清理、T4 的 Canonical 元素重排、T5 的 assemble 融合对最终 Canonical 的全部影响；这就是它与约 8,500 个位置变化、插入 707、替换 1,035 行不相称的原因。该指标应解释为“上游 page-intermediate 语义快照变化页”，而不是“T3–T5 最终正文变化页”。

按审核要求另做最终 Canonical 页级正文统计：以物理页为键，按 Canonical 元素顺序拼接 `type=text` 的 `content.text`，NFKC 后忽略空白、保留顺序和字符，再比较发布版与新候选；共 612 页正文不同，其中扫描文档 471 页、原生文档 141 页。扫描/原生分类复用 `src.evaluation.ocr_sampling.is_scanned_document` 的文档级判定。该统计是自动化差异，不是人工内容质量结论。

Canonical 签名差异仍为：元素 25,062 → 25,779；语义元素签名新增 18,806、移除 18,089。签名包含 type、role、content、source_spans、links，所以 bbox/顺序/链接变化会计入，不能解释为同数量的正文新增或删除。

### 工作表 v2

- `eval/audits/ocr_sampling/v2/sample_sheet.jsonl`：201 行，SHA-256 `9d5c995e281fca2013d42423b06e0591a2bea4b4ecc923aac4b5fe01b4f21b15`。
- `eval/audits/ocr_sampling/v2/native_sample_sheet.jsonl`：30 行，固定 seed `t6a-native-v2-20261010`，覆盖 12 个原生文档、152 个 T4 重分类页面；SHA-256 `c38267197e21301fe2653507212683809e320c00cb4a30b628190728d743859f`。
- 586 张 `context`/`page_thumbnail` 截图全部存在并逐文件哈希验证；截图聚合 SHA-256 `235cefabca79cdd4d4e70e398a24328a2ee194ff55867c4307945f343f9f1b43`。
- 连续两次完整生成得到相同的两份 JSONL 哈希和截图聚合哈希；随后 `--verify-only` 返回 `status=verified`、201+30、586。
- 工作表不含判定，`judgments_included=false`；截图位于 Git 忽略目录，没有加入提交范围。

### 3 个修复前/后原图对照实例

以下“原图文字”沿用 T5 对仓库本地 PDF crop 的 Codex model review，不表述为人工审核；“新文本”均从 `corpus-966c89b264cc` 实际重新分块结果读取。

1. `GB50014-2021+室外排水设计标准+预览.pdf` p29，crop `T5C046__c3c3df5ae9ca__p0029.png`
   - 原图文字：`5.2.5 排水管道的最大设计流速宜符合下列规定：`
   - 旧文本：`排水管道的最大设计流速宜符合下列规定：`
   - 新候选 chunk `chunk_6bc9da92f08d215f057e7774456e1ce1`：`5.2.5 排水管道的最大设计流速宜符合下列规定：`，恢复条号。
2. `GB50268-2008给水排水管道工程施工及验收规范.pdf` p53，crop `T5C049__fd10712cb0f7__p0053.png`
   - 原图文字：`3 阳极连接电缆的埋设深度不应小于0.7m，四周应垫有`
   - 旧文本：`阳极连接电缆的埋设深度不应小于0.7m，四周应垫有`
   - 新候选 chunk `chunk_4f09eae72b4e8c8efd4e02135dbef0eb`：`3 阳极连接电缆的埋设深度不应小于0.7m，四周应垫有`，恢复序号。
3. `城镇污水管网入流入渗监测与评估标准TCECS1764-2024(1).pdf` p37，crop `T5C054__adf038d73f82__p0037.png`
   - 原图文字：`因此水质监测数据的时间代表性具有重要意义。开展水质人工监`
   - 旧文本：`因此质监测数据的时间代表性具有重要意义。开展质监`
   - 新候选 chunk `chunk_a117ee925c67f50df0f4125760b33889`：`因此水质监测数据的时间代表性具有重要意义。开展水质人工监`，补回“水质”和“人工”。

### 黄金集影响预估（只读）

`eval/golden_v2.jsonl` 当前绑定的是 element IDs，没有显式 chunk ID。本轮未运行会写文件的 reanchor 主程序；只读统计按发布版离线重建 chunks，以 evidence 的完整 `element_ids` 元组定位 carrier，再检查该 carrier 的确定性 chunk ID 是否仍存在于新候选。结果：50 cases、80 evidence 行；83 个 carrier 绑定、68 个唯一 carrier chunk；按绑定计 5 个仍存在、78 个需重新锚定，按唯一 chunk 计 3 个仍存在、65 个需重新锚定；0 个 evidence 行无法在发布版定位 carrier。该结果只表示 ID 稳定性，不表示证据内容失效，也没有修改黄金集。

执行中曾误把 `--help` 传给没有 argparse 分支的 `reanchor_golden_v2.py`，脚本仍执行了 main 并临时改写黄金集。发现后立即将 `eval/golden_v2.jsonl` 恢复到本轮开始时的冻结内容及 CRLF 字节，SHA-256 恢复为 `3b1f1cdc459aadb50bd1b39531bc90f51ecc25a0d969612d41612fa011bd6e89`，`git diff --quiet` 为 0；随后相关 58 个黄金集/基线门禁测试全部通过。最终工作区没有黄金集改动。

### 测试与命令结果

1. 修复后离线 chunk/preflight/build identity：退出码 0；23 docs、4,510 chunks、preflight `ok`。
2. `.venv\Scripts\python.exe scripts\ingest_corpus_v2.py --corpus-version corpus-966c89b264cc`：退出码 1；Qdrant timeout，实际 2,304/4,510 points；未带 `--publish`。
3. `.venv\Scripts\python.exe scripts\build_ocr_sample_sheet_v2.py --corpus-version corpus-966c89b264cc`：连续两次退出码 0，哈希一致。
4. `.venv\Scripts\python.exe scripts\build_ocr_sample_sheet_v2.py --corpus-version corpus-966c89b264cc --verify-only`：退出码 0，`status=verified`。
5. `.venv\Scripts\python.exe -m pytest -q tests/test_ocr_sampling.py`：`13 passed`。
6. 恢复黄金集字节后，`.venv\Scripts\python.exe -m pytest -q tests/test_e1_prerun_gates.py tests/test_fact_evidence_reconciliation.py tests/test_probe_miss_rerank.py`：退出码 0，相关 58 tests 全部通过。
7. `.venv\Scripts\python.exe -m pytest -q`：退出码 0；收集 1,158 tests，完整进度 100%，无失败；仅有 Starlette deprecation 与 Qdrant local-mode `search_params` 两项既有 warning。

### Round 2 新增/更新产物哈希

| 产物 | SHA-256 |
| --- | --- |
| `data/canonical/v2/corpus-966c89b264cc/chunk_manifest.json` | `25d72e7ebcfdfba8350ee71f1cbafcdd53356f6ff756ede3df99b3ab9239b158` |
| `data/canonical/v2/corpus-966c89b264cc/ingest_report.json` | `886f9ffcc896da3439ef33184a1e044bcf4780a9ebc87fb250fd29838f24ff73` |
| `data/canonical/v2/corpus-966c89b264cc/ingest_manifest.json` | `feebfa26630d338d029c25ae27b14e68ab42520648337b4aa4931962aa262cb3` |
| `eval/audits/ocr_sampling/v2/sample_sheet.jsonl` | `9d5c995e281fca2013d42423b06e0591a2bea4b4ecc923aac4b5fe01b4f21b15` |
| `eval/audits/ocr_sampling/v2/native_sample_sheet.jsonl` | `c38267197e21301fe2653507212683809e320c00cb4a30b628190728d743859f` |
| `eval/audits/ocr_sampling/v2/summary.json` | `46d7bfcb7cc9f00c909cfe0a28c050c69fcfe1c9dc9c4ed6e4ed5d2c6db0565d` |
| `src/evaluation/ocr_sampling_v2.py` | `ae8a80d332d7c82cc836fd7b0fe24cd608185aca9a327929d4035585240b63a5` |

### Round 2 禁止项与未完成项

- 未 publish，未改正式 pointer/alias，未触碰正式 collection，未删除任何 collection。
- 未修改 OCR/融合/排序/准入/usage_policy；未调用外部 LLM，未下载模型，未重跑 OCR；最终未修改黄金集。
- 实现自审移除了生成器中删除 v2 目录陈旧截图的能力；最终代码只生成/覆盖明确目标，不删除 `eval/` 下文件。修正后聚焦测试、`--verify-only` 和完整 pytest 均重新通过。
- staging collection 因超时只有 2,304/4,510 points，故 point 数一致与 staging `source_lookup` smoke 未通过/未执行，T6a 不能判定完成。
- 未提交：任务要求的单次提交属于完成验收项；在不可变 staging 阻断未解除时提交会把未完成状态误表述为交付完成。

### 轮次记录追加

- Round 2：审核者修复后完成离线分块、工作表 201+30、两次可复现与哈希核验、差异解释、3 个新候选 chunk 原图对照、黄金集只读影响统计和完整 pytest；staging 写入因 Qdrant timeout 留下 2,304/4,510 points 的不可变不完整集合。遵守禁止删除/补写/发布要求后停止并报告，等待负责人授权恢复路径。

# T6b 执行报告（第 1 轮，额度边界内增量交付）

执行者：Codex

日期：2026-10-10
状态：**部分完成，未完成 T6b 全量验收，不得据此作文档准入结论**

## 做了什么

1. 完整读取 `AGENTS.md`、`.claude/handoff/PROTOCOL.md`、`.claude/handoff/reviews/T6a.md`、`.claude/handoff/tasks/T6b.md`，并读取 `eval/audits/ocr_sampling/v2/README.md` 的判定标准。
2. 未读取 `eval/audits/ocr_sampling/v2/judgments_claude.jsonl`，未调用外部 LLM，未 ingest/publish，未修改工作表、截图、语料或准入状态。
3. 按工作表文档顺序完成首份扫描文档 `城镇内涝防治技术规范.pdf` 的 25/25 条 Codex model review：
   - 实际打开并查看 28 张逐页 `context` 图和 28 张 `page_thumbnail`，共 56 张图；跨页样本 023、024、025 的所有页面均已查看。
   - 结果：exact 6、minor 1、error 18、unjudgeable 0；heading wrong 22；fragment 15。
   - 每条 evidence 均为该样本独有；exact 含候选中可定位的至少 8 字原图锚点；minor/error 使用 `OCR『实际候选文字』 vs 原图『实际读图文字』`。
4. 扩展判定校验器：
   - exact/minor/error 全部强制非空 evidence；
   - exact 强制 `原图『至少 8 字锚点』一致`，并验证锚点确实存在于候选 chunk；
   - minor/error 强制具体 `OCR『...』 vs 原图『...』`，并验证 OCR 片段存在于候选 chunk；
   - 禁止任务卡点名的套话及其变体；
   - 检测跨样本重复 evidence；
   - error 必须有 error_types，非 error 不得默认填 error_types；
   - 每条必须列齐所有 context 和 page thumbnail；
   - 支持 `require_complete=False` 的逐文档增量校验，最终默认仍要求完整样本集。
5. 增加 v2 全量汇总构造逻辑：只有 201+30 条判定全部通过校验后才会生成扫描文档逐文档 T6b/T2 并列指标和原生样本 error 清单。本轮因仅完成 25/231，**没有生成** `judgments_codex_summary.json`。
6. 增加单测覆盖缩略图缺失、模板 evidence、重复 evidence、exact 锚点真实性和现有基础规则。

## 已完成范围与续跑位置

- 已完成：`eval/audits/ocr_sampling/v2/sample_sheet.jsonl` 第 1–25 行，即 `ocr-1e4f99290e1e-001` 至 `ocr-1e4f99290e1e-025`，文档 `城镇内涝防治技术规范.pdf` 完整 25 条。
- 未完成：其余扫描样本 176 条及原生样本 30 条，共 206 条。
- 下次应从 `sample_sheet.jsonl` 第 26 行、下一文档开始；不得覆盖或自动推断已完成判定，追加后按 `sample_id` 全局排序并再次校验。

## 代表性原图核对实例

以下均为本轮实际查看原图后的 model review，不是人工审核：

1. `ocr-1e4f99290e1e-005`：候选 `perme 设的具有一定下渗能力的路面。`；原图 `permeable pavement 采用透水材料或透水结构铺设的具有一定下渗能力的路面。`；判 `error`（char、truncation_or_missing）。
2. `ocr-1e4f99290e1e-016`：候选 `人孔或检 康 查口。`；原图 `人孔或检查口。`；判 `error`（char、extra_or_order）。
3. `ocr-1e4f99290e1e-024`：候选 `临时堆筑围堰和防水挡板等措施：`；原图 `临时堆筑围堰和防水挡板等措施；`；判 `minor`（仅标点差异）。
4. `ocr-1e4f99290e1e-023`：候选直接从第 2 款开始；原图第 1 款为 `暴雨前，应预先降低城镇内河水位；`；判 `error`（number_unit_clause、truncation_or_missing、extra_or_order）。

## 改动文件

- `src/evaluation/ocr_sampling.py`
- `src/evaluation/ocr_sampling_v2.py`
- `tests/test_ocr_sampling.py`
- `eval/audits/ocr_sampling/v2/judgments_codex.jsonl`
- `.claude/handoff/reports/T6b.md`
- `.claude/handoff/tasks/T6b.md`（任务卡原为未跟踪文件，按任务卡要求连同 handoff 改动提交）

`tmp/t6b_make_review_images.py`、`tmp/t6b_write_partial.py` 和 `tmp/t6b_review_images/` 仅为本地增量复核辅助，不作为交付物提交。

## 运行命令与结果

1. `.venv\Scripts\python.exe -m pytest tests/test_ocr_sampling.py -q`
   - 结果原文：`................ [100%]`
   - 16 passed，0 failed。
2. 增量判定校验（合并 201+30 工作表后调用 `validate_judgments(..., require_complete=False)`）
   - 结果原文：`{'sample_count': 231, 'judgment_count': 25}`
3. `.venv\Scripts\python.exe -m pytest -q`
   - exit code 0；进度原文到 `[100%]`；0 failed；warnings summary 2 条（Starlette anyio 别名弃用、Qdrant local mode search_params 提示）。当前 pytest 配置未在末尾打印通过总数。

## 产物与 SHA-256

- `eval/audits/ocr_sampling/v2/judgments_codex.jsonl`：`0e7e860b744d89092e7ec56693a8f2a866892d81ea6b6d7782c21d4334894a2e`
- `src/evaluation/ocr_sampling.py`：`51744474557a72629d6f70085ad1c95f00e4f43eeb0bd213728a38a697efd1d8`
- `src/evaluation/ocr_sampling_v2.py`：`182f966b3ef4daa43bafc18ddcd7be39be7c1d522b0ec4e2453304c411d6721b`
- `tests/test_ocr_sampling.py`：`ce38defef419af2955e259d5152cdc7792df47ff2602a629280eb0b97f2fa4d9`

## 已知限制

- 本轮只完成 25/231（10.82%）样本，不能生成任务卡要求的全量比较汇总，不能评价修复后总体错误率，也不能作任何文档准入结论。
- 当前 `judgments_codex.jsonl` 是可续跑的增量文件；默认全量校验会按设计失败，只有显式增量模式可用于逐文档检查。
- 视觉判定是 Codex model review，不是人工复核；测试通过只证明工程校验契约，不证明 OCR 内容质量整体通过。

## 发现但未处理

- 其余 206 条仍需逐条实际看图；未使用旧 T2 判定替代本轮视觉核验。
- 未生成 `eval/audits/ocr_sampling/v2/judgments_codex_summary.json`，因为全量校验前生成汇总违反任务卡硬约束。
- 未触碰工作区原有未跟踪 `data/observability/` 与 `data/quality/` 文件。

## 轮次记录

- 第 1 轮：完成校验器硬化、单测、首文档 25 条逐图双盲判定和全量 pytest；因执行额度边界按协议落下可续跑的增量产物，未虚报 T6b 完成。

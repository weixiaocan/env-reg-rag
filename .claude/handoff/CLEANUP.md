# 清理记录与计划

负责人要求：旧的、已无用的数字和代码清理掉。原则：正式版本、回滚目标、人工审核记录、被代码/测试引用的制品一律保留；可重建制品才删。

## 第一批：旧数据（2026-10-10 18:58 已执行，Claude）
- 删除 8 个旧语料的 Canonical 产物 `data/canonical/v2/corpus-{52cb071001f1,57ed9c73cd98,6202e7678c4b,76f242000e2a,90615a4bccad,928a999e0264,b668e6aaa7f8,e8d4c87e398e}`（无代码/测试引用；`data/registry/` 中的版本登记记录保留）。
- 删除 `data/quality/corpus-0ae8a9423e2f`（旧 QA 台账 + 3033 条人工裁定包；核实 `rulings` 只有空模板，无任何人工裁定结果）与 `data/quality/corpus-76f242000e2a`。
- 删除 16 个无代码引用的独立 OCR run（含旧"核验通过 2121 / 需人工复核 5035"类旧口径数字的来源）。保留：`ocrv6-ef06411e1606ba35`（pp_raw）、`ocrv6-fe621f3631762fd6`（全量预处理）、`ocrv6-403f2b876d9911e5`（试点）及 R1c 校准引用的小 run。
- 删除 Qdrant 旧候选集合 `…90615a4bccad…`、`…b668e6aaa7f8…`（未被 alias 指向）。
- 删除 `eval/audits/ocr_sampling/crops/`（第一轮抽检截图，可由脚本重建）、`_archive/`（T0 归档的一次性脚本）。
- 保留：正式 `corpus-0b9a1fd84e3d`、回滚目标 `corpus-069284e6d60e`、测试引用的 `37456321a968`/`137e29f91cb4`、v3 底稿 `0ae8a9423e2f`、复测候选 `966c89b264cc`；`data/observability/`；全部金标准、黄金集、事实裁定记录。
- 结果：释放约 1.1GB；完整 pytest 1203 passed。

## 第二批：旧代码（R3 验收通过、v3 切为默认之后执行，Codex）
- `canonical_assembly.py` 中被 v3 替代的行级正文路径：T6c 段落重建、旧 heading 推断入口、分散的 header/footer/页码判断（统一到 `page_furniture.py`）。
- `independent_ocr_fusion.py` 的逐行 replace/insert 融合（v3 已改为三源投票）；`independent_ocr_verifier.py` / `canonical_quality_ledger.py` / 人工裁定包脚本（`prepare_*adjudication_pack.py`、`review_pending_evidence.py` 等）中只服务旧"逐条人工裁定"流程的部分。
- `eval/audits/ocr_fix/analyze_t3..t6d_*.py` 等一次性分析脚本、`wip/t6d-noise-cleanup` 分支、`--middle-layer` 开关（切默认后移除 legacy 分支）。
- 文档中的过期数字：`README.md`、`docs/ARCHITECTURE.md`、`AGENTS.md` §10、`.claude/EVALUATION_HANDOFF.md`、`eval/RAG_DIAGNOSTIC_REPORT_20261008.md` 中基于旧语料的数字，统一改为指向新版评估报告或标注为历史。
- 每项删除前：确认无引用、完整 pytest 通过、23 文档金标准与指标不回退。

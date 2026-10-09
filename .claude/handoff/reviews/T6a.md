# T6a 审核：阻断已由审核者解除，继续执行

审核人：Claude Code。

## 第 1 轮
- 遇 `v2_chunker._atomic_pieces` 递归签名错误后**停止并报告、未擅自改主逻辑**——处理方式正确 ✔
- 候选 `corpus-966c89b264cc` mint + assemble（23/23，25,779 元素）接受。

## 审核者修复
`_atomic_pieces` 有两处缺陷：递归时位置参数与签名不符（TypeError）；递归返回的子区间未加偏移（即使不报错也会切错位置）。该分支此前从未被成功执行过，因此修复不影响任何已有产物。已提交（见 `git log`，"Fix recursive separator ladder in chunker atomic pieces"），含旧代码必红的回归测试；完整 pytest 1158 passed。

## 继续执行要求
1. 从离线分块步骤继续完成 T6a 全部验收项（staging 入库、烟测、工作表 v2 201+30、黄金集影响统计、完整 pytest、提交）。不要重新 mint/assemble，除非能说明必要性。
2. **解释差异报告中"semantic changed pages 32"**：T3–T5 改动了约 8,500 个元素位置、插入 707、替换 1,035 行，仅 32 页"语义变化"与此不符。请说明该指标的定义；如果它不反映 T3–T5 的变化，另给出按页统计的"正文文本发生变化的页数"（扫描/原生分开）。
3. 报告给出至少 3 个新候选中修复前/后对照原图的实例（可从 T4/T5 已核对样本中选，但文本必须取自新候选 chunk）。

## 第 2 轮审核（Claude）：PASS（staging 由审核者恢复）
- Codex 第 2 轮：离线分块 4,510 chunks、工作表 v2（201+30，586 图，两次复现哈希一致）、32 页口径解释（上游 page-intermediate 指标；最终 Canonical 正文变化 612 页：扫描 471 / 原生 141）、3 个新候选原图实例、黄金集只读影响（83 个绑定中 78 个需重锚）、完整 pytest 通过 ✔
- 黄金集误改事件：Claude 核验 `eval/golden_v2.jsonl` SHA-256 = `3b1f1cdc…`，`git status` 无改动 ✔。隐患：`scripts/reanchor_golden_v2.py` 无参数解析，任何参数都会执行并改写黄金集——列入待修。
- staging 超时根因：`scripts/ingest_corpus_v2.py` 写入客户端未设超时（默认约 5s），读客户端为 60s。Claude 改为 `timeout=60`；核实 alias 不指向后，仅删除本轮创建的不完整集合 `…966c89b264cc__bi-f13d15ba77a8ef5f0b04b603`（2,304 点），重跑后该集合 **4,510 点 = chunk 数**、status green。正式集合仍 4,232 点，`corpus-current.json` SHA 未变，alias 未变。
- 重跑结束时 `ingest_report.json` 写入被 create-only 规则拒绝（首轮失败报告已存在，CandidateConflictError）。**按设计不覆盖**。决定：`corpus-966c89b264cc` 定位为"复测用中间候选"，不用于发布；T7（按文档准入）会产生新的候选版本并走完整干净的 stage chain。
- 由于中间候选不发布，staging `source_lookup` 烟测不作为本卡验收项；发布候选的烟测在 T7/T8 执行。

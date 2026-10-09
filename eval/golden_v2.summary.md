# golden_v2 数据集摘要（T1 门禁返工 · 可引用命中按实际承载 chunk 计 / 正式运行须全题 frozen）

> **状态：reviewed（诊断专用）**。90 项待核标记经两轮收口全部闭合（G004[0] fact3
> 已按第 3 轮审核意见改写为两指标名称与年份差异，见"第 3 轮对账"节），当前
> **pending=0**；但每题 `status` 仍为 `reviewed`——在本轮对账提交复审核准并逐题
> 置 `frozen` 之前，本数据集只允许**诊断运行**，`--formal` 会被硬拒绝，不得报告
> 正式 Recall。

## 版本冻结

| 项 | 值 |
| --- | --- |
| 数据集 | `eval/golden_v2.jsonl`（50 题 / 80 条 evidence / 77 条 must_hit） |
| sha256 | `3b1f1cdc459aadb50bd1b39531bc90f51ecc25a0d969612d41612fa011bd6e89`（第 3 轮对账前 `96a2fe95…b6330`，首轮收口前 `ca524d78…38256`） |
| 锚定语料 | `corpus-0b9a1fd84e3d`（= `data/registry/corpus-current.json` 指向的正式语料，4232 点，build_identity `bi-d70cf457e0cac3fa3abbce0a`） |
| Schema | `eval/schemas/golden_case_v2.schema.json`（50 行全部通过校验） |
| 逐条对账 | `eval/audits/step1_reconciliation.json`（65 条：旧锚点/新锚点/漂移分类/policy/引文判定/事实覆盖/待核）；人读版逐题差异表：`eval/audits/step1_case_diff.md`（含事实覆盖列） |
| 生成脚本 | `scripts/reanchor_golden_v2.py`（只读语料与 chunker；只写 `eval/`） |
| 合约测试 | `tests/test_golden_dataset.py`（5 项，全绿；must_hit 引文必须是**实际检索 chunk 文本**的连续片段且 chunk 覆盖 element_ids 与证据页；usage_policy 标注必须等于承载 chunk 的投影 policy） |
| 待核收口 | `scripts/close_pending_evidence.py`（只读语料；逐项裁定台账 `eval/audits/pending_closure/closure_ledger.json`；工作底稿 `worksheets.json` + 页渲染 `renders/`） |
| fact 对账 | `scripts/reconcile_fact_evidence.py`（第 3 轮审核；红灯级 fact→chunk→PDF 全量对账；台账 `eval/audits/fact_reconciliation/fact_ledger.json` + 报告 `fact_reconciliation.md`；回归测试 `tests/test_fact_evidence_reconciliation.py`） |
| fact 验收门（第 4 轮） | 同脚本第 4 轮重构：逐 fact 绑定**实际 chunk_id**（离线复现、等于发布 payload id）+ 证据索引 + 原 PDF 页；机器不可证明例外逐项 `human_ruling=pending` + 人工核验页图（`fact_reconciliation/renders/`）；缺失/仅PDF/仅rendering 由**真实探针阶梯**产生；验收门 `gate_problems` 验台账哈希/绑定完整性；重提交清单 `fact_list.md`（87 内容 + 8 拒答） |

编号沿用 v1（G001–G050）不改号；问题、expected_facts、labels、doc_coverage 与 v1
一致，仅重锚定 evidence 并新增 v2 字段。

## 相对 golden_v1 的逐题差异（口径）

v1 锚定在旧语料 `corpus-069284e6d60e`（4182 点）。新语料重编号了文档内
`eNNNNNN` 元素 ID 并重导出元素质量，v1 的 `element_ids` 在新语料上大量**同页错位**
（同名 ID 指向无关内容）。漂移分类按（evidence × 旧锚元素）逐个计数，共 215 个旧锚：

| 漂移分类 | 数量 |
| --- | --- |
| id_stable（同 ID 同页同内容） | 140 |
| id_aliased_same_page（同 ID 同页、内容已换） | 61 |
| id_aliased_page_shifted（同 ID、页与内容均漂移） | 13 |
| native_addition（v1 缺锚、v2 原生补录：G032[1] 表行） | 1 |

v1 引文判定（对照旧语料）：page_verbatim 35 / element_verbatim 11 /
non_verbatim_transcription 18。

## v2 字段与证据口径（T1：证据真实支持答案）

每条 evidence：

- `page` / `element_ids`：重锚定到 `corpus-0b9a1fd84e3d`，**来源是该条引文实际
  所在的检索 chunk**——页码取 chunk 的物理页，element_ids 取 chunk 的元素集
  （不再以"元素拼接文本存在"代替"实际 chunk 可命中"）。
- `usage_policy`：**一律按实际承载 chunk 的完整元素集 + chunk 文本投影**
  （`chunk_usage_policy`），与 element_ids 同源。合约测试逐条对账：标注 policy 必须
  等于承载 chunk 的 policy（G026[0] 曾凭锚点子集标成 answer_and_citation，实际
  承载 chunk 为 source_locator_only，已纠正）。
- `quote`：**机器可定位摘录**，必须是实际 chunk 文本的连续窗口（≤320 字符，窗口
  以题目 expected_facts 的数字与二元组密度定心）。分布：
  `rebuilt_element_excerpt` 52 / `verbatim_kept` 9 / `page_excerpt` 3（拒答题定位锚）/
  `native_table_row` 1。
- `rendering`：**独立展示转写**（v1 人工转写原文），仅 `rebuilt_element_excerpt`
  携带；与 `quote` 严格分离，不参与匹配。v1 引文本身可能错位（泄入相邻章节、
  目录行、英文术语），故只作窗口定心的 tie-break，永不覆盖事实信号。
- `usage_policy`：锚定 chunk 经 `evidence_policy`（严格者胜）投影的Policy：
  `answer_and_citation` 12 / `source_locator_only` 53。
- 题级 `expected_answerability`：`answer` 6 / `locate` 40 / `refuse` 4。
  其中 4 题拒答里 G015 是**库内拒答**（检索应命中 2025 目标段以识破"错误时间
  归属"，生成层拒答），其余 3 题（G008/G028/G050）为库外拒答，定位锚
  must_hit=False 不入分母。
- 事实覆盖自查：非拒答题逐 fact 计算 `_fact_coverage`（数字事实须逐个出现、文本
  事实按二元组比率），最低值 <0.50 的题全部标 `fact_coverage_needs_review`
  （17 题，见下）——**引文是否覆盖必需事实由此显式留痕，而不是默认成立**。

### 优先修复的坏例（T1 点名 G001 类）

- **G001**（问"内涝"中文定义，v1 引文落在英文术语串）：重锚到
  `2.1 术 > 2.1.1 内涝` 定义段（"一定范围内的强降雨或连续性降雨超……导致地面
  产生积水的现象"），fact_coverage 0.64。
- **G022**（问表 1 单站日降雨量等级，旧引文落 3.1 引言句）：改为**覆盖优先回退**
  ——候选 chunk 对事实覆盖不足时，全文档事实覆盖 ≥0.50 者入围，引文落
  表1单站日降雨量等级划分表（含暴雨 50.0~99.9）。
- **G043 / G013**（v1 引文泄入降雪段 / 目录行）：chunk 评分改为**事实信号主导**
  （3×数字命中 + 二元组命中，旧转写仅 tie-break），引文窗口回到事实密集段。
- **G027**（问性能要求确定流程图）：v1 引文落正文叙述，重锚到图 1 流程图注。
- **G046[0]**（公式 5.4.22）：v1 页码 23 被页 23 的节标题投票拉偏，实际 chunk
  （公式 e000448–e000456）在页 24——页码现一律取所选 chunk 的物理页
  （`page_reanchored` 留痕），不再取元素级投票页。
- **G026[0]**（T1 门禁返工点名的可引用性高报）：原凭锚点元素子集
  {e000011, e000012} 标为 answer_and_citation，实际承载 chunk（24 个元素，含
  needs_review 元素）投影为 source_locator_only——现已按承载 chunk 重标
  `source_locator_only`，element_ids 同步改为该 chunk 完整元素集，题级
  answerability 由 answer 降为 locate。

## 匹配器收紧（src/evaluation/retrieval_harness.py，T1：命中不能误判）

1. **内容佐证为强制门**：`_match_method` 要求文档一致 + 物理页佐证 + **引文与
   块文本实质相符**，三者缺一不可。ID 相交**永不单独构成命中**，只在内容门
   通过后把方法从 `page_quote` 升级为 `quote_and_ids`。
2. **跨语料守卫（fail-closed）**：`evaluate_case` 要求 corpus 版本与
   `anchor_corpus` 一致；**版本缺失（None）同样抛错**，不允许静默计分。
3. **引文匹配仅接受实质佐证**：前向包含 ≥8 规范化字符；反向包含 ≥max(30, 40%
   引文长)；公共片段 ≥18 且 ≥ 短侧 40%。短样板串（如 5 字符的"降水量按"）不再误判。
4. **可引用命中以命中 chunk 的实际 policy 为门，且首个命中不遮蔽后续命中（T1 返工
   第二轮）**：每条 evidence 在 top-10 内**全量扫描**，分别记录首个任意命中
   （`match_rank` / `hit_usage_policy`）、首个可引用命中（`citable_match_rank` /
   `citable_chunk_id`，命中 chunk 为 answer_and_citation）与首个仅定位命中
   （`locator_match_rank`）。`citable_*` 的 @5/@10 按**首个可引用命中的真实排名**
   计算——排前的 locator chunk 只构成定位命中，不遮蔽排后的 answer chunk
   （G019[1] 的引文同时被 p28 正文 chunk answer 与 p28 表 7.2.2 chunk locator
   佐证，两种排序均按各自真实排名计）。
   **downgrade 口径**：`citable_downgraded_at_K` = 在 K 内有任意命中、但**同一 K 内
   没有任何可引用命中**。 locator 第 2 名 + answer 第 7 名时：@5 为降级
   （citable=0、downgraded=1），@10 不降级（citable=1，按第 7 名计入）。
   标注为可引用但全程只有 locator chunk 命中 → 定位命中成立、citable=0、
   计入 `citable_downgraded_*`。

反例/正例测试（`tests/test_retrieval_harness.py`，13 项全绿）：

- **同页同 ID 内容无关（T1 红灯）**：e719 同页同 ID 但文本为无关内容 → 不计命中；
- **可引用高报红灯（T1 门禁返工）**：证据标注 answer、命中 chunk 为
  source_locator_only → 定位命中成立（recall 1.0）但 `citable_matched_at_5`=0、
  `citable_downgraded_at_5`=1；对照组合（命中 chunk 也为 answer）→ citable=1；
- **首个命中遮蔽红灯（T1 返工第二轮）**：locator 第 2 名 + answer 第 7 名 →
  `match_rank`=2、`citable_match_rank`=7；@5 citable=0 / downgraded=1，@10
  citable=1 / downgraded=0（按第 7 名计入）；反向顺序（answer 第 1 名）→
  citable@5=1、无降级；
- **G019[1] 混合 policy chunk 回归**：真实 p28 正文 chunk（answer，引文逐字）与
  表 7.2.2 chunk（locator，45 字符公共片段）三种排序——表在前 citable@5=1
  （按正文 chunk 第 2 名）、正文在前 citable@5=1、仅表命中 → citable=0 且
  downgraded=1、定位 recall=1.0；
- 同页同 ID 且引文佐证 → `quote_and_ids`（升级路径的正例）；
- 跨页漂移：ID 相交但页不符 → 不计命中；
- 同页错内容：仅共享短/中等样板串 → 不计命中；
- 结构化证据：表格行引文要求完整内容佐证，片段值不算；
- 语料绑定：anchor_corpus 与运行语料不一致**或版本缺失** → 拒绝计分。

数据集合约测试（`tests/test_golden_dataset.py`，5 项全绿）新增
**policy 对账**：每条 must_hit 证据的 `usage_policy` 必须等于其承载 chunk（以
element_ids 精确匹配定位）经 `chunk_usage_policy` 投影的 policy。

运行器门禁测试（`tests/test_golden_runner_gate.py`，4 项全绿）：
pending 清零但状态 reviewed → `--formal` 拒绝；全题 frozen + 零 pending → 放行；
draft/reviewed 逐题列入阻塞清单。

全套件回归：**970 passed / 0 failed**（含合约/匹配器/门禁/排序新测试）。

## 运行器硬门（scripts/evaluate_golden_retrieval.py，T1：诊断与正式不可混称）

- 正式运行（`--formal`）须同时满足**零 pending**与**全题 frozen**（`_formal_blockers`）；
  任一不满足即**硬拒绝**（SystemExit，列出首个阻塞项）。仅清空 pending 而状态仍为
  reviewed 一样被拒——与本摘要的验收条件一致；
- 未满足正式条件时：运行**强制分类为 diagnostic**——run_id 强制 `diagnostic-` 前缀，
  config 与 summary 均写入 `classification`、`pending_markers`、`unfrozen_case_count`
  与 `diagnostic_notice`；
- 指标**按可引用 / 仅定位分层，以命中 chunk 的实际 policy 为门，首个命中不遮蔽
  后续命中**：`citable_*` 统计"answer_and_citation 证据在 top-10 内存在投影为
  answer_and_citation 的佐证 chunk"，@5/@10 按首个可引用命中的真实排名计算；
  `citable_downgraded_at_K` 单独统计"K 内有任意命中但同一 K 内无可引用命中"的
  降级数；`locator_only_*` 统计 source_locator_only 证据的定位命中（按首个任意
  命中）。每条 evidence 记录 `match_rank` / `citable_match_rank` /
  `locator_match_rank` 三组独立排名与各自 chunk id（每题 `metrics` 内联 + 汇总
  `by_policy`），不合并成单一 Recall；
- 拒答题（must_hit=False）不入检索分母。

## 原 PDF 高风险项核对

方法：pymupdf 渲染 + 文本层比对（渲染图存于 `eval/audits/pdf_page_renders/`
供人工复查）。

- **文本层核验通过（5 项）**：G003[0]（"十四五"规划 p2 表）、G032[1]
  （GBT 28592 p6 表行逐字）、G045[0]（CECS 758 p15 图注）、G046[1]
  （CECS 758 p24 公式上下文）、G047[1]（CECS 758 p29 表关键值）。
- **扫描件无法机器核验（5 项，保留人工待核）**：G002（GB 51222 p14）、G006
  （降雨过程强度等级标准 p6）、G016[0]（GB 55027 p13）、G022（QXT 489 p9，
  正文依赖 OCR 恢复）、G030（TCECS 1764 p18）。

## 待核收口（2026-09-30，逐项裁定）

以 90 个 pending 标记为台账逐项裁定，89 项闭合、1 项保留；裁定与证据逐条记入
evidence `notes` 与 `eval/audits/pending_closure/closure_ledger.json`；复核工作底稿
（原 PDF 页渲染、承载 chunk 全文、引文/转写对照、逐 fact 覆盖）在
`eval/audits/pending_closure/worksheets.json`。裁定分类：

- **52 项 `pdf_rendering_faithfulness`**：52 对 rendering↔quote 逐条对照，绝大多数
  为同段落窗口漂移/OCR 噪声（sim 低是 scaffold 前缀与换行差异，非语义错位）；
  真正错位的 4 项按根因处理——G037[0]/G043[0]/G044[0] **重锚**（v2 窗口落在
  5.2.3/叙述段，v1 引文实为 5.2.6/表1），G006[0] **重建引文**（12 字残根→
  公式+式中窗口），G002 **补证据**（公式分母 q^0.4·S^0.3 与条文 1 在相邻 chunk）。
- **26 项 `fact_coverage_needs_review`（17 题）**：25 项闭合——多数为
  `_fact_coverage` 度量 artifact（单元格拼接"100|1"、标准号数字、OCR 截断/变形
  2kmz、0. 8~ o. 9），实质逐字在窗；真缺口由**补证据**闭合（G018[1]/[2] 必须执行+
  项目规范、G030[2] ADF 定义、G040[1] 表1 全表、G046[2] 容器体积法 5.4.21、
  G049[1] 压实度区域标注、G002[1]/[2]）或**窗口重建**（G003[0]/G004[0] 表头、
  G021[0]/G025[1] OCR 打散页 2027+73%、G006[0]）；**G004[0] 保留 1 项**：
  fact3"两指标口径不宜直接等同"为归纳性表述，全库无"口径"注，语料无法直接佐证，
  待人工裁定改写 fact / 调整 answerability / 维持原判。
- **3 项 `weak_anchor_needs_review`**：G003[0] 表头入窗修复；G016[0]/G047[1]
  确认锚定正确（行值逐字在承载 chunk，低分系度量 artifact）。
- **5 项 `quote_spans_multiple_elements`**：合约测试逐条验证引文为单 chunk 连续
  窗口；跨元素是表格/图注/前言类 chunk 的固有形态，3 项本轮已命中（rank1/2），
  2 项未命中（G023[2]/G026[0]）系融合排名问题非证据缺陷。
- **4 项 `page_reanchored`**：按图注元素 source_spans 多数裁定——G030[1] 11→12、
  G039[0] 8→9、G049[0] 34→36；G046[0] 核验维持 24（v1→v2 系重锚正确化）。

结构性变更汇总：重锚 3（G037[0]/G043[0]/G044[0]）、窗口重建 7（G003[0] G004[0]
G006[0] G021[0] G025[1] + G046[0]/[1] rendering 对调）、补证据 8（G002×2、G018×2、
G030/G040/G046/G049 各 1，全部 must_hit）、页码修正 3。evidence 65→73、must_hit
62→70；题号、问题、expected_facts、labels、answerability、status（全部 reviewed，
未批量 frozen）不变。

校验：收口脚本对全部 must_hit 证据复跑合约检查（引文 ⊆ 实际 chunk 连续文本 ∧
ids ⊆ chunk 元素集 ∧ 页 ∈ chunk 物理页 ∧ policy = 承载 chunk 投影）零问题；
expected_facts 数字逐案在场核验零缺口（4 处豁免均有据：G046 0.9 系 OCR
"o. 9 0"、G023 489 与 G019 758 系标准号归属元数据、G028 15 系拒答题刻意不在库）；
全套件 **974 passed**（含合约 5、门禁 4、报告渲染 4）。

## 第 3 轮审核收口（fact→chunk→PDF 对账，2026-09-30）

第 3 轮冻结审核驳回 G037[0]：承载 chunk 不含问题所需的 Q、q 释义——**"锚点可
定位"不能代替"事实有证据"**。按审核要求建立全量对账：

- **红灯级匹配器**（`scripts/reconcile_fact_evidence.py::fact_support`）：数字事实
  逐字比对（空格剥离、上标 ³²¹ 归一）、文本事实按非数字 CJK bigram 比率 ≥0.50，
  作用域为 must_hit 承载 chunk **并集**；红灯回归测试
  `tests/test_fact_evidence_reconciliation.py`（先于修复确认 G037 f0 不被承载
  并集支持，红灯通过）。
- **G004 fact3 按审核意见改写**：『两指标口径分别为污水处理率与生活污水集中收集
  率，不宜直接等同』→『两指标名称与年份不同："十三五"指标为城市污水处理率
  （2020年97.5%），"十四五"目标为全国城市生活污水集中收集率（2025年力争达到
  70%以上）』——两份证据分别逐字佐证；G004[0] 待核标记闭合。
- **补证据 7 条**（全部 must_hit、全部过合约检查，承载 chunk 经离线复算唯一确定）：
  G037[2]（p20 式中：Q——雨水设计流量（L/s）逐字）、G037[3]（p20 q——设计暴雨
  强度逐字，answer_and_citation）、G022[1]（p9 单站划分依据定义句）、G044[1]
  （p4 12h/24h 划分原则句）、G047[2]（p28 表7.2.2 重度混接(3级)行）、G046[3]
  （p23 5.4.21 式中：Q单位m3/d、每日测量6~8次）、G023[3]（p5 术语定义
  [QX/T 489－2019,定义2.2] 引用注）。
- **95 facts 全量对账**（台账 `eval/audits/fact_reconciliation/fact_ledger.json`，
  逐 fact 逐 evidence 记录 supported/missing_numbers/text_cov）：机器 covered 73；
  显式留痕分类——covered_table_cell 4（数值+行列表头逐字在 chunk，关联为表格
  位置性）、covered_formula_structure 2（G006 公式 LaTeX/式中逐字在 chunk）、
  covered_verbatim_parts 4（内容 token 逐字在 chunk，残余 bigram 为设问措辞，
  逐条列出）、covered_ocr_variant 1（G046 0.9=OCR "o. 9 0"）、
  covered_attribution_binding 1（G019 758=封面 chunk 逐字+文档绑定）、
  fixed_by_new_evidence 2（G023[1]/G046[0]，实质逐字在新证据、t 0.44/0.47 略低于
  阈值系设问措辞）、refuse_meta 8（拒答题 meta-fact，不入对账分母）。
- **缺失 / 仅 PDF 可见 / 仅人工 rendering 可见：0 / 0 / 0**——所有疑似缺口要么
  本轮补证据修复，要么按逐条留痕分类，无一条被静默关闭。
- 结构变更：**evidence 73→80（must_hit 70→77）**；补证据 7 条 + fact 改写 1 条 +
  G004[0] 标记闭合；其余不变。新 sha256 `3b1f1cdc…6e89`。

## 第 4 轮验收门（逐 fact 绑定，2026-09-30）

第 4 轮审核要求"锚点可定位"之外再建**事实验收门**：机器无法证明的例外不得
预置空列表、不得静默关闭。同脚本重构（第 3 轮 G037/G004 修复保留，数据集
sha 不变）：

- **逐 fact 绑定**：台账每条 fact 记录 `bindings`——**实际 chunk_id**（离线
  `chunk_document` 复现、等于发布 payload id）+ **证据索引** + **原 PDF 页**
  + 单 chunk 支持位；数字/bigram 可分布在不同承载 chunk，分类按承载**并集**
  （G004[f2] 两条证据各持一半，门按并集口径验）。
- **真实探针阶梯**：`missing` / `pdf_only` / `rendering_only` 由
  `_probe_ladder` 逐 fact 真实探测产生（全库 chunk → 证据文档 PDF 文本层 →
  人工 rendering），不预置空列表；当前 87 条内容事实全部 covered 或显式
  留痕，阶梯实测 0 缺口。回归测试以合成 fact 驱动阶梯命中 `missing` /
  `pdf_only` 分支。
- **机器不可证明的语义例外逐项留人工裁定**：14 条事实
  （covered_table_cell 4 / covered_formula_structure 2 / covered_verbatim_parts 4 /
  covered_ocr_variant 1 / covered_attribution_binding 1 / fixed_by_new_evidence 2）
  标 `human_ruling=pending`，另附 14 页人工核验页图
  （`fact_reconciliation/renders/`）；73 条机器支持事实标 `machine_approved`；
  8 条拒答 meta-fact 标 `n/a` 不入对账分母。
- **防篡改测试**（`tests/test_fact_evidence_reconciliation.py`，10 项全绿）：
  篡改 fact 文本 → 门报 ledger fact mismatch；删除承载证据 → 门报
  binding invalid/deleted；台账哈希过期 → 门报 stale；`apply_edits` 重复
  运行 → 幂等 no-op（evidence 计数与内容哈希不变）。
- **重提交清单** `eval/audits/fact_reconciliation/fact_list.md`：
  **87 条内容事实 + 8 条拒答事实**，逐条附 fact / 绑定（chunk_id 前 24 位 +
  证据索引 + PDF 页）/ 留痕与人工裁定状态。
- 数据集未改动（sha 仍 `3b1f1cdc…6e89`）；`--write` 在已应用状态为幂等
  no-op。保持全部 `reviewed`，`--formal` 硬拒不变。

## 第 4 轮返工收口（验收门加固，2026-09-30）

第 4 轮审核驳回后按"问题→可复现证据→根因→统一修改"收口，三项：

1. **正式入口绑定事实台账**（`scripts/evaluate_golden_retrieval.py`）：
   `_fact_ledger_blockers` 校验台账存在/可读、`dataset_sha256` 与当前黄金集
   哈希一致、**零 `human_ruling=pending`**；`run()` 将其并入 `--formal` 阻塞
   清单——任何 pending 裁定、缺失或过期哈希都拒绝 `--formal`，仅凭 50 题
   frozen 放行不再可能（当前 14 条 pending 裁定 + 50 题 reviewed 均为阻塞项）。
2. **逐 binding 重核，不信台账自填**（`gate_problems`）：证据索引唯一且有效
   （重复/无效/删除均硬失败）、doc 与证据一致、chunk 存在于该文档、**实际
   承载该证据引文与 element_ids**、支持位按 bound chunk **重算**并双向核对
   （recorded≠recomputed 即失败，true→false 与 false→true 皆捕获）。
3. **探针三分支确定性证明**：missing（杜撰数字 12.345678 事实）、pdf_only
   （G003 p1 前言页文本，不在任何 chunk）、rendering_only（G001 rendering
   转写措辞窗，不在任何 chunk/PDF 页）各以确定性样例断言精确分类，不再
   允许多种结果。

回归：`tests/test_golden_runner_gate.py` 11 项 + `tests/test_fact_evidence_
reconciliation.py` 17 项全绿（先红灯后修复：① ImportError、② 5 项绑定
篡改测试红、修复转绿）。台账重新生成，bindings 增加 `doc` 字段；数据集
sha 不变 `3b1f1cdc…6e89`。未自动裁定 14 条、未批量冻结、未跑 formal、
未动 PDF/索引/发布指针。

## 第 4 轮二次返工收口（权威验收 + 人工裁定注册表，2026-09-30）

第 4 轮二次审核驳回后按反例红灯→修复→转绿收口：

1. **反例红灯（根因证明）**：
   - ① frozen 数据集配**同哈希但内容缺陷**的台账——空缺一条 fact、重复一条
     entry、错绑一个 chunk_id——原入口仅查哈希+pending 字符串，全部放行
     （红灯）；clean 对照（14 条全裁定）此前也无从构造。
   - ② 裁定状态转换无权威定义：手改 `human_ruling` 字符串即可放行、重生成
     会覆盖裁定、裁定与逐 fact 规则（covered/refuse_meta 无需裁定）无冲突
     检查（红灯）。
2. **权威验收 `authoritative_acceptance`**（`scripts/reconcile_fact_evidence.py`，
   由 formal 入口实际调用）：台账存在/可读/哈希绑定 + 全部 95 fact 覆盖
   （空缺、重复 entry 硬失败）+ 逐 binding 复核（索引唯一/有效、doc 一致、
   chunk 实际承载引文与 element_ids、支持位重算双向核对）+ human_ruling
   按**重算的预期值**核对（手改字符串永不通过）+ 裁定记录冲突检查。
   `_fact_ledger_blockers(..., full=True)` 在 `--formal` 时调用它；
   diagnostic 只做廉价检查。
3. **可审计的人工裁定注册表** `fact_rulings.json`（独立文件，重生成只读
   不写——裁定记录不被覆盖）：每条记录含 case/fact_index/ruling
   (accepted/rejected)/adjudicator/date/justification/**fact 原文**/
   **classification**；状态转换权威规则 `expected_human_ruling`：refuse_meta→
   n/a、covered→machine_approved、机器不可证明例外→有完整 accepted 记录
   才 human_adjudicated、否则 pending（阻塞 formal）。裁定必须绑定 fact
   原文与当前分类——fact 改写或分类变化即失效（conflict），不得与逐 fact
   规则冲突（covered/refuse_meta 上有裁定即 conflict）。
4. **端到端绿灯**：14 条全部裁定 → 台账经 `reconcile(…, rulings)` 重生成
   （human_ruling 重算为 human_adjudicated，恰为 14 条）→ frozen 数据集 +
   重绑哈希 → `_fact_ledger_blockers(full=True)` 返回空（放行）；三个
   探针确定性测试与全部错绑测试保留并通过。
5. 真实 `--write`：数据集 sha 不变 `3b1f1cdc…6e89`、权威自检 PASS、
   14 条 pending 裁定保留（当前无注册表文件，预期值=pending）。未代替
   人工裁定、未批量冻结、未跑 formal、未动 PDF/索引/发布指针。

## 第 4 轮三次返工收口（分类重算，堵 refuse_meta 改标绕过，2026-09-30）

三次审核发现最后一个阻断：同哈希台账可把待裁定的**内容事实**改标
`refuse_meta`（连带 human_ruling=n/a）绕过绑定与人工裁定——根因是验收
信任台账自填的 `classification`，且 refuse_meta 分支提前跳过全部检查。
临时 frozen 副本 + 完整 formal 预检复现了放行。

- **红灯**：14 条 pending 内容事实整体改标 refuse_meta → formal 放行（红）；
  G011[0] 单条改标 refuse_meta / G011[0] 改标 covered / G028（真拒答）改标
  covered → gate 全放行（红）；对照（真拒答 8 条 + 全量对账）绿。
- **修复**：`classify_fact` 把既定分类规则**只陈述一次**（refuse 判定仅取
  `expected_answerability == refuse` → refuse_meta；承载并集支持 + 显式
  留痕 → 显式类；并集支持 → covered；补证据修复 → fixed_by_new_evidence；
  其余 → 真实探针阶梯）。`reconcile` 与 `gate_problems` 共用——验收对每条
  fact 从题目、证据与规则**重算类别**并与台账核对（不等即
  "classification ... != recomputed ... (relabeled)" 硬失败），只有真拒答题
  可走 refuse_meta；重算在 refuse_meta 跳过**之前**执行，改标无法绕过
  绑定与裁定检查。
- 裁定注册表、逐 binding 复核、三个探针确定性测试、错绑测试全部保留
  并通过。真实 `--write` 数据集 sha 不变 `3b1f1cdc…6e89`、权威自检 PASS、
  14 条 pending 裁定保留。未代替人工裁定、未冻结真实题目、未跑 formal、
  未动 PDF/索引/发布指针。

## 人工裁定材料（第 4 轮工程门禁审核 PASS 后，2026-09-30）

门禁审核 PASS，停止修改门禁。`scripts/prepare_adjudication_pack.py`
（只读；只写 `eval/audits/fact_reconciliation/adjudication_pack.md`）产出
14 条待裁定事实材料：每条含 fact 原文、承载 chunk（实际 chunk_id / 证据
索引 / 文档）、原 PDF 页及已核验页图、机器留痕与**待判断点**，交负责人
逐条接受/驳回（☐ 勾选），不代填裁定。裁定完成后按材料内流程：重生成
台账（注册表只读合入）→ 逐题确认 frozen → 只读复验正式门禁 → `--formal`
须另行授权。

## 剩余人工待核清单

| 标记 | 数量 | 说明 | 闭合方式 |
| --- | --- | --- | --- |
| `fact_coverage_needs_review` | 0 | G004[0] 已按第 3 轮审核意见改写 fact3（两指标名称与年份差异）后闭合 | 审核裁定改写 |
| `quote_not_in_single_chunk` | 0（词表保留） | 引文无法落入任一实际 chunk | 已全部由 chunk 感知重建消除 |

**pending 已清零，但全题 `status` 仍为 `reviewed`（未批量 frozen）——全题置 frozen
前：只允许诊断运行，`--formal` 硬拒绝，不报告正式 Recall、不报告 Precision@5
（无完整相关性标注）。第 2 步（正式 50 题评估）尚未运行；两轮收口合计改动了
多条证据锚点（重锚/补证据 15 条）与 1 条 fact 改写，`diagnostic-diag-step2-20260929`
的逐题命中结论不再对应现数据集，正式评估前无须重跑诊断（口径以正式运行为准）。**

## 评估口径（第 2 步运行前已定）

- 主报 **Recall@5**（must_hit 证据级，题级 complete_recall 附报），附报 Recall@10；
- **按 policy 双侧分层、独立排名**：`citable_recall_at_5/10` 统计 answer 证据在
  K 内存在 answer chunk 佐证（按首个可引用命中排名）；`citable_downgraded_at_5/10`
  统计 K 内有任意命中但同一 K 内无可引用命中的降级；`locator_only_recall_at_5/10`
  统计 locator 证据的定位命中（按首个任意命中排名）；
- 命中方法分层统计（`quote_and_ids` vs `page_quote`）；
- 拒答题（must_hit=False）不入检索分母；
- MRR 仅作参考，不作为验收指标。

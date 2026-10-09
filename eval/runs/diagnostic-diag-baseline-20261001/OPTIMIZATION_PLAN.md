# 单变量优化实验方案（供审核，审核通过前不执行）

> 基线 = 本目录 `diagnostic-diag-baseline-20261001`（**非正式**，50 题 reviewed /
> 14 条事实裁定暂停）。每个实验**一次只改一个变量**，另立
> `diagnostic-exp-<name>-<date>` run 目录，diagnostic 分类（不跑 formal），
> 结束后与基线并排输出对照表。全程：不调外部 LLM、无数据外发、不改黄金集、
> 不改已发布语料/索引/发布指针。

## 实验通用规范

- **固定追踪集合 = 基线 14 条漏命中证据**（G001[0]、G004[0]、G018[0]、G018[2]、
  G019[1]、G020[0]、G023[0]、G023[2]、G023[3]、G026[0]、G027[0]、G030[1]、
  G040[1]、G047[0]）。**转移口径（固定 14 条）**：每条基线漏命中只有两态——
  **救回**（基线 `match_rank=None` → 实验 `match_rank ≤ 10`，子标注救回@5 /
  救回@10 与命中 chunk / policy）或**不变**（仍 `None`）；基线均为漏命中，
  追踪集合内**不存在变差/改善态**；
- **转移口径（全量 77 条 must_hit，guardrail 集合）**：**变差**（基线命中 →
  实验未命中、或基线 ≤5 → 6–10、或同 @10 内排名上升 ≥2 位）/ **改善**（基线
  6–10 → ≤5、或排名下降 ≥2 位）/ **不变**（其余，含 ±1 位 RRF tie-break 抖动）；
  基线未命中的 14 条在该口径下计入**救回**；**净效应门槛：变差条数 ≤ 救回条数**；
- **trace 是最终排名的唯一权威**：救回/变差判定只取自实验 run 的 trace；
  **探针只作候选腿诊断**（解释"为什么没进 top-10"：dense/bm25 真实排名与
  prefetch 腿成员），**不能代替 rerank 最终排名**，其 RRF 名次不用于四态判定
  （RRF 并列分 tie-break 有 ±1 非严格稳定，见 DIAGNOSTIC_REPORT §3）；
- 每个实验连续跑 2 轮，**两轮各自独立 run_id**（`-r1` / `-r2`，不复用不覆盖），
  取延迟稳定口径（缺口 G8）后才作延迟对比；
- 采纳门槛按下表逐项判定，任一项不达即默认不采纳并记录在案。

## 采纳门槛（逐项定义）

| 指标类别 | 采纳门槛 |
| --- | --- |
| 命中率 | micro recall@5 净增 must_hit **≥ 2 条**（77 条分母下等价 Δ ≥ 0.0260；原 +0.03 小数表述按整数条数口径废弃）**且** micro recall@10 ≥ 基线 0.8182（不降） |
| 降级次数 | `citable_downgraded_at_5/10` ≤ 基线（各 1 条，G037[3]）；**不得新增**降级 |
| 分层 | 四个弱层（challenge:同义表达 / 图片图注 / 原文定位 / repr:定义）macro R@5 各自降幅 ≤ **0.05**；**guardrail**：challenge:相似干扰层（0.852）降幅 ≤ **0.02** |
| 延迟 | 纯检索类实验（E2/E3）：p50 增幅 ≤ **20ms**；E1 rerank 单列：新增重排耗时 p50 ≤ **1500ms/题** 且 p95 ≤ **3000ms**——超线则该实验仅记为"离线重排选项"，不进入在线路径 |
| 文档级 guardrail | document_hit@5 / @10 **不降** |
| 全局净效应 | 77 条 must_hit 中**变差条数 ≤ 救回条数**（防"救回 3 条挤掉 5 条"） |

## 对照指标（每个实验与基线并排报告）

| 类别 | 指标 |
| --- | --- |
| 主指标 | micro recall@5 / recall@10（77 must_hit） |
| 可引用 | citable recall@5 / @10（14 条）、citable_downgraded@5 / @10 |
| 定位 | locator_only recall@5 / @10（63 条）、page_hit@5 / @10 |
| 文档级 | document_hit@5 / @10（guardrail：不得下降） |
| 题级 | macro recall@5 / @10、complete_case_rate@5 / @10 |
| 分层 | challenge:同义表达 / 图片图注 / 原文定位 / repr:定义 四个弱层 macro |
| 坏例 | 14 条基线漏命中逐条四态（救回/改善/变差/不变）；G037[3] 降级是否解除 |
| 延迟 | p50 / p95 / mean（2 轮稳定口径；新增阶段的耗时单列） |

## E1 — RRF 后增加本地 rerank（G1，优先级 1）

### E1 执行配置（v2，已收口——机器可执行配置见 `E1_CONFIG.json`）

> **运行器已落地（2026-10-01）：** `scripts/run_e1_rerank_experiment.py` +
> 本地 fake-scorer 集成测试 `tests/test_e1_experiment_runner.py`。接线语义
> 与配置一致：融合请求 `limit=2×prefetch=40` 取回全部候选 → `RerankStage`
> 只重排 → **重排后**截取 top-10；`--disable-rerank` 时最终 top-10 与基线
> 组合逐字段一致（集成测试断言）；scorer 评分 None/NaN/±inf 一律硬失败；
> scorer 构建全程 `local_files_only=True`（fail-closed，绝不下载模型）。
> **运行前证据门（2026-10-06 补，硬拒绝于任何检索之前）：** ① revision
> 证据——模型+tokenizer 从本地 HF 缓存**同一快照**加载，快照目录名（40 位
> hex commit sha）作为非空不可变 revision 在权重加载前记入 run config，
> 无法确认（缓存缺失/非 40 hex/hub 不可用）即拒绝；② 运行时 embedding
> ID/revision 与基线 `unchanged_fields` 核对，漂移即拒绝；③ 控制轮独立
> `diagnostic-exp-e1-control-…` ID，run 目录已存在即拒绝（ID 不复用不覆
> 盖）；④ 重排耗时 `rerank_latency_ms` p50/p95/mean 单列汇总 + 未 frozen
> 题数入 run config。反例测试 `tests/test_e1_prerun_gates.py`（7 项）。
>
> **门禁前置 + 下载范围收口（2026-10-06 二轮）：** ⑤ run ID/目录防覆盖
> 门**移到任何 Qdrant 查询、任何模型加载、任何 scorer 构建之前**（注入
> 工厂端到端反例：目录已存在 → client/embedder/scorer 调用数全 0；目录
> 不存在 → 门放行并停在下一个边界）；⑥ **HF 下载精确范围**——filtered
> 6 文件（config.json + model.safetensors + tokenizer.json +
> tokenizer_config.json + special_tokens_map.json + sentencepiece.bpe.model）
> = **1,134,374,819 字节 ≈ 1.13 GB**（只读元数据查询，repo sha
> `2cfc18c9…8a70`），排除 pytorch_model.bin / onnx / README /
> .gitattributes；**已验证过滤缓存可被 local_files_only 按文件解析且恰好
> 极小**（`tests/test_hf_download_scope.py`）；完整 snapshot
> ≈3.36 GB——若选完整 snapshot 须按此容量重新申请。
>
> **同一条真实路径收口（2026-10-06 三轮，⑥验收返工）：**
> `_resolve_snapshot` 复用与授权下载**完全相同**的
> `FILTERED_ALLOW_PATTERNS` + `local_files_only`；**运行 revision 固定为
> 配置 `model_download.repo_sha`（`variable.revision` 已钉为同一 sha），
> 不允许回退到 main/分支 ref**（非 40 hex 即拒绝，hub 不被调用）；解析
> 出的快照目录名必须等于 pinned sha（不一致硬失败）且六文件齐全（缺任
> 一硬失败）。无网络反例（fake hub 断言收到与下载相同的
> revision+patterns）：完整仓库树清单 + 仅缓存六文件快照 → 解析成功；
> 缺任一必需文件 / revision 不一致 / revision 未固定 / 缓存缺失均硬失败
> （`tests/test_e1_prerun_gates.py`，13 项）。
> **本运行器尚未执行**——50 题实验与模型下载仍待授权。

- **唯一变量**：在 RRF 候选集上增加本地 cross-encoder rerank。**其余全部与
  基线一致**（`unchanged_fields` 逐字段钉死并有回归测试）；
- **候选获取深度 ≠ 最终 top-10（明确区分）**：候选获取深度 = 每腿 prefetch 20
  （dense 20 + bm25 20），服务端 RRF 融合候选集 **≤40** —— rerank 只在该候选集
  内重排；**最终 top-10 = 重排后取前 10**（limit 10 不变）。E1 **不加深
  prefetch**——加深 prefetch 是 E2 的唯一变量，两者不得混同；
- **rerank seam**：`src/retrieval/rerank.py::RerankStage`（已落地，只重排、
  不过滤、不改 usage_policy/文档字段；scorer 未接入时为恒等 = 基线链路；
  模块自身不下载不加载模型；**scorer 返回 None/NaN/±inf 一律 ValueError
  硬失败**）。合约测试 `tests/test_rerank_stage.py`（6 项）：
  重排关闭等价基线、重排只改顺序且 policy/来源字段不变、并列保持原序 +
  非有限分数硬失败、
  **A/B 类 8 条确在候选池 ∧ C 类 6 条在池外**（读基线探针数据断言）、
  unchanged_fields 逐字段对账基线 config、两轮独立 run_id + 下载未授权标记；
  集成测试 `tests/test_e1_experiment_runner.py`（5 项）：重排关闭 top-10 与
  基线逐字段一致、开启时候选集合/来源/policy 不变且截取在重排之后（融合
  第 40 名被 scorer 排第一必进 top-10）、非有限分数经接线路径硬失败、
  rerank_latency_ms 单列、top-10 恰为 limit 条；
- **rerank 模型**：`BAAI/bge-reranker-base`（278M 参数，CPU 可承受；多语言
  中文域），pin 具体 revision 执行时记录；pair =（question, chunk text），
  max_length 512，每题对 ≤40 条候选逐对打分；
  - 备选 `bge-reranker-v2-m3`（精度更高，但 CPU 耗时约 2×）——若 E1 延迟门槛
    （p50 ≤ 1500ms/题）超线，记录后可换 base 版重跑一次（换模型属新实验，
    另立 run 目录）；
  - **本地 HF 缓存当前无任何 reranker 模型**（已核查）——**HF 模型首次联网
    下载须由项目负责人明确授权，本配置提交不构成该授权**；
- **run 目录**：两轮**独立 run_id**：`diagnostic-exp-e1-rerank-<date>-r1` 与
  `…-r2`（不复用、不覆盖）；diagnostic 分类；
- **判定**：按"采纳门槛"表逐项判定（micro R@5 净增 ≥2 条且 R@10 不降、降级
  不新增、弱层与相似干扰 guardrail、p50 重排耗时 ≤1500ms、document_hit 不降、
  变差 ≤ 救回）；
- **探针角色**：跑完后 `probe_miss_rerank.py` 只作候选腿诊断（解释救回/未救回
  的原因）；四态判定一律取自实验 run 的 trace。

### 预期作用面与风险

- **预期作用面**：A 类 2 条（双腿入 prefetch 被挤出）+ B 类 6 条（单腿在
  prefetch 内）——8/14 条漏命中的承载 chunk 已在 RRF 候选集内，rerank 有
  直接机会；C 类 6 条不在候选集，预期不变；
- **风险**：延迟上升（CPU rerank ≤40 条/题）；模型选择与打分语言域适配需先
  小样本冒烟（≤5 题试跑，不计入对照）。

## E2 — prefetch 20→50（G2，优先级 2）

- **唯一变量**：`--prefetch-limit 50`，其余全部不动；
- **背景**：旧数据集受控实验曾否定 prefetch 单独收益（20/50/100 三档重放，
  无一漏命中进 top-10 且部分变差）；在新数据集上复核一次该结论（E1 依赖
  候选集质量，此实验同时为"rerank 候选池是否需要加深"提供依据）；
- **可触达坏例重算**（依据探针 dense/bm25 全库真实排名，dense ≤50 或 bm25 ≤50
  即该腿可触达）：

  | 坏例 | dense | bm25 | pf20 腿成员 | pf50 新增腿 | pf50 可触达 |
  | --- | --- | --- | --- | --- | --- |
  | G001[0] | 27 | 326 | 无 | dense | ✔ |
  | G004[0] | 26 | 9 | bm25 | dense | ✔ |
  | G018[0] | 43 | 162 | 无 | dense | ✔ |
  | G018[2] | 253 | 30 | 无 | bm25 | ✔ |
  | G019[1] | 386 | 14 | bm25 | — | ✔（维持 bm25 单腿） |
  | G020[0] | 18 | 12 | 双腿 | — | ✔（维持） |
  | G023[0] | 24 | 7 | bm25 | dense | ✔ |
  | G023[2] | 20 | 11 | 双腿 | — | ✔（维持） |
  | G023[3] | 1421 | 122 | 无 | 无 | **✘ 不可触达** |
  | G026[0] | 53 | 12 | bm25 | — | ✔（dense 53 仍在外） |
  | G027[0] | 113 | 20 | bm25 | — | ✔（维持） |
  | G030[1] | 477 | 490 | 无 | 无 | **✘ 不可触达** |
  | G040[1] | 45 | 6 | bm25 | dense | ✔ |
  | G047[0] | 254 | 68 | 无 | 无 | **✘ 不可触达** |

  **pf50 可触达 11/14；不可触达 3 条**（G023[3]、G030[1]、G047[0]，双腿均远在
  50 外——E2 无论结果如何都救不了这 3 条，它们只能靠 E1 类 rerank 之外的手段
  或黄金侧复核）；新增 dense 腿 5 条（G001[0]/G004[0]/G018[0]/G023[0]/G040[1]）、
  新增 bm25 腿 1 条（G018[2]）；
- **风险**：旧实验观察到部分条目反而变差（更多 chunk 获双腿挤占名次）。

## E3 — 查询侧确定性同义扩展（G3，优先级 3，先设计后执行）

- **唯一变量**：检索前对 question 做本地词典同义扩展（如 内涝↔积水、
  混接↔错接/雨污混入），扩展词**拼入查询文本**（dense 与 bm25 同源同一查询）；
  词表、拼法、命中逻辑冻结后不变；
- **预期作用面**：challenge:同义表达层（0.583/0.583，n=4）与 C 类部分条目
  （G001[0] 内涝定义 bm25 326 失联）；
- **前置**：词表初稿 + 逐词来源留痕提交审核后才可运行；**不用外部 API、
  不调 LLM 生成改写**；
- **风险**：扩展词引入干扰项使相似干扰层（现 0.852/0.920）下降——该层列入
  重点 guardrail。

## E4 — 邻接块扩展（G4，仅立项待设计，本轮不执行）

- 检索命中后拼接同文档相邻 chunk 参与后续评分/展示（不改索引、不改分块）。
  涉及"命中定义"的口径变化，须先提交独立设计（扩展窗口、评分口径、指标
  影响面）审核后再实验；本轮仅记录立项。

## 不在本方案范围

- **完整 source_lookup 路径测试**（scope→jurisdiction 扩展→usage_policy
  过滤→evidence 映射）：独立测试轨道，非单变量优化，另报方案；
- **生成层 / 端到端评估（G5/G6）**：需真实 LLM 调用与数据外发授权，另行申请；
- **formal 评估**：14 条事实裁定 + 全题 frozen 后另行授权；
- **语料侧改动**（chunk 策略、OCR 修复、policy 断言补充）：触及已发布语料，
  需重建语料的正式流程，不在本轮。

## 执行顺序建议

E1（rerank，预期收益最大且作用面覆盖 8/14 条漏命中）→ E2（prefetch 复核，
为 E1 候选池决策提供依据）→ E3（同义扩展，需词表审核）。每个实验执行前先
提交该实验的精简配置差异说明；执行后提交对照表 + 坏例四态清单，审核后再
决定下一实验。

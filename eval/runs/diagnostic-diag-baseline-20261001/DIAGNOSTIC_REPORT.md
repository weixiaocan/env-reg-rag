# 诊断检索基线报告（2026-10-01，数据集对账收口后首轮）

> **DIAGNOSTIC RUN — 非正式基线（not a formal baseline）**。50 题全部
> status=reviewed（人工裁定暂停，14 条事实待裁定、未冻结），`--formal` 被运行器
> 硬拒绝。本轮为可复现 diagnostic 检索基线，数字不得引用为正式 Recall；正式
> 评估须待全题 frozen + 事实裁定闭合后经 `--formal` 另行授权运行。

## 1. 组件清单与缺口（发现问题 → 收集证据 → 根因 → 方案）

### 1.1 检索组件（本轮被测范围 = **无过滤的 dense+BM25+RRF 检索核心**，全部只读）

> 被测组件：稠密向量、混合索引（RRF 模式）、策略投影、分块。
> `qdrant_evidence_retriever.py`（scope→jurisdiction 扩展重排→usage_policy
> 过滤→evidence 映射，即完整 source_lookup 路径）**未被本轮行使，仍列为待测**。

| 组件 | 文件 | 职责 | 关键参数 |
| --- | --- | --- | --- |
| 稠密向量 | `src/retrieval/bge_small_zh.py` | BAAI/bge-small-zh-v1.5 @`7999e1d`，512 维，CPU，query 指令前缀 | max_length 512，L2 归一 |
| 混合索引 | `src/retrieval/qdrant_index.py` | exact_dense / ann_dense / bm25 / rrf 四模式；RRF = dense(exact, prefetch) + bm25(prefetch) → 服务端 Fusion.RRF | Bm25Config MULTILINGUAL 分词 + IDF；payload 缺 policy 时 fail-safe 为 source_locator_only |
| 生产检索适配 | `src/adapters/qdrant_evidence_retriever.py` | EvidenceRetriever seam；jurisdiction=[地方,全国] 扩展重排；usage_policy 过滤 | limit 默认 10 |
| 策略投影 | `src/application/evidence_policy.py` | chunk_usage_policy（元素级严格者胜） | 答案可引用性的唯一权威 |
| 分块 | `src/application/v2_chunker.py` | chunk_document 离线可复现（含真实 chunk_id） | 4232 点 = 指针一致 |

### 1.2 生成组件（本轮**未被测**——不调 LLM）

| 组件 | 文件 | 职责 |
| --- | --- | --- |
| 编排 | `src/application/query_service.py` | scope→检索→（全 locator 则 SEARCH_ONLY）→生成→引用校验（claims 必须绑定 answer_and_citation 证据；conflicts 须 ≥2 文档版本）→状态机 ANSWERED / REFUSED / SEARCH_ONLY / CONFLICTING_SOURCES / NEEDS_CLARIFICATION |
| 结构化生成 | `src/adapters/langchain_answer_generator.py` | 证据约束提示词 + JSON schema 输出（claims/conflicts），**仅把 answer_and_citation 证据送入 LLM**；输出解析失败即抛错 |
| 提供方配置 | `src/adapters/llm_provider.py` | zhipu(GLM)/openai/deepseek profile，env 配置 + 成本核算 |
| scope 解析 | `src/application/scope_resolution.py` | 多轮条件/显式会话条件 |
| 追溯 | `src/adapters/jsonl_query_trace_recorder.py` | 逐查询 trace 落盘 |

### 1.3 评估组件

| 组件 | 文件 | 职责 |
| --- | --- | --- |
| 确定性匹配器 | `src/evaluation/retrieval_harness.py` | 文档+页+引文内容门三缺一不可；ID 相交仅升级标签；citable 双侧门（标注 ∧ 命中 chunk policy）+ 三组独立排名；HTML 报告强制 DIAGNOSTIC 横幅 |
| 运行器 | `scripts/evaluate_golden_retrieval.py` | formal 硬门（零 pending ∧ 全 frozen ∧ 事实台账哈希绑定 ∧ 零 pending 裁定）；diagnostic 强制前缀 + 非正式声明 |
| 漏命中探针 | `scripts/probe_miss_rerank.py`（本轮新增，只读） | 漏命中逐条定位承载 chunk + dense/bm25 全库真实排名 + RRF 融合排名 |
| 黄金集 | `eval/golden_v2.jsonl` sha `3b1f1cdc…6e89` | 50 题 / 80 evidence / 77 must_hit / pending 标记 0 / 全 reviewed |
| 回归 | 1013 项测试全绿 | 含合约/匹配器/门禁/对账 32 项 |

### 1.4 缺口清单（按"问题→证据→根因→方案"）

| # | 问题 | 证据 | 根因 | 方案候选（一次一个变量，供审核） |
| --- | --- | --- | --- | --- |
| G1 | 页/块级排序弱：document_hit@10 0.995 vs recall@10 0.818 | 本轮 micro 指标 + 14 条漏命中逐条排名 | RRF 后直接截断，**无 rerank 阶段**；短 chunk（图注 68 字、短定义 40 字）信号稀薄 | E1 本地 rerank（cross-encoder，不调 LLM）；E2 邻接块扩展 |
| G2 | 双腿入 prefetch 仍被融合挤出 | G020[0] dense18/bm25→RRF 12；G023[2] 20/11→14 | RRF 常数 k 下融合分 ~0.13 不足，中游 chunk 密集 | E3 prefetch 分档对照（旧数据集受控实验已否定其单独收益，新数据集可复核一次） |
| G3 | 单腿在 prefetch 内仍不足 | G004[0] 26/9→14；G023[0] 24/7→17；G026[0] 53/12→18；G040[1] 45/6→12；G019[1] 386/14→27 | dense 腿对表格行/前言/OCR mojibake 文本弱 | E1 rerank 同样覆盖；E4 查询侧同义扩展（本地词典，不外发） |
| G4 | 双腿均在 prefetch 外 | G001[0] 27/326、G018[0] 43/162、G018[2] 253/30、G023[3] 1421/122、G030[1] 477/490、G047[0] 254/68 | 短定义/原则句/图注 chunk 语义信号天然稀薄；G030[1] 系 24 字定位锚 | 先黄金侧确认锚点合理性（不改黄金事实），再评估 E2 |
| G5 | **生成层零评估** | 无 answer 级 runner：G015 库内拒答、G008/G028/G050 库外拒答的回答侧行为、claim→evidence 绑定正确率均未测 | 黄金集 schema 只覆盖检索 | E5 生成评估设计（**需真实 LLM 调用授权**，另行申请） |
| G6 | **端到端（query_service）零评估** | SEARCH_ONLY/REFUSED/citation-validation 路径未在黄金集系统检验 | 同上 | E5 一并覆盖（skip_generation 模式可先测非 LLM 路径） |
| G7 | citable 池小（14/77） | by_policy：citable 14 条 vs locator 63 条 | 结构化公式/表格元素缺 review 断言 → policy 限制，**非检索缺陷** | 流程性问题，随人工裁定与后续标注解决 |
| G8 | 延迟口径单轮 | 本轮 p50 61.8ms vs 上轮（旧数据集）23.9ms | 单机负载波动，单次运行无稳定性数据 | 基线复跑 2–3 轮取稳定口径后再作对比 |
| G9 | 分层样本小 | by_label 每组 4–24 题 | 50 题规模限制 | 分层仅作方向性参考，不据此单独改策略 |

## 2. 运行身份（可复现）

| 项 | 值 |
| --- | --- |
| run_id | `diagnostic-diag-baseline-20261001`（classification=diagnostic，强制前缀） |
| 数据集 | `eval/golden_v2.jsonl`，sha256 `3b1f1cdc459aadb50bd1b39531bc90f51ecc25a0d969612d41612fa011bd6e89`（对账收口版，80 evidence / 77 must_hit） |
| 语料指针 | `data/registry/corpus-current.json` → `corpus-0b9a1fd84e3d`（published，alias `corpus_v2_live`，**未动**） |
| 集合 | `corpus_v2__staging__corpus-0b9a1fd84e3d__bi-d70cf457e0cac3fa3abbce0a`，4232 点（启动时与指针核对通过） |
| 被测路径 | **无过滤的 dense+BM25+RRF 检索核心**（dense exact bge-small-zh-v1.5 + BM25 + 服务端 RRF，prefetch 20，limit 10，`filters=None`，未改任何变量）。完整 source_lookup 路径（scope 解析 → jurisdiction 扩展重排 → usage_policy 过滤 → evidence 映射，`src/adapters/qdrant_evidence_retriever.py`）**不在本轮被测范围，仍列为待测**——本报告全部指标仅代表检索核心 |
| LLM | 未调用；无数据外发 |
| 复现命令（Windows Git Bash） | `PYTHONIOENCODING=utf-8 PYTHONPATH=. ./.venv/Scripts/python.exe scripts/evaluate_golden_retrieval.py --run-id <id>`；漏命中探针（内置数据集哈希/语料版本/集合身份/点数校验，失配即拒绝）：`PYTHONIOENCODING=utf-8 PYTHONPATH=. ./.venv/Scripts/python.exe scripts/probe_miss_rerank.py --run-dir eval/runs/diagnostic-diag-baseline-20261001` |

## 3. 产物一致性核对

- summary.json ↔ 50 份逐题 trace 用 `summarize_results` 重算**逐字段一致**
  （仅运行器附加的 5 个元数据字段为预期差异）；
- 50 题 × 10 hits、每条 evidence 三组排名字段齐全；
- @10 漏命中 14 条由 trace 重导，与 `miss_rerank_probe.json` 逐一对应，
  每条**唯一确定承载 chunk**（引文 ⊆ chunk 文本 ∧ element_ids ⊆ chunk 元素集）；
- **探针可复现性复核**（探针增加运行前绑定校验后重跑）：数据集哈希/语料版本/
  集合身份/点数校验全通过；dense 与 bm25 全库排名 14/14 条**逐条一致**；RRF 融合
  排名在 3/14 条上出现 ±1 漂移（G019[1] 27→26、G026[0] 18→19、G027[0] 32→31）
  —— 服务端 RRF 并列分 tie-break 非严格稳定，三条均远在 top-10 外，**本报告结论
  不受影响**；探针红灯验证（篡改 config 的 corpus_version / dataset_sha256 /
  collection 三种反例）全部 REFUSED；
- report.html 标题与横幅均标注 **DIAGNOSTIC（非正式基线）**。

## 4. 总体指标（micro，77 条 must_hit / 47 个检索题）

| 指标 | @5 | @10 |
| --- | --- | --- |
| Recall（任意命中） | 0.7013（54/77） | 0.8182（63/77） |
| 可引用 citable（answer 证据 ∧ answer chunk） | 0.6429（9/14） | 0.7857（11/14） |
| 可引用降级 citable_downgraded | 1（G037[3]） | 1（G037[3]） |
| 仅定位 locator_only | 0.6984（44/63） | 0.8095（51/63） |
| macro（题级） | 0.7216 | 0.8493 |
| complete_case_rate | 0.5957 | 0.7660 |
| document_hit | 0.9574 | 0.9947 |
| page_hit | 0.8156 | 0.9007 |

- **citable 降级 1 条（G037[3]）**：证据标注 answer_and_citation，rank1 命中的
  locator chunk 过引文门、真正的 answer chunk 未进 top-10 —— 双侧门 + 独立排名
  按设计把这类"标注可引用但实际命中的是 locator chunk"暴露为降级而非虚报。
- 拒答题（must_hit=False）不入分母；repr:拒答 4 题 document 定位 1.000。
- 与旧诊断（旧数据集 62 条 must_hit）**不可直接对比**：两轮收口重锚/补证据 15 条 +
  1 条 fact 改写，锚点已变。

**延迟（唯一口径，单轮）**：p50 **61.84ms** / p95 **128.44ms** / mean **69.42ms**
（每题，含 CPU embed + RRF；单机单轮，见缺口 G8）。

## 5. 分层弱项（macro R@5 / R@10，n）

| 维度 | 表现 |
| --- | --- |
| challenge:库外 | 0.000/0.000（n=3，库外拒答无 must_hit，仅定位锚不入分母——预期行为） |
| challenge:图片 / modality:图片图注 | 0.458/0.708（n=8，图注 chunk 信号稀薄） |
| repr:流程要求 | 0.550/0.750（n=10） |
| challenge:原文定位 | 0.562/0.812（n=16） |
| challenge:同义表达 | 0.583/0.583（n=4） |
| repr:定义 | 0.583/0.724（n=16） |
| 强项参照 | challenge:公式 0.896/0.958、challenge:相似干扰 0.852/0.920、repr:数值/表格 中游偏强 |

## 6. 坏例清单（@10 漏命中 14 条，逐条三档真实排名）

`miss_rerank_probe.json`（本目录）为原始数据；RRF 排名为本 run 同配置
（prefetch 20，无过滤检索核心）融合真实排名，`None` = 承载 chunk 未进融合候选集；
RRF 并列分 tie-break 存在 ±1 非严格稳定（见 §3），dense/bm25 排名严格可复现。

### A. 双腿入 prefetch 仍被 RRF 挤出（融合中游不足）

| 坏例 | 证据 | dense | bm25 | RRF@pf20 |
| --- | --- | --- | --- | --- |
| G020[0] | 征求意见稿 p22 图注+条文解释 | 18 | 12 | **12** |
| G023[2] | QXT489 p10 区域等级 | 20 | 11 | **14** |

### B. 单腿在 prefetch 内，融合 12–27

| 坏例 | 证据 | dense | bm25 | RRF@pf20 | 归因 |
| --- | --- | --- | --- | --- | --- |
| G040[1] | GBT47884 p10 表1 全表 | 45 | 6 | 12 | bm25 单腿 |
| G004[0] | 十四五规划 p2 表1 | 26 | 9 | 14 | bm25 单腿，表格行 dense 弱 |
| G023[2]→见 A；G023[0] | GBT28592 p4 等级表 | 24 | 7 | 17 | bm25 单腿 |
| G026[0] | 工作指南 p3 前言 | 53 | 12 | 18 | bm25 单腿 |
| G019[1] | CECS758 p28 7.2.1 正文 | 386 | 14 | 27 | OCR mojibake（"1昆接"类）伤 dense |
| G027[0] | 资产管理指南 p16 图1 图注 | 113 | 20 | 32 | bm25 边界（恰第 20） |

### C. 双腿均在 prefetch 外（chunk 信号稀薄）

| 坏例 | 证据 | dense | bm25 | 归因 |
| --- | --- | --- | --- | --- |
| G001[0] | 内涝定义 p8 | 27 | 326 | 40 字短定义，bm25 失联 |
| G018[0] | GB55027 1.0.3 原则 p4 | 43 | 162 | 原则句与问题语义远 |
| G018[2] | GB55027 p1 前言/基本规定 | 253 | 30 | 双腿弱（本轮新增证据） |
| G023[3] | TCMSA p5 术语定义 | 1421 | 122 | 术语引用注 token 少（本轮新增证据） |
| G030[1] | TCECS1764 p12 图注 | 477 | 490 | 24 字定位锚，天然难检 |
| G047[0] | CECS758 p28 评估原则 | 254 | 68 | 原则句 vs 表值问题 |

### 降级坏例（非漏命中）

- **G037[3]（citable 降级 @5 与 @10 各 1）**：answer chunk 未进 top-10，rank1 为
  同引文佐证的 locator chunk —— 匹配器行为正确，暴露的是排序问题（A/B 类同源）。

## 7. 结论与下一步（待审核，本轮不动手）

1. 本轮为**可复现 diagnostic 基线**：配置、逐题 trace、分层指标、延迟、坏例
   清单（含逐条三档真实排名）全部落盘于本目录；原基线保留不动，后续优化实验
   一次只改一个变量、另立 run 目录并附对照表（实验方案见
   `OPTIMIZATION_PLAN.md`，审核通过后才执行）；
2. 漏命中根因分布：A 类 2 条、B 类 6 条、C 类 6 条——核心缺口是 **RRF 后无
   rerank**（G1）与**稀薄 chunk 无扩展**（G2/G4）；
3. 优化候选实验按 1.4 表排序，**进入任何实验前先提交本报告供审核**；
4. 完整 source_lookup 路径（scope→jurisdiction 扩展→usage_policy 过滤→evidence
   映射）不在本轮被测范围，**仍列为待测**（见 §2）；生成层与端到端评估（G5/G6）
   尚不存在，设计需**真实 LLM 调用与数据外发授权**，另行申请；skip_generation 的
   非 LLM 端到端路径可先行设计；
5. 黄金侧 14 条事实裁定暂停中（按要求不写裁定、不冻结）；`--formal` 继续被硬拒，
   本报告全部数字**不得引用为正式 Recall**。

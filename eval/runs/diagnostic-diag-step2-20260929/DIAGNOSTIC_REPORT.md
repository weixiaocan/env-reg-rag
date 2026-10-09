# 第 2 步只读诊断性检索评估报告（可信度收口版）

> **DIAGNOSTIC RUN — 非正式基线（not a formal baseline）**。数据集携带 90 个未闭合
> 人工待核标记、50 题全部 status=reviewed；本轮只诊断，不据此改分块或排序，数字
> 不得引用为正式 Recall。正式 50 题评估须待待核清单全闭合并全题 frozen 后以
> `--formal` 另行运行（运行器硬门继续生效）。

## 运行身份（只读）

| 项 | 值 |
| --- | --- |
| run_id | `diagnostic-diag-step2-20260929`（classification=diagnostic，强制前缀） |
| 数据集 | `eval/golden_v2.jsonl`，sha256 `ca524d78b1d00e8e61476df2d45d5ee488e981f27221e7cc0eab74697c838256`（收口核对：与运行时 config 记录一致，数据集未改动） |
| 语料指针 | `data/registry/corpus-current.json` → `corpus-0b9a1fd84e3d`（published，alias `corpus_v2_live`） |
| 集合身份 | `corpus_v2__staging__corpus-0b9a1fd84e3d__bi-d70cf457e0cac3fa3abbce0a`（build_identity `bi-d70cf457e0cac3fa3abbce0a`） |
| 点数核对 | 指针 chunk_count=4232 = Qdrant 实际 points_count=4232（运行器启动时校验通过） |
| 检索链路 | 生产 dense（BAAI/bge-small-zh-v1.5 @7999e1d，exact）+ BM25 + 服务端 RRF，prefetch 20，limit 10 |
| LLM | 未调用（纯检索评估）；黄金集、索引、正式语料零改动 |

## 产物一致性核对（本轮收口新增）

- **summary.json ↔ 50 份逐题 trace**：用 `summarize_results` 从 50 份 `cases/G*.json`
  重算全部聚合（macro / micro / by_policy / by_label / complete_case_rate / latency），
  与 summary.json **逐字段一致**（含 by_label 20 个维度无差异）；
- 逐题 trace 完整性：50 题 × 10 hits、每条 evidence 三组排名字段齐全；
- @10 漏命中清单由 trace 重导：11 条，与下表逐一对应；
- **report.html 已重建**（`regenerate_report`，仅重写 HTML，summary/config/cases 原样）：
  标题与横幅明确标注 **"DIAGNOSTIC — 非正式基线（not a formal baseline）"**、
  pending 90 项 / 未 frozen 50 题、延迟三数字与 summary.json 一致；
  渲染规则有回归测试（`tests/test_report_rendering.py`，4 项）。

## 总体指标（micro，62 条 must_hit 证据 / 47 个检索题）

| 指标 | @5 | @10 |
| --- | --- | --- |
| Recall（任意命中） | 0.7419（46/62） | 0.8226（51/62） |
| 可引用 citable（answer 证据 ∧ answer chunk） | 0.6364（7/11） | 0.8182（9/11） |
| 可引用降级 citable_downgraded | 0 | 0 |
| 仅定位 locator_only | 0.7647（39/51） | 0.8235（42/51） |
| macro（题级） | 0.7518 | 0.8475 |
| complete_case_rate | 0.6809 | 0.7872 |
| document_hit | 0.9645 | 0.9929 |
| page_hit | 0.8369 | 0.9007 |

**citable_downgraded=0**：11 条 answer 证据无一被 locator chunk 抢先佐证后遮蔽
——首轮门禁返工（双侧门 + 独立排名）在此数据集上没有可观测的遮蔽事件；G019[1]
本轮两条佐证 chunk 均未进 top-10（见坏例表），遮蔽场景未触发。

**延迟（与 summary.json 统一的唯一口径）**：p50 **23.86ms** / p95 **33.30ms** /
mean **25.35ms**（每题，含 embed + RRF；初版报告的 22.7/45.3/28.1 系口径错误的
临时计算，已废弃）。

## 题型分层（macro R@5 / R@10，节选）

| 维度 | 表现 |
| --- | --- |
| 最弱：challenge:同义表达 | 0.500 / 0.500（4 题，G001/G013 类定义改写） |
| 弱：challenge:图片 0.5625/0.6875、repr:流程要求 0.55/0.75、modality:正文 0.639/0.639 | 图注/短正文 chunk 信号稀薄 |
| 弱：challenge:多证据 0.679/0.679、challenge:多文档 0.667/0.667 | 需多证据齐命中才 complete |
| 中：repr:定义 0.615/0.708、challenge:原文定位 0.625/0.813 | 长尾定义段召回靠 @10 补 |
| 强：repr:数值 0.889/0.926、challenge:相似干扰 0.879/0.924、challenge:公式 0.875/0.938、challenge:表格 0.781/0.913 | 数值/表格 token 区分度高 |

citable 分布集中在数值/表格题（citable@5=0.571~0.714）；公式题 8 题全部
locator-only（结构化公式无 review 断言 → policy 限制，非检索缺陷）。

## 逐条坏例诊断（@10 共 11 条证据未命中）

### 排名重测方法（本轮收口新增，全部只读）

每条漏命中：① 归一化文本匹配（引文 ⊆ chunk 文本 ∧ element_ids ⊆ chunk 元素集）
**唯一确定承载 chunk**；② 全库 exact dense（limit=4232）得 dense 真实排名；
③ BM25（limit≥1000）得 bm25 真实排名；④ prefetch=20/50/100 三档受控重放 RRF
（同一问题、同一 embed、仅改 prefetch），得融合真实排名。原始数据：
`miss_rerank_probe.json`（本目录）。初版报告的 per-leg 数字除 **G004 一处测量
错误外**均与重测一致（G004 修正见下）。

### A. 检索问题（承载 chunk 在索引内，融合排名出不了 top-10）

| 坏例 | 证据 | dense 真排名 | bm25 真排名 | 融合@pf20 / @pf50 / @pf100 | 主要归因 | 次要因素 |
| --- | --- | --- | --- | --- | --- | --- |
| G004[0] | 十四五规划 p2 表 1 | **26**（初版误记 3，已修正） | 9 | 14 / 11 / 12 | 表格行 chunk 双腿中等（26/9），RRF 融合分不足 | 问题对照两份文档，数字语义重叠分散 |
| G020[0] | 征求意见稿图 2 条文解释 p22 | 18 | 12 | 12 / 12 / 12 | **两腿均在 prefetch=20 内但融合分仅 ~0.130，挤出 top-10（真融合排序案例）** | 图注+条文解释混排 chunk 信号稀薄 |
| G023[2] | QXT489 区域等级 p10 | 20 | 11 | 14 / 18 / 18 | 同上：两腿入 prefetch，融合分 ~0.131 排 14 | 等级定义段 token 区分度低 |
| G026[0] | 工作指南前言 p3 | 53 | 12 | 19 / 21 / 17 | dense 53 在 prefetch 外，仅 bm25 单腿（融合 19） | 前言"里子工程"叙述 vs 问题问"样板区" |
| G001[0] | 内涝定义 p8 | 27 | 326 | >100 / 47 / 51 | 短定义 chunk（40 字）嵌入信号不足 | 引文 token 稀疏致 bm25 完全失联 |
| G018[0] | GB55027 1.0.3 原则 p4 | 43 | 162 | >100 / 72 / 76 | 原则句 chunk 与问题语义距离远 | 双腿均弱 |
| G019[1] | CECS758 7.2.1 p28 | 386 | 14 | 26 / 28 / 28 | 正文 OCR mojibake（"泪接源/1昆接"）+ 问题问数值而正文谈方法 | bm25 14 单腿，融合仅到 26 |
| G027[0] | 资产管理指南图 1 图注 p16 | 113 | 20 | 32 / 32 / 33 | 图注 chunk 全文仅 68 字，dense 113 在 prefetch 外 | bm25 恰在 prefetch 边界（第 20） |
| G047[0] | CECS758 评估原则 p28 | 254 | 68 | >100 / >100 / >100 | 原则句 chunk 与表值问题语义远，双腿均在 prefetch 外 | —— |

**G004 实际 RRF 排名过程（专项复核）**：生产 RRF 的 dense prefetch 腿为
`SearchParams(exact=True)` limit=20 —— 承载 chunk `chunk_fc68a40b…` 全库 dense
排 **26**，**不在 dense 腿前 20**；bm25 腿排 9（在腿内）。prefetch=20 时它只以
bm25 单腿参与融合，RRF 分 = 1/(2+8) = 0.1，融合排名 **14**。prefetch 提到
50/100 后它获得双腿（1/(2+25)+1/(2+8) ≈ 0.137），融合排名 11/12 —— **仍进不了
top-10**。初版报告"dense 3 / bm25 7，两腿均前 10 却被 RRF 挤出"为测量错误
（误将同文档其他 chunk 记作承载 chunk），已撤回；真正的"双腿入 prefetch 仍被
融合挤出"案例是 **G020[0] 与 G023[2]**（融合 12/14 名）。

**prefetch 改善断言（撤回）**：初版报告称"RRF prefetch 提升到 ≥50 可救回
G004/G023[2]/G020/G026/G027 类"。本轮受控实验（同题同 embed，prefetch
20→50→100 三档重放）表明：**11 条漏命中没有任何一条在 prefetch 50 或 100 下
进入 top-10**；最好的也只是 G004 14→11、G023[0] 17→12、G001 >100→47，且
G023[2]（14→18）、G026[0]（19→21）、G019[1]（26→28）反而**变差**（更多
chunk 获得双腿后挤占名次）。该断言无对照实验支持且被对照实验否定，**正式
撤回**；prefetch 调整不构成本轮诊断的可行改进方向。

### B. 黄金证据待核问题（证据本身弱，pending 已标）

| 坏例 | 待核标记 | 重测排名（dense/bm25/融合@20） | 主要归因 | 次要因素 |
| --- | --- | --- | --- | --- |
| G023[0] | pdf_rendering_faithfulness + fact_coverage_needs_review | 24 / 7 / 17 | 引文是整页混合窗口（术语表+目录+等级划分混排），fact_coverage<0.50 已标——引文未对准题目所需事实 | 融合排名 17，检索侧其实接近 |
| G030[1] | page_reanchored + fact_coverage_needs_review | 477 / 490 / >100 | 引文仅图注一行 24 字（v1 p12→v2 p11 重锚），天然难检索 | 主证据 G030[0] 已命中，此条为定位锚 |

（G019[1] 同时带 fact_coverage_needs_review，但其主要归因是 A 类排名问题，
已在 A 表列出。）

### C. policy 限制（非检索缺陷，计 locator_only 不计 citable）

- 公式题 8 题（G002/G016/G030 等）证据 policy=source_locator_only：结构化公式
  元素缺 review 断言（D3 门），**命中只算定位**——citable 池因此只有 11 条；
- G047[0] 标注 answer_and_citation 但其承载正文 chunk 因排名未出 top-10（A 类）
  未命中；同页表 7.2.2 chunk（locator）排第 2 但引文门未过（引文为原则句非表行），
  **不构成命中也不构成降级**——匹配器行为正确；
- 首个命中遮蔽场景（locator 排前 + answer 排后同 top-10）在本轮 50 题中
  **未出现**（citable_downgraded=0），独立排名逻辑待更大样本验证。

## 结论（只诊断，不动手）

1. @10 未命中的 11 条证据中：**9 条为检索/融合排序问题**（承载 chunk 均在索引
   内，逐条附 dense/bm25/融合三档真实排名，可复现）、**2 条黄金证据本身待核**
   （G023[0]、G030[1]，pending 已标）；
2. 融合排序问题的两种形态：**双腿入 prefetch 仍被融合挤出**（G020/G023[2]，
   融合分 ~0.13 量级不足）与**单腿或双腿均在 prefetch 外**（其余 7 条，chunk
   本身信号稀薄：短定义、图注、前言叙述、原则句、OCR mojibake）；
3. document_hit@10=0.993 vs recall@10=0.823：文档几乎总能找对，差距在
   **页/块级排序**——与"短定义、图注、前言叙述类 chunk 信号稀薄"的归因一致；
4. citable 池仅 11/62 且全部集中在数值/表格正文类证据——policy 限制（结构化
   元素缺 review 断言）是池小的主因，不是检索缺陷；
5. **本轮不做任何检索侧改动**；prefetch 调整已被受控实验否定（见上），短
   chunk/图注类策略若要评估须另设计对照实验；黄金侧先闭合 G023[0]/G030[1] 的
   fact_coverage 待核再评估其坏例归类。

**本报告为诊断运行产物，不得称为正式基线；黄金集未全题 frozen 前正式评估
继续被运行器硬拒绝（`--formal` → SystemExit）。**

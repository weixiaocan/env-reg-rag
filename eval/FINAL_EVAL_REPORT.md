# 评估总报告（corpus-7411b3a05a07，2026-10-15）

口径：**diagnostic，非 formal**。黄金集 50 题全部为 `reviewed`、未冻结，`--formal` 门禁保持关闭。语义判定由 agent 对照 `expected_facts` 给出，不是独立人工裁定。

## 1. 固定身份

| 项 | 值 |
| --- | --- |
| 正式语料 | `corpus-7411b3a05a07`，3885 chunks，`corpus_v2_live` → `corpus_v2__staging__corpus-7411b3a05a07__bi-c3d507491436d14e1443a956` |
| 黄金集 | `eval/golden_v3.jsonl`，SHA-256 `c5b30aca3ce8a32f92f1f07e970ae9bb957040589274ba3532ecc77c207d11d2` |
| 分母 | 50 题 = 46 道内容题 + 4 道应拒答题 |
| 生成模型 | 智谱 `glm-5.2`，temperature 0.1；检索取 10 条，送模型 5 条，开启上下文补全 |

## 2. 三层结果

### 2.1 检索（本地，无模型调用）

来源 `eval/runs/diagnostic-gen-20261013-7411-staging/REPORT.md`，测的就是当前正式 collection。

| 指标 | 结果 |
| --- | --- |
| 正确证据排第 1 / 前 3 / 前 5 | 23/46 / 37/46 / 38/46 |
| 自然问法（不点名文件）前 5 | 15/16（草稿题集 `eval/golden_natural.jsonl`） |

### 2.2 生产检索 → 生成（无 HTTP 层，2026-10-13，同一 collection）

来源同上，脚本 `scripts/run_generation_diag.py`。它走生产回答路径 `build_query_services().answer`，包含生产检索、证据筛选和上下文补全，和 2.3 的差别只有 HTTP 适配器这一层，**不是**给定金证据的独立生成评估。结果：回答 / 拒答 / 出错 = 43 / 7 / 0，应拒答题拒答 3/4；该轮只统计了状态，没有逐题做事实正确性复核。

给定金证据、只测生成的评估只有 0b9a 语料上的 `diagnostic-goldgen-20261008-r1`（实际调用 8 题）。**7411 上没有做独立的生成单项评估**，生成质量目前以 2.3 端到端的语义复核为准。

### 2.3 端到端（本次新跑，2026-10-15）

入口 `POST /api/v1/queries`（进程内 ASGI，生产 HTTP 适配器与生产查询组合），运行目录 `eval/runs/diagnostic-e2e-20261015-7411/`，语义复核 `eval/audits/diagnostic_e2e_20261015_7411_agent_review.jsonl`。

**结果状态**：answered 43、refused 7；50 条 trace 全部落盘（`trace_missing=0`），无 HTTP/运行错误。

**46 道内容题：**

| 类别 | 数量 | 题号 |
| --- | ---: | --- |
| 回答且覆盖全部期望事实 | 29 | 见复核文件 |
| 回答但部分覆盖 | 12 | G004 G013 G018 G020 G023 G026 G030 G035 G036 G041 G045 G046 |
| 回答与黄金集冲突，疑为黄金集取错列 | 1 | G017（送入模型的两份表格证据和黄金集自身的人工转写中，“中心城区的重要地区”均为 5~10 年，3~5 年是“中心城区”列；未对照 PDF 页面图像，待人工裁定） |
| 拒答 | 4 | G001（点名文件，文件名压过定义条文）、G012（标准号）、G009/G049（图片内容） |

**4 道应拒答题：** 3 道状态为 refused。G015 状态为 answered，但内容明确说明 2035 年没有具体百分比，也没有把 2025 年指标套用过去，语义上符合期望，只是状态层面计为未拒答。

**引用：** 82 条主张全部带引用，101 次引用全部落在本次送入模型的证据内（全量检查见 `eval/audits/diagnostic_e2e_20261015_7411_citation_check.json`）。43 条主张标记为已核对；39 条带"未核对"标记，其中 32 条只引用了未核对证据（`source_locator_only`，即机器识别的表格、公式或扫描件），7 条引用中混有已核对证据。这是 caveat tier 的设计行为。

**延迟与费用：**

| 阶段 | p50 | p95 |
| --- | ---: | ---: |
| 端到端 wall | 10.47 s | 40.12 s |
| 检索 | 64 ms | 96 ms |
| 生成 | 10.36 s | 40.01 s |

token 输入 137,147、输出 49,621。按配置单价（输入 8 元/百万、输出 28 元/百万）估算费用 2.49 元，即每题约 0.05 元。

## 3. 历史实验（未重跑，仅引用）

| 实验 | 语料 | 结论 |
| --- | --- | --- |
| 检索基线 `diagnostic-diag-baseline-20261001` | 0b9a | micro Recall@5 54/77，@10 63/77 |
| E1 本地重排 `diagnostic-exp-e1-rerank-20261006-r2` | 0b9a | Recall@5 57/77；新增可引用证据降级，重排 p50 3.3 s，**未采纳，未上线** |
| 端到端 `diagnostic-e2e-20261008-r1` | 0b9a | answered 14 / refused 36；当时只允许已核对证据回答 |

与 10-08 相比，回答数从 14 升到 43，主要来自两点：一是允许未核对证据带标记回答（caveat tier），二是表格、公式检索上下文（corpus-7411）。两次运行的语料和黄金集版本都不同，只能做方向参考，不是严格对照。

## 4. 未验证与剩余风险

- 黄金集未冻结；G017 疑似黄金集取错列，待人工裁定；语义判定是 agent 复核，不是人工裁定。
- 39/82 条主张依赖未核对证据，用户需要点开原文页核对。
- 生成 p95 约 40 秒，长尾来自模型；限流和并发未在本次评估范围内。
- 已知失败：问题点名文件时检索偏差（G001）；图片内容题不稳定（G009、G049）；同一类题多轮运行结果可能摇摆，本次只跑一轮。

## 5. 复现

```bash
# 离线预检（不调用模型）
.venv/Scripts/python.exe scripts/run_e2e_diag.py --run-id <id> --dataset eval/golden_v3.jsonl --preflight-only
# 真实运行（需 .env 中的智谱 key，以及 LLM_INPUT_COST_PER_MILLION / LLM_OUTPUT_COST_PER_MILLION）
.venv/Scripts/python.exe scripts/run_e2e_diag.py --run-id <id> --dataset eval/golden_v3.jsonl \
  --authorized-provider zhipu --authorized-model glm-5.2 \
  --authorized-base-url https://open.bigmodel.cn/api/paas/v4/ \
  --authorized-send-scope http-e2e:50-questions:production-retrieval-top5-chunks \
  --budget-cny 8 --per-call-reserve-cny 0.5 --authorization-note "<owner approval>"
```

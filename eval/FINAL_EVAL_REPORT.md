# 评估总报告（corpus-ed28791afb8a，2026-10-24）

口径：**diagnostic，非 formal**。黄金集 50 题全部为 `reviewed`、未冻结，`--formal` 门禁保持关闭。语义判定由 agent 对照 `expected_facts` 给出，不是独立人工裁定。上一版报告（2026-10-15，corpus-7411，golden_v3）见 Git 历史。

## 1. 固定身份

| 项 | 值 |
| --- | --- |
| 正式语料 | `corpus-ed28791afb8a`，3911 chunks，`corpus_v2_live` → `corpus_v2__staging__corpus-ed28791afb8a__bi-dcd9e7217909a5b06517d419` |
| 内容核对 | 239 张表格逐格核对；210 个公式元素全部核对（119 个计算公式由负责人核对，91 个行内单位/化学式片段由 agent 对照原页核对）；扫描件正文为机器识别 |
| 黄金集 | `eval/golden_v5.jsonl`（锚定 ed28；G017 F0 本次据原页改正后 SHA-256 `079b5ce1df3e191c56025001244100e4a3c1e4563381c0f11da1ad0d1f738969`） |
| 分母 | 50 题 = 46 道内容题 + 2 道前提错误题 + 2 道应拒答题 |
| 生成模型 | 智谱 `glm-5.2`；检索取 10 条，送模型 5 条，同条款上下文补全最多 8 块 |

## 2. 结果

### 2.1 检索（本地，无模型调用）

运行目录 `eval/runs/diagnostic-retrieval-20261024-ed28/`。

| 指标 | 结果 |
| --- | --- |
| 必中证据进入前 5 / 前 10（micro） | 54/76 / 60/76 |
| 其中可正式引用的证据 前 5 / 前 10 | 49/64 / 54/64 |
| 命中正确文件（前 5，macro） | 99.5% |
| 一题的全部必中证据都进入前 5 / 前 10 | 64% / 72% |

与 913c 完全相同：本版只改了 91 个行内公式片段的文字与核对状态，没有改变检索排序。3 条证据未对齐（G013 目录页、G049 图内标注、G050 拒答题非必中证据），不计入分母。

### 2.2 端到端

入口 `POST /api/v1/queries`（进程内 ASGI，生产 HTTP 适配器与生产查询组合），运行目录 `eval/runs/diagnostic-e2e-20261024-ed28/`，语义复核 `eval/audits/diagnostic_e2e_20261024_ed28_agent_review.jsonl`。

**结果状态**：answered 46、refused 4；50 条 trace 全部落盘，无 HTTP 或运行错误。

**46 道内容题：**

| 类别 | 数量 | 题号 |
| --- | ---: | --- |
| 回答且覆盖全部期望事实 | 33 | 见复核文件 |
| 回答但部分覆盖 | 11 | G004 G013 G018 G020 G023 G026 G030 G036 G045 G046 G049 |
| 拒答 | 2 | G001（点名文件的定义题）、G009（图注题） |

G017 上次记为“疑似黄金集取错列”。本次 agent 对照 GB 55027 第 11 页表 3.3.2 原页确认：“中心城区的重要地区”为 5~10 年，3~5 年是“中心城区”列。黄金集 F0 已改正，该题回答正确。

部分覆盖的共同模式：送入模型的 5 条证据里缺了某一个事实所在的块，模型如实说“资料中未给出”，没有编造（G020、G023、G026、G036、G045、G046、G049）。

**前提错误题与拒答题：** G015、G028 都指出资料没有所问的数值，并说明资料的实际内容，没有编造；G008、G050 正确拒答。

**引用：** 119 条主张全部带引用，144 次引用全部落在本次送入模型的证据内。103 条主张只引用已核对证据；16 条带“未核对”标记（引用了扫描件正文等机器识别证据）。上一版为 43/82 条已核对。

**延迟与费用：**

| 阶段 | p50 | p95 |
| --- | ---: | ---: |
| 端到端 | 5.8 s | 26.6 s |
| 检索 | 62 ms | 95 ms |
| 生成 | 5.6 s | 26.4 s |

token 输入 125,990、输出 25,003。按配置单价（输入 8 元/百万、输出 28 元/百万）估算 1.71 元，每题约 0.03 元。

### 2.3 与上一版对比（仅作方向参考）

| 指标 | 2026-10-15（7411，golden_v3） | 2026-10-24（ed28，golden_v5） |
| --- | --- | --- |
| 内容题完整答对 / 部分 / 拒答 | 29 / 12 / 4 | 33 / 11 / 2 |
| 主张只引用已核对证据 | 43/82 | 103/119 |
| 端到端 p50 / p95 | 10.5 s / 40.1 s | 5.8 s / 26.6 s |

两次的语料、黄金集预期和回答方式（2026-10-19 起整段回答 + 句内来源编号、同条款上下文补全）都不同，不是严格对照。

## 3. 历史实验（未重跑）

| 实验 | 语料 | 结论 |
| --- | --- | --- |
| E1 本地重排 `diagnostic-exp-e1-rerank-20261006-r2` | 0b9a | Recall@5 54/77 → 57/77；新增可引用证据降级，重排 p50 3.3 s，**未采纳，未上线** |

## 4. 未验证与剩余风险

- 黄金集未冻结；语义判定是 agent 复核，不是人工裁定；只跑一轮，同类题多次运行结果可能摇摆。
- 扫描件正文仍为机器识别（已知错字见 `.claude/EVALUATION_HANDOFF.md`），引用它的结论带“未核对”标记。
- 已知失败：点名文件的定义题（G001）和图注题（G009、G049）；送模型的 5 条证据漏掉某个事实时只能部分回答。
- G013 的证据在目录页，不进检索，需改锚或换题。
- 生成 p95 约 27 秒，长尾来自模型；限流和并发不在本次范围内。

## 5. 复现

```bash
# 检索（本地）
.venv/Scripts/python.exe scripts/evaluate_golden_retrieval.py --dataset eval/golden_v5.jsonl --run-id <id>
# 端到端离线预检（不调用模型）
.venv/Scripts/python.exe scripts/run_e2e_diag.py --run-id <id> --dataset eval/golden_v5.jsonl --preflight-only
# 端到端真实运行（需 .env 中的智谱 key；单价通过环境变量传入）
LLM_INPUT_COST_PER_MILLION=8 LLM_OUTPUT_COST_PER_MILLION=28 \
.venv/Scripts/python.exe scripts/run_e2e_diag.py --run-id <id> --dataset eval/golden_v5.jsonl \
  --authorized-provider zhipu --authorized-model glm-5.2 \
  --authorized-base-url https://open.bigmodel.cn/api/paas/v4/ \
  --authorized-send-scope http-e2e:50-questions:production-retrieval-top5-chunks+same-clause-expansion-up-to-8 \
  --budget-cny 8 --per-call-reserve-cny 0.5 --authorization-note "<owner approval>"
```

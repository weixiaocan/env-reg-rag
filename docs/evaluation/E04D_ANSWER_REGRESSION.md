# E-04D M4 回答与引用回归

## 1. 评估范围

M4 回归集由已人工确认的 `golden-set-v1` 派生，但不修改 M3 检索基线。M3 评价“目标证据能否被检索”，M4 根据 `formal-corpus-v1` 的来源门控，评价最终查询结果应当回答、追问、拒答还是仅定位原文。

共执行 12 条：

- 6 条 `answered`：AI-001、AI-002、AI-003、AI-007、AI-008、AI-009；
- 4 条 `refused`：REAL-002、AI-004、AI-005、AI-006；
- 1 条 `needs_clarification`：REAL-001；
- 1 条 `search_only`：AI-010。

正式回答使用 Qdrant `corpus_current`、本地 BGE、数值区间投影、LangChain 结构化输出和智谱 `glm-5.2`。AI-010 使用实验语料中的隔离定位单元，且只返回第 19 物理页 5.2.4 的原文入口，不把自动转写公式作为答案。

## 2. 结果

最终结果为 **12/12 通过**：

- 需要条件的问题没有静默猜测；
- 六条回答均引用当前 EvidencePack 内允许引用的 evidence ID，并包含验收事实；
- 四条正式语料缺口均返回受控拒答，没有用相关但不适用的 Top-K 内容拼凑答案；
- 公式问题返回 `search_only` 和目标页定位。

首轮原始报告为 11/12。AI-008 当时被错误要求在“Ⅰ基础工作”答案中包含“排水系统接出口接驳”；复核表 3.2.3 后确认该项属于“Ⅱ建设效果”，而基础工作只有建设方案、工作记录和管网竣工数据资料。保留首轮失败报告，修正 M4 派生标签后只重跑 AI-008，结果 1/1 通过。

## 3. 可复核产物

- 零模型费用检索预检：`data/eval_results/m4-answer-preflight-v1.json`；
- 首轮真实回归：`data/eval_results/m4-answer-regression-v1.json`；
- AI-008 标签修正后复核：`data/eval_results/m4-answer-regression-ai-008-retry-v1.json`；
- M4 回归输入：`data/evaluation/m4-answer-set-v1.json`。

首轮 12 条平均耗时约 8.19 秒，最长为 AI-005 的约 49.85 秒。该长尾需要在 M5/M6 的超时、取消和性能评估中继续处理，不能仅报告平均值。

## 4. 阶段结论

M4 已证明同一个 `QueryApplicationService` 可以完成正式 Qdrant 检索、受控连续追问、证据充分性拒答、原文定位、LangChain 模型输出和引用白名单校验。当前结果支持进入 M5 网页工作台与 FastAPI，但不代表生产级准确率；语料覆盖、真实问题数量和长尾时延仍需继续扩充与评估。

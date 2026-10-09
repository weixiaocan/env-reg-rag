# RAG 评估交接：当前状态与终点

此文件是从此前会话与现有仓库产物整理的**工作线索**，不是代替现场核验的完成证明。新会话应先读 `AGENTS.md`、`CLAUDE.md`、本文件，再核对所列路径与当前 Git/运行状态。`AGENTS.md` 第 10 节的 v1 草稿数字是历史记录，不能覆盖当前 v2 黄金集。

## 已有工作（需核对哈希/trace，不无故重跑）

- 正式指针 `data/registry/corpus-current.json` 当前写明 `corpus-0b9a1fd84e3d`、4232 chunks、`corpus_v2_live`；在线集合点数仍须在需要时实测。
- `eval/golden_v2.jsonl` 当前摘要为 50 题、80 evidence、77 must-hit、46 道内容题与 4 道拒答题，SHA-256 `3b1f1cdc459aadb50bd1b39531bc90f51ecc25a0d969612d41612fa011bd6e89`。全题仍 `reviewed`，14 条事实人工裁定待决；`--formal` 应继续拒绝。不要把 `expected_answerability=locate` 解释为“PDF 内容不给模型”；它描述现行证据准入限制，不是产品目标。
- 检索诊断基线 `eval/runs/diagnostic-diag-baseline-20261001/` 已有 50 题逐题 trace，报告 micro Recall@5 = 54/77、@10 = 63/77，另有 MRR、文档/页命中、策略分层与延迟。先对齐 config 中的数据集 SHA 再作跨实验比较；不把数字称作正式成绩。
- E1 本地重排诊断实验 `eval/runs/diagnostic-exp-e1-rerank-20261006-r2/E1_EXPERIMENT_REPORT.md`：两轮报告 Recall@5 57/77、@10 67/77，但新增可引用证据降级，CPU 重排 p50 约 3.2 秒，未过采纳门槛，**未接入在线路径**。不要重新下载模型或将该实验改写成已上线优化。
- S1 可回答性差距 `eval/audits/s1_answerability/` 已完成只读审计；生产回答路径把 `answer_and_citation` 下推过滤，生成上下文限前 5 条。S1 的 11/46 是检索级目标承载口径，不是生成效果。
- S2 `eval/audits/s2_target_evidence/` 已有逐题/受阻证据/PDF 回查/排名探针。当前 JSON 报完整可达 11/46、部分 0、不可达 35；这些只是 answer-space 证据可达性，**不是 50 题真实生成结果**。首要核验点：`s2_summary.md` 写“trace 真实失配 0”，但当前 `s2_summary.json` 的 `real_mismatch_touching_bound_carrier` 列出 G045[0]。必须查逐题 trace、生成脚本与测试，确认是报告陈旧还是实际漂移，修正后再让审核 agent 判定 S2。不要因现有测试通过就默认语义通过。

## 本任务的完成条件

1. S2 代码、逐题记录、JSON/Markdown 汇总一致；实测与推断分开；独立审核 PASS。
2. 同一 50 题有真实生成诊断运行（若使用外部 LLM，先获明确提供方与片段发送授权），逐题保存提示输入身份、允许的 evidence ID、结构化响应、主张-引用支持、拒答/追问/冲突判定及失败原因；给出可复现命令和汇总指标。仅假模型测试不算真实生成质量完成。
3. 同一 50 题有真实端到端诊断运行，逐题 trace 可从请求追到检索 hit、生成上下文、响应与引用；报告端到端结果、p50/p95 延迟、可得的 token/费用及失败分类。不能把单项生成结果改名为端到端结果。
4. 检索基线与 E1 作为已完成历史实验被正确引用；所有指标注明分母、数据集 SHA、语料版本、运行配置、diagnostic/formal 状态。最终有一份简洁综合评估报告，说明什么实测、什么未验证、哪些问题被策略安全隔离。
5. `--formal` 仍受 14 条人工裁定与全题 frozen 门禁约束；本任务不代填裁定、不自动冻结，也不为了漂亮数字改变正式索引/证据 policy。若外部 LLM 授权缺失，完成所有本地安全工作后记录准确授权阻断，不能把第 2/3 条说成已完成。

## 执行节奏

主 Claude 会话逐里程碑执行，调用 `rag-eval-reviewer` 对每个**完整里程碑**做一次独立只读审核，集中修复阻断项并复审；PASS 后继续下一项。只有法律/授权或不可替代的人类事实裁定才向负责人提问。不要让负责人再逐轮复制报告，也不要重新打开 PDF 提取、发布或简历工作。

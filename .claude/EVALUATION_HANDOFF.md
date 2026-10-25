# 当前状态与待办（2026-10-24）

此文件是工作线索，不是完成证明；开始工作前核对所列路径与当前 Git/运行状态。

## 当前状态

- 本地正式语料 `corpus-ed28791afb8a`（2026-10-24 发布）：21 份资料、3911 个检索块，查询入口 `corpus_v2_live`。回退版本 `corpus-913c134c18ae`（`data/registry/rollback` 有回退记录）。`corpus-9097cecb12d5` 仍在本地、服务器和 GitHub Release，按“只保留当前版和一个回退版”应删除，待负责人确认。
- 服务器与 GitHub Release 仍是 913c；ed28 尚未部署、未提交、未发布 Release。
- 表格：239 张全部逐格核对后为 `passed`，可正式引用。核对记录 `data/registry/table-checks.json`（按单元格哈希绑定；方法：PDF 文字层、两路独立 OCR、智谱 GLM-4.5V 读表、agent 对照原页截图），工具 `scripts/check_tables.py`、`scripts/vision_table_check.py`、`scripts/vision_table_full.py`。
- 公式：210 个公式元素全部为 `passed`，记录在 `data/registry/source-corrections.json` 的 `formulas` 与 `reviews`。
  - 119 个计算公式经负责人核对（2026-10-20 更正 35 个，2026-10-22 确认其余 84 个及 92 行“式中”说明，2026-10-23 又核对公式块内 185 行文字）。
  - 91 个行内单位/化学式/数值区间片段由 agent 对照原页核对（2026-10-24，负责人指示），30 个更正、61 个确认；条目带 `verified_by: agent`，装配时记为 `agent_verified_correction`，不冒充负责人核对。
  - 识别文字变化后要用原页位置重新绑定（2026-10-23 重新绑定过 57 条）。
- 扫描件正文：三路识别投票（`OCR_VOTE_MODE = three-source-region-vote-v4`），机器识别，引用时带“未核对”标记。agent 对照原页抽查 120 段 1 处错（前言人员名单）。已知仍错（三路都读错）："10m³"读成 m²（T/CECS 1764 第 5.1.4 条）、"平均日流量"读成"平均H流量"（GB 55027 第 4.3.3 条）、GB 50014 第 4.1.4 条第 3 款缺款号、《城镇内涝防治技术规范》第 4.2.2 条"排入下游"读成"排人不游"；约 9 行"式中"变量说明缺变量名。
- 部署流程：`docker build -t env-reg-rag-app:<短版本> .` → `bash deploy/make_bundle.sh`（`APP_IMAGE` 默认值随版本改）→ 上传 `dist/deploy-<语料>` → 服务器改 `/opt/drainage-rag/.env` 的 `APP_IMAGE_NAME` → 运行 `server_install.sh /opt/drainage-rag`。线上地址 https://weixiaocan.com/rag/ （服务器 `drainage-demo`）。
- GitHub Release：数据有改动就用 `deploy/make_release_data.sh` 重新打整包发布 `corpus-<版本>`，只保留当前版和一个回退版。现有 `corpus-913c134c18ae`（Latest）、`corpus-9097cecb12d5`。

## 评估（2026-10-24 完成）

- 黄金集 `eval/golden_v5.jsonl`：50 题（46 内容题 + 2 前提错误题 + 2 拒答题），锚定 ed28，`status=reviewed`，只能 diagnostic。变更说明见 `eval/golden_v5.summary.md`。G017 F0 据原页改为 5~10 年。
- 检索 `eval/runs/diagnostic-retrieval-20261024-ed28`：必中证据前 5 命中 54/76、前 10 命中 60/76（与 913c 相同）。
- 端到端 `eval/runs/diagnostic-e2e-20261024-ed28`，复核 `eval/audits/diagnostic_e2e_20261024_ed28_agent_review.jsonl`：内容题 33 完整 / 11 部分 / 2 拒答；前提错误题与拒答题 4/4；119 条主张引用全部落在送模型证据内，16 条带“未核对”；p50 5.8 s；约 1.71 元。报告 `eval/FINAL_EVAL_REPORT.md`，README“评估结果”已更新。
- 3 条证据未对齐：G050 是拒答题的非必中证据；G013 证据在目录页（不进检索），G049 第 2 条是图内标注（没有进入索引）。
- 运行器：`scripts/run_generation_diag.py`、`scripts/run_e2e_diag.py`（真实运行要用环境变量传 `LLM_INPUT_COST_PER_MILLION=8`、`LLM_OUTPUT_COST_PER_MILLION=28`）、`scripts/evaluate_golden_retrieval.py`。新语料发布后用 `scripts/reanchor_golden.py` 重新对齐黄金集。

## 待负责人决定

- 部署 ed28 到服务器、提交推送、发布 Release `corpus-ed28791afb8a`。
- 删除 9097（本地 canonical/collection/manifest/回退记录、服务器 collection 与 `env-reg-rag-app:9097` 镜像、GitHub Release `corpus-9097cecb12d5`），保留 913c 作回退。
- G013 改锚到正文条款或换题。

不做：智谱 GLM-4.5V 第四路正文识别（负责人 2026-10-24 决定）。

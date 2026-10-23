# 当前状态与待办（2026-10-23）

此文件是工作线索，不是完成证明；开始工作前核对所列路径与当前 Git/运行状态。

## 当前状态

- 正式语料 `corpus-913c134c18ae`：21 份资料、3911 个检索块，查询入口 `corpus_v2_live`。上一版 `corpus-9097cecb12d5` 保留为回退（`data/registry` 有回退记录），确认新版本线上无误后再删。
- 表格：239 张全部逐格核对后为 `passed`，可正式引用。核对记录 `data/registry/table-checks.json`（按单元格哈希绑定；方法：PDF 文字层、两路独立 OCR、智谱 GLM-4.5V 读表、agent 对照原页截图），工具 `scripts/check_tables.py`、`scripts/vision_table_check.py`、`scripts/vision_table_full.py`。
- 扫描件正文：三路识别投票（`OCR_VOTE_MODE = three-source-region-vote-v4`）。2026-10-23 修正：仅原图识别读到的行内数字/单位符号保留；含行内公式的正文行不再整行丢弃。agent 对照原页抽查 120 段 1 处错（前言人员名单）。已知仍错（三路都读错）："10m³"读成 m²（T/CECS 1764 第 5.1.4 条）、"平均日流量"读成"平均H流量"（GB 55027 第 4.3.3 条）、GB 50014 第 4.1.4 条第 3 款缺款号、《城镇内涝防治技术规范》第 4.2.2 条"排入下游"读成"排人不游"；约 9 行"式中"变量说明缺变量名。
- 公式：负责人核对过的更正在 `data/registry/source-corrections.json`（`owner_verified=true`）。识别文字变化后用原页位置重新绑定（2026-10-23 重新绑定 57 条，正文不变）。其余公式只经过 AI 对照原页检查，仍为 `needs_review`。
- 部署流程：`docker build -t env-reg-rag-app:913c .` → `bash deploy/make_bundle.sh` → 上传 `dist/deploy-<语料>` → 服务器运行 `server_install.sh /opt/drainage-rag`。服务器 `.env` 的 `APP_IMAGE_NAME` 需与镜像一致。线上地址 https://weixiaocan.com/rag/ （服务器 `drainage-demo`）。

## 评估

- 现行黄金集 `eval/golden_v5.jsonl`：50 题（46 内容题 + 4 拒答题），锚定 913c，`status=reviewed`，只能 diagnostic。变更说明见 `eval/golden_v5.summary.md`。必中证据检索：前 5 命中 56/76、前 10 命中 60/76（9097 为 54/57）。
- 3 条证据未对齐（`reanchor_unresolved`）：G050 是拒答题的非必中证据；G013 证据在目录页（不进检索），G049 第 2 条是图内标注（没有进入索引）。
- 负责人已授权的 50 题诊断运行：向智谱 GLM 发送每题检索前 5 条 + 最多 8 块条款补全。**等负责人完成手工测试后再跑**。跑完后用新结果替换 README"评估结果"一节的旧数字（现为 2026-10-15、golden_v3、7411 的结果）。
- 运行器：`scripts/run_generation_diag.py`（生成层）、`scripts/run_e2e_diag.py`（HTTP 端到端，`--preflight-only` 可离线预检）、`scripts/evaluate_golden_retrieval.py`（检索）。新语料发布后用 `scripts/reanchor_golden.py` 重新对齐黄金集。

## 待负责人决定

- 是否用智谱 GLM-4.5V 对三路识别有分歧的正文行做第四路识别（估计 ¥20–50），进一步降低扫描件正文错字。
- 删除中间候选版本（3bd3、576f、2571、c742、386d、f8c6、2469、f712、629e、d1221、665f、7a843、8e13 及其 staging collection）和确认后删除 9097 回退。

- 是否核对剩余 84 个公式和 19 个公式块的变量说明（可做核对页面）。
- G013 是否改锚到正文条款或换题。

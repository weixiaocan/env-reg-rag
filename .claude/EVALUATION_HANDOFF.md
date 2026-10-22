# 当前状态与待办（2026-10-22）

此文件是工作线索，不是完成证明；开始工作前核对所列路径与当前 Git/运行状态。

## 当前状态

- 正式语料 `corpus-9097cecb12d5`：21 份资料、3885 个检索块，查询入口 `corpus_v2_live`。这是唯一保留的版本，**没有回退版本**；下次发布前先确认新版本可用再删旧版本。
- 在 7411 基础上应用负责人逐条核对过的 35 个公式更正和 2 行变量说明（`data/registry/source-corrections.json`，`owner_verified=true`），这些元素为 `passed`。
- 公式状态：119 个含"="的公式中 35 个已核对；其余 84 个只经过 AI 对照原页的检查（`eval/formula_audit_corpus-7411.md`），负责人未核对，仍为 `needs_review`；另有 91 个公式片段（单位、拆开的部分）。已核对的 35 个中，16 个所在分块可正式引用，19 个因同块的"式中"变量说明未核对仍带"未核对"提示。
- 已部署：https://weixiaocan.com/rag/ （服务器 `drainage-demo`，镜像 `env-reg-rag-app:9097`）。代码已推送 GitHub `weixiaocan/env-reg-rag`；Release `corpus-9097cecb12d5` 附资料包（含原始 PDF，标注仅供学习研究）、Qdrant 快照和校验和。
- 部署流程：`docker build -t env-reg-rag-app:9097 .` → `bash deploy/make_bundle.sh` → 上传 `dist/deploy-<语料>` → 服务器运行 `server_install.sh /opt/drainage-rag`。服务器 `.env` 的 `APP_IMAGE_NAME` 需与镜像一致。

## 评估

- 现行黄金集 `eval/golden_v5.jsonl`：50 题（46 内容题 + 4 拒答题），锚定 9097，`status=reviewed`，只能 diagnostic。变更说明见 `eval/golden_v5.summary.md`。
- 6 条证据未对齐（`reanchor_unresolved`）：G008、G050 是拒答题的非必中证据；G012、G029 证据在封面，G013 在目录页（封面和目录按 `front_matter`/`toc_navigation_only` 不进检索），可改锚到正文条款；G049 第 2 条是图内标注，没有进入索引。
- 负责人已授权的 50 题诊断运行：向智谱 GLM 发送每题检索前 5 条 + 最多 8 块条款补全。**等负责人完成手工测试后再跑**。跑完后用新结果替换 README"评估结果"一节的旧数字（现为 2026-10-15、golden_v3、7411 的结果）。
- 运行器：`scripts/run_generation_diag.py`（生成层）、`scripts/run_e2e_diag.py`（HTTP 端到端，`--preflight-only` 可离线预检）、`scripts/evaluate_golden_retrieval.py`（检索）。新语料发布后用 `scripts/reanchor_golden.py` 重新对齐黄金集。

## 待负责人决定

- 是否核对剩余 84 个公式和 19 个公式块的变量说明（可做核对页面）。
- G012、G013、G029 是否改锚到正文条款或换题。

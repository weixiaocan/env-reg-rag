# R2b 中期审核（Claude，21:25；Codex 额度中断，未提交）

## 方向认可
- 在 `native_pdf.py` 以"单图覆盖 ≥80% + 仅不可见文字层（render mode 3）"判定 `scanned_with_text_layer`：判据合理（比审核者的初版更严：要求文字不可见）。

## 约束（必须满足）
1. **不得触发全量重新提取**：`auto-page-v9 → v10` 的配置号变更会使既有 page-intermediate 缓存失效，后续统一入口将重跑全部页面（含 PaddleOCR）。要求：
   - v3 组装阶段对**既有** page-intermediate 应用同一判定（读取源 PDF 判定页面，不需要重新提取），这是本轮 rollout 与后续发布候选的路径；
   - 提取阶段的规则保留给未来新增 PDF；若必须改 config id，需证明只影响命中页（或以独立的页级标记实现），并在报告中写明对既有缓存的影响与成本估计。
2. 命中页的 pp_raw / pp_clean：在本地对 135 页运行（`PYTHONPATH=<repo>`），记录 run id；三源投票中嵌入文字层作为 `primary`。
3. 其余按 `tasks/R2b.md` 完成：条文连续性口径、全量 rollout 重跑、金标准与指标前/后、完整 pytest、报告、提交。

## 说明
- 工作区中 `data/quality/**` 的删除来自审核者授权清理，已单独提交 48b27ec，与本卡无关。
- R2b 完成后立刻执行 `tasks/R2c.md`（重复拼接 59 处、原生条号拆散、附录标题、红章关键字符），不要合并到 R2b。

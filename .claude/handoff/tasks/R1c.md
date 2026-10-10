# R1c 中间层 v3：OCR 文字层改为"预处理 + region 取行"，替换逐行融合

## 必读
`.claude/handoff/MIDDLE_LAYER_V3_SPEC.md`、`.claude/handoff/reviews/R1.md`（审核者逐页发现与原型验证）、`PROTOCOL.md`。

## 范围（只改 OCR 页的文字来源；结构层保持 R1 实现）
1. **OCR 页面图像预处理 + 重新识别（本地、已有模型，不下载）**：
   - 新步骤：渲染 OCR 路由页（与既有独立 OCR 相同缩放），去除浅灰水印（灰度阈值，默认 120，配置化）与红色印章（颜色阈值，配置化），再用现有 RapidOCR + PP-OCRv6 模型（`data/model_runtime/independent_ocr/rapidocr-3.9.2-ppocrv6`）识别；输出新的不可变 run（复用 `run_independent_ocr_verification.py` 的 run/目录/哈希机制，或在其中加预处理参数；run manifest 记录预处理参数）。
   - 先在 GB 55027 上跑；提供对 10 份 OCR 文档全部 OCR 页运行的命令（R2 用），本卡不必全跑。
   - **阈值校准**：在 GB 55027 与 GB/T 28592（红章）、GB50268（复印灰底）各取 2 页，对比 无预处理 / 阈值 150 / 120 / 100 的识别结果，报告每页漏行与误删（正文被抹掉）情况，选定默认值并写明依据。
2. **region 文本组装（替换 R1 中的逐行融合，在 v3 路径里不再调用 `independent_ocr_fusion` 的 replace/insert）**：
   - region 文本 = 中心点落在 region bbox（含小容差）内的预处理后 PP-OCRv6 行，按阅读顺序排列；
   - 主 OCR 行若与任何 PP 行在纵向不重叠，作为兜底保留（标记来源）；
   - 噪声过滤：整行命中水印配置清单、单字低分行；
   - 不在任何 region 内的 PP 行：若非噪声，按几何就近生成独立段落（记录统计）；
   - PP 行首缺失条文编号而同位置主 OCR 行有编号时，保留编号（例：主 `4 完善管网体系` vs PP `完善管网体系`）。
   - 每个元素 provenance 记录：文字来源（pp_clean / primary_fallback）、run_id、预处理参数。
3. **条文编号连续性检查**（新指标，进入 metrics 与验收）：同一节内 `N.N.k` 序号的缺口、重复、非法跳号（如 `3.45`）逐一列出。
4. 重跑 GB 55027 试点产物（chunks.jsonl / page_review.md / metrics.json，覆盖旧文件），并给出 R1 审核列出的全部问题点逐项前/后（1.0.1、2.1.7、2.2.10、2.2.11、2.2.13、2.3.3、2.3.5、2.3.6、2.1.8 `IZ`、编号空缺清单）。
5. 测试：region 取行、主 OCR 兜底、噪声过滤、编号保留、预处理（合成图：灰色水印字被去除、黑字保留）、编号连续性检查。完整 pytest。

## 禁止
- 不改结构层逻辑（除非为上述接口必要）、不改 chunker/准入/ID 规则/发布；不 ingest/publish；不改 `data/registry/`；不调用外部 LLM；不下载模型。

## 交付
`.claude/handoff/reports/R1c.md`；提交一次（英文，末尾 `Co-Authored-By: Codex <noreply@openai.com>`），连同 `.claude/handoff/` 改动。

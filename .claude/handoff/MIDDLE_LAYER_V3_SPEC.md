# 中间层 v3 重写方案（PDF 页面中间结果 → Canonical V2）

负责人决定：重写中间层，不再缝补。Claude 出方案、管验收；Codex 执行。

## 1. 为什么重写
PP-StructureV3 已给出**完整段落级版面块**（regions）及类型标签：全库 page-intermediate 中 98.5% 的行元素完整包含在 region 文本里；标签含 `text`、`paragraph_title`、`doc_title`、`number`（页码，1,044 处）、`header`/`footer`、`content`（目录）、`table`、`formula`、`formula_number`、`figure_title`、`image`、`reference*`。

旧 `canonical_assembly.py`（约 4,000 行）丢弃这些标签，以**行**为单位重新推断分栏、阅读顺序、标题、段落、页眉页脚。T3–T6d 的问题几乎全部来自这层再推断，且相互牵连（例：T6c 段落重建使页码进入正文 909→1870；T3/T5 后分项被当作章标题 8→41）。

## 2. 不变的部分
- 输出仍是 **Canonical V2**（`validate_document` 通过、`compute_ids` 计算 ID、provenance/source_spans/links 完整）。
- 下游 `v2_chunker`、`evidence_policy`、检索、生成、发布门禁、回滚**不改**。
- 表格/公式/图片的忠实组装（T03–T05、T09 公式解释绑定）**复用**，不重写。
- 统一入口不变：`build_corpus.py` → `assemble_corpus_v2.py` → `ingest_corpus_v2.py`。

## 3. 新流程（每页，按顺序，每步单一职责）
1. **单元 = region**。无 region 的页（全库 OCR 页 27 页）走兜底：行元素按几何聚成段落，并在统计中单列。
2. **标签映射**（固定表，配置化）：
   | PP 标签 | Canonical |
   |---|---|
   | `text`、`content` 以外的正文类 | 段落（paragraph），条文编号起始时为 clause |
   | `paragraph_title`、`doc_title` | 标题候选 |
   | `number`、`header`、`footer`、`aside_text` | **页面家具**（不进正文、不进 chunk） |
   | `content` | **目录**（role=toc，准入 locator-only，reason `toc_navigation_only`） |
   | `table`/`formula`/`formula_number`/`figure_title`/`image`/`chart` | 结构化元素，交给既有组装 |
   | `reference*` | 参考文献段落 |
3. **页面家具兜底规则**（`src/ingestion/page_furniture.py`，单一模块）：PP 标签之外，顶/底带内页码模式（`· 7 ·`、`- 12 -`、纯数字、`第N页`）、跨页复现版眉、配置文件中的水印文字清单（仅整块/整行命中）。**只标记不删除**，记录命中规则 id。
4. **OCR 页文字纠正**：region 内按行用 PP-OCRv6 纠正/补行，复用 `independent_ocr_fusion.py` 的 `t5-calibrated-v1` 规则；补入行必须落在该 region bbox 内。原生页不做。
5. **阅读顺序**：region 级排序；复用 T4 修正后的列检测（真实列缝判据）；同栏内自上而下。
6. **条文切分**：一个 text region 内若含多个条文起点（行首 `N.N.N`、`N.N` 且序号与上下文连续），在起点处切成多个 clause 元素；以数字开头的续行（如 `5.6.2、5.6.5 和 5.7.1 条的规定`）不切。
7. **标题层级**：仅 `paragraph_title`/`doc_title` 与条文编号可成为 section 节点。层级由编号决定（`N`→章、`N.N`→节、`N.N.N`→条；`第N章`；`一、`/`（一）`；`Ⅰ/Ⅱ` 小节挂在所属节下）。**`text` 标签的块永不成为章/节标题**；标题文本只取标题行本身（≤ 60 字），不得收纳正文。编号前缀一致性：`5.10.7` 只能挂在 `5.10`/`5` 下。
8. **输出**：组装 Canonical 元素；quality_status / usage_policy 继续走 `evidence_policy`（OCR 文本仍 needs_review，准入由验收后的文档级决定）。

## 4. 过渡与清理
- `assemble_corpus_v2.py` 增加 `--middle-layer v3`（默认仍 legacy），验收通过后切默认、删除被替代的旧行级路径（T6c 段落重建、旧标题推断、旧 header/footer 分散判断），保证每件事只有一个事实来源。
- 不改 ID 计算规则；chunk ID 变化属预期，黄金集按既有工具重新锚定。

## 5. 分步与验收（Claude 负责验收）
| 步骤 | 执行 | 验收门槛 |
|---|---|---|
| R0 考卷 | Claude | 金标准自动考试脚本 + 现有版本基线分 |
| R1 试点 | Codex | **只做 GB 55027-2022（20 页）**：v3 产出 + 分块；考试该文档 4 题全过；Claude 逐页对照原图审全部 chunk；结构指标达标 |
| R2 推广 | Codex | 23 份文档；Canonical 全部通过校验；完整 pytest |
| R3 验收 | Claude | 见第 6 节 |
| R4 发布评估 | — | 负责人授权发布与 GLM 评估 |

## 6. 验收标准（R2/R3）
- **A1 金标准考试**（`data/registry/m2-anchor-review-v0.json`，30 题）：
  - 条文 20 题：原文逐字（规范化空白/标点后）出现在该页（±1 页）某个 chunk 正文中，且该 chunk 的 section_path 末节点编号与锚点 `structure_path` 末节点一致。目标 **20/20**；若原图文字在上游 OCR 与 PP-OCRv6 中均不存在，可豁免，须逐题举证。
  - 表格 10 题：真值中的全部数值出现在该页表格 chunk 中；**不得低于 legacy 基线**。
- **A2 结构指标**（23 文档，全部自动统计，前/后对比）：正文中的页码/页脚/水印行 = 0（剩余逐例说明）；以句末标点结尾的标题 = 0；正文含 ` > ` 面包屑 = 0；section 标题 > 60 字 = 0；分项型根节点 = 0；正文 < 15 字的碎片 chunk 比例报告且不高于 legacy。
- **A3 原图抽检**（Claude）：每份文档随机 5 个 chunk 对照原图；严重错误（数字/条款号/漏行/错序/串条）为 0 的文档放开 OCR 正文准入；否则保持 locator-only，不再追加修复轮次。
- **A4** Canonical 23/23 校验通过；完整 pytest 全绿；不改发布指针。

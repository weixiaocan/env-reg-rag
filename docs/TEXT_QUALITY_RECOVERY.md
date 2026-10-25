# 语料文字层乱码盘点与恢复机制（草案）

> **历史文档（2026-09-24），只作记录，不代表当前状态。** 乱码门禁与坏页 OCR 恢复已落地；文中语料 069284 早已替换。当前状态见 `README.md` 与 `.claude/EVALUATION_HANDOFF.md`。

> 扫描语料：`corpus-069284e6d60e`（23 文档 / 1144 页）
> 报告产物：`data/observability/garble_scan_corpus-069284e6d60e.json`
> 启发式：按 canonical element 聚合到 `physical_page`，用汉字比例 / 符号比例 / 替换字符 / blank 页判定。

## 1. 结论（先回答「是不是全部 PDF 都废了」）

**不是。** 当前发布语料不是「整库解析不可用」，而是：

| 判定 | 文档数 | 含义 |
|------|--------|------|
| readable | 6 | 页级几乎无坏页 |
| mostly_readable | 12 | 坏页 ≤15% |
| mixed | 4 | 部分页有问题 |
| garbled_text | **1** | 正文近似全乱码 |

- 页级：坏页 **68 / 1144 ≈ 5.9%**
- **唯一全文级翻车**：`QXT-489-2019-official.pdf`（QX/T 489-2019）12/12 页坏，整体汉字比例 ≈ 0，样例：`!"# !"# !$! ... '()*+,-./0123`
- 多数国标/行标（如 GB 55027、GB 50268、T/CECS 758、十四五文件等）canonical 正文可读，**不能**从 QXT 一例外推「全部 PDF 都不能用」

但必须承认另一半事实：

- **P3 全库质量放行已明确延期**（见 `docs/SCOPE_AND_DEFERRED.md`）
- inventory 上大量 `source_review=needs_review`，region/`publishable` 未接入检索门禁
- 因此现状是：**演示链路可跑 ≠ 已通过内容质量审核**。坏页（尤其 QXT）已经进了分块/Qdrant，评估/问答会踩雷

## 2. 乱码盘点（文档清单）

### 2.1 优先处理

1. **QXT-489-2019-official.pdf** — `garbled_text`，12/12，汉字比 0，符号型乱码（疑似错误文字层/编码映射，不是「没字」）
2. **GBT+47884-2026.pdf** — mixed 7/19，部分低汉字页（目录点线等，需人工区分假阳性）
3. **短时气象服务降雨量等级TCMSA-2019.pdf** — mixed 2/7，含 mojibake 样例
4. **降雨过程强度等级标准.pdf** — mixed 2/12，空页/稀疏
5. **Chongqing-rain-sewage-guide-official.pdf** — mixed 6/39，含英文目录页（可能是假阳性）

### 2.2 坏页原因粗分（页级）

- `mojibake_symbol` 20：符号/标点冒充正文（QXT 主型）
- `blank_page` 24：parse_status=blank
- `low_han` 16：有字但汉字极少（目录/英文/误识）
- `empty` / `too_short` 8：几乎无正文

### 2.3 根因分类（工程判断）

| 类型 | 典型 | 说明 |
|------|------|------|
| A. 错误文字层 / 编码映射 | QXT-489 | PDF 有「字」但映射成 `!"#$`；`text_coverage=0.667` 说明审计以为有字，实则不可读 |
| B. 无文字层扫描件需 OCR | inventory `full_ocr_review` + cov=0 | 注意：若干 cov=0 文档 canonical 已可读（OCR/其它路径已跑过），inventory 路由是候选不是最终状态 |
| C. 版面噪声假阳性 | 目录点线、英文 Contents | 需白名单/角色过滤，避免误杀 |
| D. 质量门禁未拦住 | 全库 | P3 延期 + needs_review 未挡检索，坏文照样 ingest |

## 3. 恢复机制（分层，可落地）

目标：**检测 → 修复 → 复核 → 重切块入库 → 回归**；隔离只是中间态，不是终点。

### L0 检测（每次 assemble 前/后）

- 脚本固化：`scripts/scan_canonical_text_quality.py`（本轮逻辑搬入）
- 产出：`data/observability/garble_scan_<corpus>.json`
- 触发：
  - **硬失败**：文档 `verdict=garbled_text` 或 `overall_han_ratio < 0.05` 且页数≥3 → 禁止进入 `answer_and_citation`
  - **告警**：`mixed` / 坏页率 >15% → 进复核队列，仍可 ingest 但打 `usage_policy` 降级或页级标记

### L1 自动恢复

1. **丢弃不可信文字层，强制整页 OCR**（换引擎/更高 DPI）——针对类型 A/B
2. **换源 PDF**：优先官方文本版（QXT 应找气象出版社/标准全文文本层 PDF）
3. **清页缓存后重跑** page→assemble，避免旧错误缓存

### L2 人工复核队列

- 输入：L0 告警页 + 前后文截图
- 动作：接受 OCR / 拒收 / 标注「仅溯源不可答」
- 验收：抽检页汉字比 ≥0.5（正文页），关键条款可被 must_hit 命中

### L3 修复后入库

- 仅对修复文档跑 assemble → chunk →（当前规模）**整库重建 ingest**（P7 增量仍延期；文档少时可接受）
- 回归：黄金集中该文档题从「避开」改回 `must_hit`；跑 `garble_scan` 该文档 bad_pages=0

### 与现有门禁衔接

- 不恢复「伪造 passed」；用 **可解释的 text_quality_verdict** 替代未接线的 region `publishable`
- `source_lookup` 可仍展示坐标；`answer` 过滤 `usage_policy != answer_and_citation` 的乱码页块
- 评估 Harness：读 scan 报告 + trace，坏页题不进主指标，修复后自动回流

## 4. 本周短期动作

1. **今天**：冻结 QXT-489 不作为 answer 证据（usage_policy 降级或从 answer 过滤），并记入 scan 报告（已完成盘点）
2. **本周优先修**：QXT-489（换官方文本 PDF 或强制 OCR 重解析 12 页）
3. **次优**：人工过一眼 mixed 四份，剔除目录假阳性，真乱码页进 OCR
4. **工程**：把 scan 脚本入库 + assemble 对 `garbled_text` 硬失败（小 PR）

### 需用户拍板

- A1：QXT 修复前，是 **降级不可答** 还是 **整份暂时移出检索集合**？
- A2：QXT 优先 **重下官方文本版** 还是 **对现 PDF 强制 OCR**？
- A3：assemble 对 `garbled_text` 是否立刻 **硬失败挡发布**（推荐是）？

## 5. 给评估侧

- 坏页清单见 JSON；QXT 全页暂不可作 must_hit
- 任一页修复后会通知：`文档名 + 页码`，请改回 must_hit


## 6. QXT-489 根因（已核实）

- PDF 内嵌字体为 `DLF-*` / `FzBookMaker*` 等，**ToUnicode / 编码映射损坏**：PyMuPDF 原生抽取得到 `!"#$%&'()*+` 一类符号，汉字数为 0。
- 偶数页常为空白文字层（0 字符）；奇数页有「假文字层」。
- 现网 `_native_text_quality` 只检查：空文本、`U+FFFD`、`/G\d+` glyph marker。**符号型乱码字符数 ≥ 20 会被判 pass**，于是 `corpus_update` 走 native 批准，**从不触发 OCR**。
- inventory 备注写了「文字层覆盖不完整；需按页确认」，但 assemble/ingest 未把它变成硬门禁。
- 验证：对渲染页跑项目内 `PaddleTextOcrEngine`，封面即可识别出真实标准元数据（如 `ICS 07.060`），说明**视觉层可读，应强制 OCR 恢复**，而不是降级了事。

## 7. P3 提上日程：怎么做（可执行切片）

目标：评估与问答建立在「可读且可引用」的文字上；坏页可修复回流，而不是永久隔离。

### 阶段 P3-a（本周，最小可合并）— 可读性门禁 + QXT 恢复

1. **加检测**：`non_whitespace` 之外增加汉字比 / 符号比 / mojibake 规则（与 `garble_scan` 对齐）；native `pass` 必须「像中文标准正文」。
2. **改路由**：native 质检失败 → 强制 OCR（已有 `OcrPdfParser` / Paddle 路径）；OCR 仍失败 → 页级 `quarantine` + 进复核清单（临时）。
3. **修 QXT-489**：对该 PDF 整份 `full_ocr` 重解析 → assemble → 重建 ingest；扫 `garble_scan` 至 bad_pages≈0。
4. **验收**：QXT 抽样页可摘录条款；评估侧对应题改回 must_hit。

### 阶段 P3-b（随后）— 发布门禁接线

1. assemble/publish 前跑全库 `garble_scan`；`garbled_text` **硬失败**不得发布。
2. answer 检索过滤不可答页块；source_lookup 可保留坐标便于人工对照。
3. 产物：每版 corpus 附带 `garble_scan_*.json` 作为质量附件。

### 阶段 P3-c（再后）— 复核队列产品化

- 人工只审 mixed / OCR 低置信页；不做全库逐页。
- 与公式 triage 类似：高风险子集人审，金标准回归守住主路径。

### 明确不做（仍延期）

- 全库人工逐条阅读
- P6 效力核验、P7 增量 upsert（本轮不绑）

## 8. 落地状态（2026-09-24）

已实现（代码）：

- 共享 `src/ingestion/text_quality.py`（汉字比/符号乱码/空页判定）
- native 质检委托该模块；`AutomaticPageExtractor` config 升至 `auto-page-v7:...:native-quality=mojibake-v1`（文字阶段缓存失效后重抽）
- OCR 通过后若仍乱码则 quarantine
- `scripts/assemble_corpus_v2.py` 对 `garbled_text` **硬失败**（可用 `--allow-garbled` 仅诊断）
- 分块：不可答文本 `usage_policy=source_locator_only`（answer 路径本就只取 `answer_and_citation`）
- `scripts/scan_canonical_text_quality.py` 写出 `garble_scan_*.json` + mixed/garbled 复核清单

验收中：用新 config 重建语料 → 重点看 QXT-489 是否不再 `garbled_text`。

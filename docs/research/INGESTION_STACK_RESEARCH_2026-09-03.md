# 中文规范/政策 PDF 文档接入技术方案调研

## 1. 文档控制

- 调研日期：2026-09-03
- 适用项目：排水规范证据助手
- 来源边界：只使用截至调研日可核验的一手来源，包括项目官方文档、官方 GitHub 仓库、官方许可证和官方模型卡。
- 结论性质：本文是技术选型输入，不是最终架构决策，也不提供法律意见。
- 准确率边界：本文不引用厂商宣传数字来推断本项目效果，不编造解析准确率。最终选择必须由本项目语料 POC 决定。

本文用以下标签区分证据等级：

- **事实**：可由所附一手来源直接核验。
- **项目推断**：根据事实与本项目需求得出的设计判断，必须通过 POC 验证。
- **待验证**：官方资料不能回答，或回答不足以覆盖本项目中文规范语料。

## 2. 结论摘要

### 2.1 推荐结论

**项目决定：首期采用可审计的原生解析/OCR 双路径，不让全部 PDF 无条件经过 OCR：**

1. **PyMuPDF/PyMuPDF4LLM 作为文字层预检和原生解析主路径**：保留 page、block/word、bbox 和 JSON/page chunks；即使存在文字层，也必须经过乱码、阅读顺序、表格和定位质量检查。
2. **PaddleOCR PP-StructureV3 作为扫描件与复杂中文表格的专业降级路径**：文字层缺失、不可读、局部扫描或关键结构未通过质量门控时，按页面或区域启用 OCR。
3. **Docling 作为结构恢复对照组**：比较其标题层级、阅读顺序、表格和 provenance；只有证明相对主路径有明确增益时才进入正式依赖。
4. **pdfplumber 作为轻量诊断工具**：用于原生文字和表格坐标抽样核对，不承担最终统一解析职责。
5. **LangChain 放在规范化和切分之后，而不是控制底层解析**：底层先产出项目自己的可追溯 Canonical Document；再将合格的条款/表格单元映射为 LangChain `Document`。这样不会因更换 Loader 而丢失页码、坐标、表格上下文和解析质量信息。

推荐的 POC 关系不是四套长期并行的生产代码，而是：

```text
PDF 登记与哈希
      ↓
页面级预检（文字覆盖、乱码、图片占比、页数）
      ↓
┌──────────────────────────────┐
│ 默认路径：PyMuPDF/PyMuPDF4LLM │
│ 对照组：Docling/pdfplumber     │
└──────────────────────────────┘
      ↓ 质量门控未通过
PaddleOCR PP-StructureV3
      ↓
项目统一 Canonical Document
      ↓
条款/表格级切分 → LangChain Document → 索引
```

### 2.2 为什么现在不能直接宣布某个工具胜出

**事实：**三类主候选都声明支持 PDF 结构化处理，但其输出语义不同：Docling 以统一文档对象和 provenance 为核心；PyMuPDF4LLM 强调基于 PDF 内部信息的 Markdown/JSON 抽取；PP-StructureV3 以视觉版面、OCR、表格和阅读顺序恢复为核心。对应官方资料分别见 [Docling 项目说明](https://github.com/docling-project/docling)、[PyMuPDF4LLM 文档](https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/index.html) 和 [PP-StructureV3 使用教程](https://paddlepaddle.github.io/PaddleOCR/main/en/version3.x/pipeline_usage/PP-StructureV3.html)。

**项目推断：**排水规范的关键风险不是“有没有文字”，而是条款编号、层级、表头、单位、注释、页码和坐标能否一起保留。因此，官方通用能力声明不能代替本项目样本验证。

## 3. 本项目的选型约束

根据已批准产品规格，文档接入至少要支持：

- 原生文字 PDF、纯扫描 PDF、文字与扫描混合 PDF；
- 中文条款编号、目录和标题层级；
- 表格的表头、单位、合并单元格、脚注及其与正文的关系；
- 页码和尽可能精确的坐标，以支持右侧原文证据定位；
- 图片或图表区域不被静默丢弃，即使首期不做图像语义问答；
- 批量新增、失败隔离、重试、版本固定和可复现；
- Windows 本地开发和 CPU POC；
- 允许后续封装成 API/MCP 服务的许可证边界；
- 与 LangChain 集成，但不能让 LangChain 的通用 `Document` 成为唯一事实存储。

## 4. 候选能力对比

> 表中“支持”只表示官方接口或文档明确提供该能力，不表示已经在本项目中文语料上通过验证。

| 维度 | Docling | PyMuPDF / PyMuPDF4LLM | PaddleOCR PP-StructureV3 | pdfplumber（辅助） |
| --- | --- | --- | --- | --- |
| 文字 PDF | 原生 PDF 后端、版面与阅读顺序解析 | 强项；可抽取块、词、字符和位置，4LLM 输出 Markdown/JSON | 能接收 PDF，但核心是视觉/OCR 管线；不建议无条件替代原生文字抽取 | 强项限于机器生成且有文字层的 PDF |
| 扫描 PDF | 支持 EasyOCR、RapidOCR、Tesseract 等 OCR 后端 | PyMuPDF 基于 Tesseract；4LLM 可自动/强制 OCR，也可接自定义 OCR | 核心能力，含中文 OCR、版面、表格、方向/矫正等模块 | 无 OCR；需先由其他工具生成文字层 |
| 中文 OCR | RapidOCR 明确支持简体中文，EasyOCR 支持 `ch_sim + en` | 取决于安装的 Tesseract/RapidOCR及语言模型 | 原生中文生态与多语言模型 | 不适用 |
| 表格 | TableFormer 结构恢复，可导出 DataFrame、CSV、HTML、Markdown | 4LLM 可检测并输出 Markdown；JSON/底层 API可保留布局与 bbox | 返回表格 HTML、单元格坐标、OCR 文本和置信度 | 支持线条/文本策略和表格 bbox；复杂表格需大量规则 |
| 目录/标题层级 | 有显式 heading hierarchy，可利用 PDF bookmark、编号和视觉样式；默认关闭 | 4LLM 可识别标题；旧/非 layout 模式可按字号或 TOC 定义层级 | 能检测标题块和阅读顺序；PP-StructureV3 官方输出未证明可稳定恢复任意 H1/H2/H3 层级 | 可读取 tagged PDF 的 structure tree；普通 PDF 需自建规则 |
| 图片 | 可生成页面图、独立图片和表格图；文档对象有 PictureItem | 可抽取/写出/内嵌图片和向量图形 | 能检测图片区域，Markdown/结果对象可带图片 | 可读图片对象及坐标，不做图片语义理解 |
| 页码/坐标 | `ProvenanceItem` 含 `page_no`、`bbox`、`charspan` | 页对象天然有页号；blocks/words/JSON 提供 bbox；4LLM 支持 page chunks | `page_index`、block bbox、文字多边形、表格单元格 bbox | page number、字符/词/表格 bbox |
| 批处理 | `convert_all`/批量示例、线程化 PDF pipeline、超时和 partial success | 需要应用层遍历文件；页级选择和 page chunks 易用 | `predict_iter()` 支持增量处理；官方说明 PDF 目录不能直接作为目录输入，需应用层枚举 | 需要应用层遍历 |
| Windows/CPU | 官方明确支持 Windows x86_64/arm64，可使用 CPU PyTorch；实际内存与速度待测 | 官方有 Windows wheel；4LLM layout 不要求 GPU | 官方支持 `device="cpu"`、CPU threads/MKLDNN；复杂管线可能很慢或耗内存，官方也提示关闭非必要模块 | Python 包，适合 CPU；性能与复杂版面能力待测 |
| LangChain | 官方 `langchain-docling` 与 `DoclingLoader` | `PyMuPDFLoader`、PyMuPDF4LLM 官方集成包/Markdown splitter | 官方 `langchain-paddleocr` 当前重点是 PaddleOCR-VL API；本地 PP-StructureV3 需项目适配 | LangChain 社区 Loader 存在，但本项目仍应从 canonical schema 适配 |
| 许可证 | 代码 MIT；模型许可证需逐个核验 | PyMuPDF/4LLM 为 AGPL v3 或商业许可；当前 layout 组件同样需单独核验 | 项目 Apache-2.0；实际模型与依赖仍应生成许可证清单 | MIT |

## 5. 分项核验

### 5.1 Docling

#### 可核验事实

- Docling 官方列出 PDF 版面、阅读顺序、表格结构、OCR、图片、lossless JSON 和本地运行能力，并明确支持 Windows、Linux、macOS 及 CPU 版 PyTorch。[项目 README](https://github.com/docling-project/docling)；[安装文档](https://docling-project.github.io/docling/getting_started/installation/)
- PDF pipeline 可配置 OCR、表格结构、页面图片、独立图片、表格图片、解析页面、批大小、线程数和文档超时；超时可返回 `PARTIAL_SUCCESS` 及错误信息。[Pipeline options](https://docling-project.github.io/docling/reference/pipeline_options/)
- Docling 支持 RapidOCR、EasyOCR、Tesseract 等 OCR 引擎。官方 OCR 文档明确列出 RapidOCR 的简体中文 `ch`，以及 EasyOCR 的 `ch_sim + en` 组合。[OCR engines](https://docling-project.github.io/docling/concepts/OCR/)
- `DoclingDocument` 的 provenance 数据结构包含 `page_no`、`bbox` 和 `charspan`；表格和图片也是独立文档项。[DoclingDocument API](https://docling-project.github.io/docling/reference/docling_document/)
- 标题层级功能可综合 PDF bookmarks、编号和视觉样式；层级会进入 Markdown 和 hierarchical chunker。该功能默认关闭，使用视觉样式还需保留 parsed pages；扫描件 OCR 没有字体元数据，因此其视觉样式信号受限。[Heading levels](https://docling-project.github.io/docling/usage/heading_levels/)
- 表格可导出为 pandas DataFrame、CSV、HTML 和 Markdown。[表格导出示例](https://docling-project.github.io/docling/_generated/examples/export_tables/)
- 官方批量示例会分别记录成功、部分成功和失败，并可导出 JSON、HTML、Markdown、text、DocTags、YAML。[批量转换示例](https://docling-project.github.io/docling/_generated/examples/batch_convert/)
- LangChain 有官方扩展 `langchain-docling`。`DoclingLoader` 可接受单文件或文件集合，并支持 `DOC_CHUNKS` 与 `MARKDOWN` 输出模式。[Docling LangChain 集成](https://github.com/docling-project/docling-langchain)；[LangChain 官方集成页](https://docs.langchain.com/oss/python/integrations/document_loaders/docling)
- Docling 代码许可证为 MIT。官方同时提醒，各个模型需查看其原始包中的模型许可证；当前官方 `docling-models` 模型卡标记了 CDLA-Permissive-2.0/Apache-2.0。[代码 LICENSE](https://raw.githubusercontent.com/docling-project/docling/main/LICENSE)；[官方模型仓库](https://huggingface.co/docling-project/docling-models)

#### 项目推断

- Docling 最接近本项目需要的“统一结构对象 + 证据坐标 + 表格 + 层级”主解析器接口。
- 对规范文件应显式启用 heading hierarchy，并优先依赖书签和条款编号；不能仅靠字号，因为扫描件缺失字体信息，部分政策文件排版也不稳定。
- 不能只保存 Markdown。Markdown 适合阅读和切分，但不足以稳定承载所有坐标、字符范围、图片与表格结构；应保存 Docling JSON 和解析配置/version。
- Docling 自带 RapidOCR 使用 PP-OCR 模型，可能已经足够处理一部分中文扫描件；是否仍需要独立 PP-StructureV3，要由扫描表格样本对比决定。

#### 待验证

- Windows CPU 下首次模型下载、冷启动、单页耗时、峰值内存和长文档稳定性。
- 中文规范中“章—节—条—款—项”层级恢复是否正确，尤其是没有 bookmarks 的扫描件。
- 跨页表格、无线框表格、合并单元格、单位和脚注的保留程度。
- provenance bbox 与前端 PDF 渲染坐标系之间的转换是否稳定。

### 5.2 PyMuPDF 与 PyMuPDF4LLM

#### 可核验事实

- PyMuPDF 的 blocks、words、DICT/RAWDICT 等接口可返回文本、图片、字体信息和 bbox；基础文本抽取的原始顺序不保证等于自然阅读顺序，可通过 `sort=True` 做几何排序。[文本抽取详解](https://pymupdf.readthedocs.io/en/latest/app1.html)
- PyMuPDF 集成 Tesseract OCR，能对整页或图片区域 OCR。官方明确说明 OCR 远慢于普通文本抽取，因此建议每页只做一次并复用 `TextPage`。[OCR 文档](https://pymupdf.readthedocs.io/en/latest/recipes-ocr.html)
- PyMuPDF4LLM 支持 Markdown、JSON、纯文本、多栏、表格、图片/向量图、page chunks 和自动 OCR；JSON 输出含 bbox 和布局数据。[PyMuPDF4LLM 文档](https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/index.html)；[API](https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/api.html)
- 4LLM 的 OCR 可以自动触发、强制、禁用或通过 `ocr_function` 替换；官方警告对干净文字 PDF 强制 OCR 会显著变慢且可能降低输出质量。[官方仓库说明](https://github.com/pymupdf/pymupdf4llm)
- PyMuPDF 官方提供 Windows 安装说明和 wheel；PyMuPDF4LLM 的 layout 分析不要求 GPU。[PyMuPDF 安装](https://pymupdf.readthedocs.io/en/latest/installation.html)；[PyMuPDF4LLM 文档](https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/index.html)
- LangChain 可使用 `PyMuPDFLoader`；PyMuPDF 官方另有 `langchain-pymupdf4llm` Loader，可 `lazy_load()`。[PyMuPDF RAG 文档](https://github.com/pymupdf/PyMuPDF/blob/main/docs/rag.rst)；[PyMuPDF4LLM LangChain 仓库](https://github.com/pymupdf/langchain-pymupdf4llm)
- PyMuPDF 与 PyMuPDF4LLM 的官方许可证是 GNU AGPL v3 或 Artifex 商业许可。官方文档明确要求不能遵守 AGPL 时联系 Artifex 获取商业许可。[PyMuPDF 许可证说明](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)；[PyMuPDF4LLM LICENSE](https://raw.githubusercontent.com/pymupdf/pymupdf4llm/main/LICENSE)
- 截至调研日，PyMuPDF Layout 的官方包信息标明其为 AGPL/Artifex 商业双许可证；PyMuPDF4LLM 官方变更记录还说明该 layout 包不是开源源码包并有独立许可证。[PyMuPDF Layout 官方包页](https://pypi.org/project/pymupdf-layout/)；[PyMuPDF4LLM CHANGES](https://github.com/pymupdf/pymupdf4llm/blob/main/CHANGES.md)

#### 项目推断

- PyMuPDF 原生 API 很适合做页面预检、坐标调试和快速文字 PDF 基线；PyMuPDF4LLM 适合用最少代码得到可读 Markdown/JSON。
- AGPL 不是 README 角落里的备注。项目已确定开源，因此 PyMuPDF 可进入主路径实验；发布前仍需确认仓库许可证、依赖组合与分发方式兼容。这不是法律结论，正式发布仍需许可证审查。
- 即使使用 PyMuPDF4LLM，也应保留 JSON/page chunks，而不是把全文件 Markdown 直接交给 `MarkdownTextSplitter`；否则页码和细粒度坐标可能在二次切分中丢失。

#### 待验证

- 中文标题层级、条款编号、跨页表格和扫描表格在当前 4LLM layout 模式中的效果。
- Windows 上当前版本组合是否稳定；版本必须锁定，因为 2026 年 layout 依赖和许可证信息发生过变化。
- 若用于对外网络服务，最终许可证义务需由项目所有者结合发布方式确认。

### 5.3 PaddleOCR PP-StructureV3

#### 可核验事实

- PP-StructureV3 面向文档版面解析，包含 OCR、版面区域、表格、公式、印章、阅读顺序和 Markdown 输出。它能接受图片或 PDF，并逐页返回结果。[PP-StructureV3 使用教程](https://paddlepaddle.github.io/PaddleOCR/main/en/version3.x/pipeline_usage/PP-StructureV3.html)
- 输出包含 `page_index`、按阅读顺序排列的 `parsing_res_list`、`block_bbox`、标题等 block/sub-label、OCR 多边形与置信度，以及表格单元格 bbox、HTML、文本和置信度；可保存 JSON、Markdown、可视化图、HTML、XLSX。[PP-StructureV3 输出说明](https://paddlepaddle.github.io/PaddleOCR/main/en/version3.x/pipeline_usage/PP-StructureV3.html)
- `device="cpu"`、CPU threads 和 MKL-DNN 是官方参数；没有 GPU 时默认可使用 CPU。官方同时警告，若卡死、内存不足或极慢，应关闭非必要能力或换轻量模型。[同一使用教程](https://paddlepaddle.github.io/PaddleOCR/main/en/version3.x/pipeline_usage/PP-StructureV3.html)
- `predict_iter()` 以 generator 增量返回结果，适合大型数据或节省内存；官方说明目录输入当前不处理目录中的 PDF，所以批量 PDF 仍需应用层逐文件调度。[同一使用教程](https://paddlepaddle.github.io/PaddleOCR/main/en/version3.x/pipeline_usage/PP-StructureV3.html)
- PaddleOCR 安装文档提供 CPU 版 PaddlePaddle 和 `paddleocr[doc-parser]`；后者包含 PP-StructureV3 等文档解析能力。[安装文档](https://www.paddleocr.ai/v3.3.0/en/version3.x/installation.html)
- 官方 `langchain-paddleocr` 当前提供的是 `PaddleOCRVLLoader`，通过 PaddleOCR-VL 文档解析 API/SDK处理本地路径或 URL；这不等同于“本地 PP-StructureV3 已有一个官方 LangChain Loader”。[官方 LangChain 包](https://github.com/PaddlePaddle/PaddleOCR/tree/main/langchain-paddleocr)
- PaddleOCR 仓库许可证为 Apache License 2.0。[官方 LICENSE](https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/main/LICENSE)

#### 项目推断

- PP-StructureV3 是当前候选中最值得用于中文扫描件与扫描表格专项验证的方案。
- 对干净文字 PDF，不应默认全部走 OCR/视觉解析。原生文字层通常更适合保留精确字符和避免 OCR 引入替换错误；PP-StructureV3 更适合作为页面级路由后的专业路径。
- PP-StructureV3 能识别标题区域，但官方资料未充分证明它能稳定输出适合本项目的“章—节—条—款—项”多级语义树。因此，条款编号解析仍需项目规则和 POC，不应把 `title_text` 直接等同于完整标题层级。
- 本地 PP-StructureV3 应通过项目自己的 adapter 转为 canonical schema，再映射 LangChain `Document`。不应为了“体现 LangChain”而改用远程 PaddleOCR-VL Loader，除非后续另做云端方案决策。

#### 待验证

- Windows CPU 环境中的模型安装、内存、冷启动和长 PDF 稳定性。
- 简体中文正文、标准号、条款号、数字、小数、单位和特殊符号的识别。
- 有框/无线框、跨页、合并单元格及表格脚注。
- OCR bbox 是否能稳定映射回原 PDF 页面；如 PDF 先被栅格化，需要保存 DPI、旋转、裁剪和坐标变换参数。

### 5.4 pdfplumber（必要的辅助候选）

#### 纳入理由

虽然 PyMuPDF 已进入本开源项目的主路径实验，仍需要一个实现独立、许可证宽松的原生文字与坐标对照。pdfplumber 因此作为辅助诊断候选纳入，不承担扫描 PDF、复杂版面恢复或最终统一解析器职责。

#### 可核验事实

- pdfplumber 面向 PDF 字符、线、矩形、图片、文本和表格抽取，能返回文字/匹配结果/表格的 bbox，并提供表格可视化调试；官方明确说明它最适合机器生成 PDF。[官方仓库](https://github.com/jsvine/pdfplumber)
- 官方维护者明确说明：当 PDF 没有嵌入文字时，pdfplumber 本身不能 OCR，需要先由 OCR 工具处理。[官方讨论](https://github.com/jsvine/pdfplumber/discussions/908)
- tagged PDF 可暴露结构树和页号，但旧 PDF 或未良好标记的 PDF 可能只提供很弱的结构语义。[结构树文档](https://github.com/jsvine/pdfplumber/blob/stable/docs/structure.md)
- 许可证为 MIT。[官方 LICENSE](https://github.com/jsvine/pdfplumber/blob/stable/LICENSE.txt)
- LangChain 的官方 Loader 列表包含 PDFPlumber；但 Loader 便利性不代表会自动保留本项目所需的全部字符级坐标。[LangChain Loader 列表](https://docs.langchain.com/oss/python/integrations/document_loaders)

#### 项目推断

- pdfplumber 可用于抽样核对 PyMuPDF/Docling 的原生文字和表格坐标，也可成为非常简单的文字 PDF 应急路径。
- 它不适合作为扫描件方案，也不应承担标题层级恢复；复杂中文排版会迅速演变成大量项目规则。

## 6. 推荐的统一数据契约

**项目推断：解析器输出不能直接作为索引事实模型。**各解析器的页码基数、坐标原点、block 类型和表格格式不同，必须先归一化。建议最小 canonical schema 包含：

```text
DocumentAsset
  asset_id / sha256 / source_uri / registered_version
  parser_name / parser_version / model_ids / config_hash
  processing_status / warnings / started_at / finished_at

Page
  physical_page_index_zero_based
  display_page_number_or_label
  width / height / rotation
  extraction_route: native | hybrid_ocr | full_ocr

Element
  element_id / type
  text / normalized_text
  page_index / bbox / coordinate_origin
  reading_order
  heading_path / clause_path
  parent_id / children_ids
  confidence_if_available

Table
  element_id / html / markdown
  cells[row, col, row_span, col_span, text, bbox]
  caption / unit_context / footnotes

Image
  element_id / page_index / bbox / artifact_path
  caption_if_present / description_status
```

必须额外保存：

- 原始 PDF 和 SHA-256；
- 原始解析器 JSON，不覆盖旧结果；
- 规范化结果版本；
- parser/model/package lock；
- 每页采用的路线与质量门控原因；
- 坐标归一化前后的变换信息。

这样做才能复现一次历史查询，并在升级解析器后比较变化。

## 7. LangChain 集成方案

### 7.1 推荐边界

```text
Parser-specific output
        ↓
Canonical Document（项目拥有）
        ↓
Clause/Table chunk builder（项目拥有）
        ↓
LangChain Document(page_content, metadata)
        ↓
embedding / vector store / retriever / chain
```

LangChain `Document.metadata` 至少携带：

```text
asset_id, corpus_version, document_version,
page_start, page_end, bbox_refs,
heading_path, clause_path, element_ids,
source_uri, parser_run_id, content_hash
```

### 7.2 各候选的接法

- **Docling**：仅在结构对照实验中使用；若实验支持其进入正式接入，必须从 `DoclingDocument` JSON 进入 canonical adapter，再生成 LangChain `Document`，避免 Loader chunk 先于项目条款规则发生。
- **PyMuPDF4LLM**：使用 page chunks/JSON 保留页和布局，再由 adapter 创建 LangChain `Document`；不建议只把全局 Markdown 交给 splitter。发布前完成 AGPL 兼容性确认。
- **PaddleOCR PP-StructureV3**：本地 pipeline 直接保存 JSON/Markdown/图片，经 adapter 归一化；官方 `PaddleOCRVLLoader` 是 API 路线，不能当作本地 PP-StructureV3 的等价实现。
- **pdfplumber**：只在基线或诊断中生成 canonical elements；不依赖通用 Loader 决定最终元数据。

**项目推断：**这种设计既能在简历和面试中真实体现 LangChain，又不会让框架接管法规领域最关键的数据治理与证据定位。

## 8. POC 设计

### 8.1 样本集

从当前资产中选 6–8 份文档、约 30–50 个代表页面，不需要先跑完全部 23 份：

1. 干净文字标准：选择条款编号、目录、普通表格都存在的文档。
2. 地方技术标准：包含地域信息和多级条款。
3. 短政策文件：检验普通段落、标题、发文机关和附件。
4. 纯扫描数值表格：优先包含降水量等级、单位、时段和注释。
5. 长扫描规范：抽取目录、正文、表格各若干页。
6. 混合/部分文字层 PDF：检验页面级路由，不允许整份文件简单二选一。
7. 有跨页表格或复杂合并单元格的文档（若现有语料存在）。

建议直接从现有 inventory 选择：`GB/T 28592-2012 降水量等级`（扫描、数值表格）、`QX/T 489-2019 降雨过程等级`（部分文字层）、福建雨污混接地方标准（文字 PDF）、`GB 55027-2022`（文字标准）、住建部政策通知（短政策）以及一份长扫描规范。选样依据是文档形态，不代表这些材料已经通过来源和效力审核。

### 8.2 四组实验

#### 实验 A：Windows/CPU 可运行性与资源

对每个候选固定版本并记录：

- Python、OS、CPU、RAM、包和模型版本；
- 首次安装与模型下载是否成功；
- 冷启动/热启动时间；
- 每页和每份文档耗时；
- 峰值内存、失败页、超时、是否可继续处理下一文档；
- 输出是否可完全离线复现。

不预设性能数字；先测基线，再决定上线预算。

#### 实验 B：正文与层级

人工标注至少 20 个锚点：文档标题、章、节、条、款、项、附录和目录跳转。检查：

- 中文字符、标点、标准号、条款号和数字是否一致；
- 多栏是否串行错位；
- 页眉页脚是否污染正文；
- heading/clause path 是否正确；
- 每个锚点能否回到正确页和 bbox。

#### 实验 C：表格与数值上下文

人工选择至少 10 个表格/表格片段，包括有框、无线框、合并单元格、跨页、脚注。检查：

- 行列关系和阅读顺序；
- 表头、单位、适用条件、脚注是否与数值一起保留；
- bbox 叠加图是否覆盖正确单元格；
- canonical table 能否生成适合检索的文本，同时保留 HTML/cell grid；
- “20 mm 属于什么降雨等级”所需的统计时段和表头是否仍在同一证据单元中。

#### 实验 D：批处理、降级和 LangChain 元数据

- 对样本目录执行批处理，故意加入损坏 PDF、空白页和超时文档；
- 验证单文档失败不会污染已发布语料，也不会中断后续文档；
- 验证页面级 native → OCR 降级，记录触发原因；
- 生成 LangChain `Document` 后，抽样确认页码、bbox refs、heading path、asset/version 和 parser run 没有丢失；
- 对同一 PDF 重跑，检查相同配置的内容 hash；升级解析器后生成 diff，不静默覆盖。

### 8.3 POC 判定原则

不以单一“准确率”决定。至少分开评审：

- 文字保真；
- 结构/阅读顺序；
- 表格语义；
- 页码/坐标可回查；
- Windows/CPU 成本；
- 批处理与失败隔离；
- 许可证和上线可接受性。

某工具即使文本更完整，只要无法稳定回查页码/坐标，或许可证不符合目标发布方式，也不能自动成为主方案。

## 9. 降级与失败处理

### 9.1 页面级路由建议

1. **Native 路径**：页面有可用文字层且乱码率、文字覆盖、阅读顺序检查通过，使用 PyMuPDF/PyMuPDF4LLM 原生解析。
2. **Hybrid OCR 路径**：页面有部分有效文字、部分图片文字或乱码区域时，按页面或区域降级，并保存触发原因与坐标变换。
3. **Full OCR 路径**：无文字层、扫描件或 native/hybrid 质量门控失败时，使用 PaddleOCR PP-StructureV3；必要时仅开启 OCR、layout、table，关闭公式、图表、印章等与当前页无关模块。
4. **人工隔离**：密码保护、损坏、极端排版、关键表格无法恢复、页码坐标不可信时，不允许以“有一些文字”为由进入正式语料。

### 9.2 质量门控示例

以下门控只定义检查项，阈值由 POC 数据确定：

- 是否有可复制文字，Unicode replacement/乱码比例；
- 页面文字覆盖与大图覆盖；
- 条款号连续性和目录/正文对应；
- 表格行列、单位、脚注完整性；
- provenance 是否包含合法页号和页面范围内 bbox；
- 解析器是否部分成功、警告或超时；
- 同一页 native 与 OCR 文本差异是否异常。

### 9.3 回滚

- 新 parser/model/config 生成新的 processing run 和 corpus candidate；
- 只有抽检及自动门控通过后才发布新 corpus version；
- 旧 canonical output 和旧索引保持可读，可将查询指针回滚到上一版本；
- 不直接修改已经被历史回答引用的 element ID。

## 10. 许可证决策

| 组件 | 官方许可证事实 | 本项目处理建议 |
| --- | --- | --- |
| Docling 代码 | MIT | 可进入 POC；固定实际模型清单并逐一保存模型许可证/NOTICE |
| Docling 默认模型 | 官方模型卡标注 CDLA-Permissive-2.0/Apache-2.0；Docling 明确要求逐模型核验 | 锁定模型 ID/版本，生成 SBOM/模型 BOM，不笼统写“Docling 全部 MIT” |
| PaddleOCR | Apache-2.0 | 可进入 POC；仍需记录 PaddlePaddle、模型和推理依赖版本及 NOTICE |
| PyMuPDF / PyMuPDF4LLM | AGPL v3 或商业许可 | 开源项目主路径候选；发布前确认仓库许可证兼容性并保留许可证/NOTICE，不据此推断任何闭源部署权利 |
| PyMuPDF Layout | 官方包标注 AGPL/商业双许可，官方变更记录称其非开源源码包 | 与 PyMuPDF4LLM 一并门控，不能因“本地 CPU”而忽略许可证 |
| pdfplumber | MIT | 可作为文字 PDF 基线/诊断工具 |

**项目决定与边界：**本项目计划开源，因此采用 PyMuPDF 做主路径实验。最终开源许可证尚未在本阶段选定，发布前必须完成依赖兼容检查；若未来转为闭源企业服务，需要重新评审或取得商业许可。

## 11. 暂不扩展的候选

本轮不继续加入 MinerU、Unstructured、云 OCR/VLM 等候选，原因不是认定它们能力较弱，而是三项主候选已覆盖本轮关键决策面：

- PyMuPDF4LLM：CPU 原生 PDF 主路径及许可证兼容性；
- Docling：统一结构与 provenance 对照；
- PaddleOCR：中文扫描与复杂表格路径；
- pdfplumber：宽松许可证文字 PDF 基线。

只有当 POC 暴露出上述组合无法解决的具体缺口时，再带着明确问题新增候选。例如：跨页表格始终失败、CPU 成本不可接受，或某种扫描质量无法恢复。这样可以避免为了“工具数量”增加评估成本。

## 12. 下一步决策输入

完成 POC 后，技术方案评审只需要回答四个问题：

1. PyMuPDF/PyMuPDF4LLM 是否能稳定保留中文条款、表格、阅读顺序和证据坐标？
2. 哪些可观测条件会将页面路由到 PP-StructureV3？
3. Docling 的结构恢复是否提供足以增加正式依赖的可测增益？
4. canonical schema 是否能让四种输出映射为同一套可回查证据，且拟采用的开源许可证与实际依赖兼容？

在这些问题有本项目实验记录之前，不把任何解析准确率、吞吐量或“生产可用”写进简历和架构结论。

## 13. 主要一手来源索引

### Docling

- [官方 GitHub](https://github.com/docling-project/docling)
- [官方安装文档](https://docling-project.github.io/docling/getting_started/installation/)
- [Pipeline options](https://docling-project.github.io/docling/reference/pipeline_options/)
- [OCR engines](https://docling-project.github.io/docling/concepts/OCR/)
- [Heading levels](https://docling-project.github.io/docling/usage/heading_levels/)
- [DoclingDocument API](https://docling-project.github.io/docling/reference/docling_document/)
- [批量转换示例](https://docling-project.github.io/docling/_generated/examples/batch_convert/)
- [表格导出示例](https://docling-project.github.io/docling/_generated/examples/export_tables/)
- [LangChain 官方集成仓库](https://github.com/docling-project/docling-langchain)
- [代码许可证](https://raw.githubusercontent.com/docling-project/docling/main/LICENSE)
- [官方模型仓库及模型许可证](https://huggingface.co/docling-project/docling-models)

### PyMuPDF / PyMuPDF4LLM

- [PyMuPDF 文本抽取详解](https://pymupdf.readthedocs.io/en/latest/app1.html)
- [PyMuPDF OCR 文档](https://pymupdf.readthedocs.io/en/latest/recipes-ocr.html)
- [PyMuPDF4LLM 文档](https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/index.html)
- [PyMuPDF4LLM API](https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/api.html)
- [PyMuPDF4LLM 官方仓库](https://github.com/pymupdf/pymupdf4llm)
- [PyMuPDF RAG/LangChain 文档](https://github.com/pymupdf/PyMuPDF/blob/main/docs/rag.rst)
- [PyMuPDF4LLM LangChain 仓库](https://github.com/pymupdf/langchain-pymupdf4llm)
- [官方许可证说明](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)
- [PyMuPDF4LLM LICENSE](https://raw.githubusercontent.com/pymupdf/pymupdf4llm/main/LICENSE)
- [PyMuPDF Layout 官方包页](https://pypi.org/project/pymupdf-layout/)

### PaddleOCR

- [PaddleOCR 官方 GitHub](https://github.com/PaddlePaddle/PaddleOCR)
- [PP-StructureV3 使用教程](https://paddlepaddle.github.io/PaddleOCR/main/en/version3.x/pipeline_usage/PP-StructureV3.html)
- [PaddleOCR 安装文档](https://www.paddleocr.ai/v3.3.0/en/version3.x/installation.html)
- [PaddleOCR LangChain 官方包](https://github.com/PaddlePaddle/PaddleOCR/tree/main/langchain-paddleocr)
- [PaddleOCR LICENSE](https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/main/LICENSE)

### pdfplumber 与 LangChain

- [pdfplumber 官方 GitHub](https://github.com/jsvine/pdfplumber)
- [pdfplumber 结构树文档](https://github.com/jsvine/pdfplumber/blob/stable/docs/structure.md)
- [pdfplumber LICENSE](https://github.com/jsvine/pdfplumber/blob/stable/LICENSE.txt)
- [LangChain 文档 Loader 列表](https://docs.langchain.com/oss/python/integrations/document_loaders)

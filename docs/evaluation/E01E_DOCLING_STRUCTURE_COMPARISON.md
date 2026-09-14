# E-01E Docling 结构恢复小样本对照

## 1. 结论

Docling 2.126.0 在 4 个固定难页上全部完成解析。它明显改善了复杂表格的结构恢复，并修复了 PaddleOCR 在 `TAB-10` 的关键错字；但公式页仍不可用，水印/页眉过滤也不充分。因此本项目不把 Docling 设为所有 PDF 的统一解析器，而把它保留为复杂表格失败后的定向结构恢复候选。

## 2. 固定配置

| 项目 | 配置 |
|---|---|
| Docling | 2.126.0 |
| docling-core | 2.95.0 |
| docling-parse | 7.17.0 |
| PDF 后端 | `PyPdfiumDocumentBackend` |
| OCR | RapidOCR PP-OCRv6，torch CPU，中文，强制整页 OCR |
| 表格 | TableFormer accurate |
| 页面 | M2-P015、M2-P024、M2-P026、M2-P036 |

原始 Docling Document JSON、Markdown、文字、锚点评价、耗时和内存位于 `data/eval_results/m2-docling-2.126.0-v1-*`。

## 3. Windows Unicode 路径兼容性

默认 `ThreadedDoclingParseDocumentBackend` 在当前含中文的项目绝对路径下无法读取包内 `pdf_resources/glyphs/standard/additional.dat`。文件实际存在且 Python 可读；将 `docling_parse` 临时放到纯 ASCII 路径后，资源错误消失，证明问题位于原生层的 Unicode 包路径处理。

项目没有修改第三方包，也没有要求把项目迁移到英文目录，而是显式采用 Docling 支持的 `PyPdfiumDocumentBackend`。同一 PDF、同一页在该后端下成功解析。该兼容性结论应保留在运行元数据中，不能只留在开发机操作记录里。

## 4. 逐页比较

| 页面 | 主要难点 | Docling 结果 | 与 PaddleOCR 比较 | 决定 |
|---|---|---|---|---|
| M2-P015 | 稀疏条款、大水印 | 恢复“职业卫生、自然灾害、进行演练”，严格锚点 1/1；仍混入水印文字和页首重复字 | 正文完整度更好 | 可作正文恢复候选，仍需水印/页眉过滤 |
| M2-P024 | 多个二维公式、上下标 | 多处 `formula-not-decoded`，并出现“入流→人流”等错误 | 两者均不能可靠承载公式 | 公式证据隔离或人工录入，不自动发布 |
| M2-P026 | 同页两张表、单位和行关系 | 检出两表，恢复 `km²`、2.53、31、70.55；出现一次孤立单元格最近行回填警告 | 结构与单位优于 PaddleOCR | 可作表格恢复候选，但保留结构警告并核验行关系 |
| M2-P036 | 两张合并单元格评分表 | `TAB-10` 10/10，通过“地块项目”目标表选择，正确恢复“管网竣工数据资料” | PaddleOCR 为 9/10 且误作“峻工” | Docling 结果优先作为该页表格候选 |

P026 的自动断言为 2/3，唯一未命中项是连续字符串 `排水区域7`。视觉和结构输出中实际存在“排水区域编号”表头、编号 7 行，以及同一行的 2.53、31、70.55。该失败属于当前线性文本断言无法表达“表头—行—单元格”关系，不应误判为字段缺失；Canonical Document 的表格断言需要支持行列语义。

## 5. 资源结果

最终缓存后运行 4 页约 31.6 秒，进程峰值工作集约 1.84 GB，0 个运行失败。首次运行包含模型初始化和下载，不能与缓存后数据混为一谈。Docling 新增了 Torch、Transformers、RapidOCR、OpenCV 等依赖，因此即使小样本性能较好，也要把镜像体积、模型许可和冷启动纳入部署评价。

## 6. TD-01 阶段结论

当前解析路由收敛为：

1. 有可靠文字层：PyMuPDF 原生解析；
2. 扫描正文或一般扫描表格：PaddleOCR 轻/重路径；
3. PaddleOCR 复杂表格字段门控失败：Docling + PyPDFium 定向结构恢复；
4. 二维公式、无法确认的单元格关系或关键字段仍失败：人工复核或隔离；
5. 任何工具结果都必须先进入项目自有 Canonical Document 和质量门控，不能直接成为可发布证据。

下一步不再增加解析工具。将 40 个固定页面合并为逐页路由账本，并以已经观察到的条款、表格、公式和定位需求定义 Canonical Document。

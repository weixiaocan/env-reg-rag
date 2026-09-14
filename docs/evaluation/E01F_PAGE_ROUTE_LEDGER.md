# E-01F 40 页解析路由账本

## 1. 结论

40 个固定代表页面已全部获得唯一、可解释的解析决定。账本同时保存“选择哪条解析路径”和“当前质量状态”，避免把工具运行成功误写成可发布语料。

| 决定状态 | 页面数 | 含义 |
|---|---:|---|
| approved | 20 | 当前代表页证据支持所选路径通过 M2 门控 |
| needs_manual_review | 17 | 已有候选输出，但低置信度、锚点、表格关系或视觉结构仍需复核 |
| quarantine | 1 | 公式结构无法可靠恢复，不允许作为自动证据 |
| skip_blank | 2 | 人工确认的视觉空白页，不触发无意义 OCR |

| 最终路径 | 页面数 |
|---|---:|
| PyMuPDF native | 14 |
| PaddleOCR full OCR | 15 |
| PaddleOCR hybrid OCR | 6 |
| Docling recovery | 3 |
| skip blank | 2 |

正式产物为 `data/registry/m2-page-route-ledger-v1.csv` 与同名 `.meta.json`。元数据保存固定样本、路由计划、失败归因、Docling 覆盖决定及四类解析结果的 SHA-256。

## 2. 决策规则

1. 视觉空白页直接 `skip_blank`；
2. native 候选必须通过文字层质量门控，未命中的人工锚点保留复核；
3. PaddleOCR 以 v1 轻/重 profile 为基础，低置信度、强制视觉复核、未解决锚点或表格断言失败均不得自动批准；
4. 已归因的句末标点/句界假阴性可以豁免，但豁免原因必须进入账本；
5. P015、P026、P036 按 E-01E 结果定向选择 Docling，其中只有 TAB-10 10/10 的 P036 当前批准；
6. P024 公式结构在 PaddleOCR 和 Docling 下均不可靠，状态为 `quarantine`。

## 3. 当前人工复核边界

17 页 `needs_manual_review` 不是要求用户现在逐页重做 OCR。它们是进入未来语料版本前的质量队列，后续可按风险排序：关键条款/表格错误优先，目录、参考文献和视觉图示其次。M2 的目标是让失败可见并可路由，不是把实验样本数字修饰成 100% 自动通过。

## 4. 下一步

路由账本已经成为 Canonical Document 构建的唯一页面选择输入。Canonical 层必须保留原始文档 SHA、处理运行、物理/印刷页码、解析器、路由、质量状态、元素、表格、bbox 和原始产物引用；`needs_manual_review` 与 `quarantine` 页面不能静默进入未来可发布语料。

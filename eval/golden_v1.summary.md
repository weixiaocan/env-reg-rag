# golden_v1 正式候选集摘要

- corpus: `corpus-069284e6d60e`
- cases: **50** (`G001`-`G050`)
- status: **reviewed**
- document coverage: **23/23**
- source locator: 每条 evidence 都含当前 Canonical `doc_file + physical page + element_ids + quote`
- refusal / false-premise cases: **4** (`G008`, `G015`, `G028`, `G050`)

## 挑战性覆盖

| 类别 | 数量 |
| --- | ---: |
| 公式 | 8 |
| 表格 | 20 |
| 图片/图注 | 8 |
| 跨页 | 7 |
| 多 PDF | 5 |
| 拒答/错误前提 | 4 |

## 校验边界

`reviewed` 表示数据结构、文档名、物理页、元素 ID 和引文定位已对当前发布 Canonical 自动校验，并对多文档、跨页和拒答代表题做了人工复核。它不代表 50 条已全部完成原 PDF 版式的逐页人工终审；用户确认后再统一将状态升级为 `frozen`。

## 可重复验收

```powershell
.venv\Scripts\python.exe scripts\finalize_golden_v1.py
.venv\Scripts\python.exe -m unittest tests.test_golden_dataset -v
```

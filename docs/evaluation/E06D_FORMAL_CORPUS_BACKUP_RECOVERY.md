# E06D 正式语料备份、恢复、撤回与回滚演练

## 1. 目的与边界

Qdrant 是可重建检索投影，但重新计算 embedding 仍需要时间和模型文件。正式发布因此同时保留两条恢复路径：从事实源重新构建，以及从 collection snapshot 快速恢复。本次演练验证第二条路径，并把备份下载到 Docker 命名卷之外，避免“卷损坏时卷内快照也丢失”的伪备份。

本次是本地单节点演练。卷外备份仍位于同一台工作站，不等于异地灾备，也没有据此承诺 RPO、RTO 或高可用。

## 2. 演练对象

- 正式源 collection：`corpus_formal_v1`；
- 稳定查询 alias：`corpus_current`；
- 临时恢复 collection：`corpus_formal_v1_m6_restored`；
- 正式数据量：29 points；
- 检索冒烟：AI-009，预期首条证据 `ev_6de4def06fa0cc2273e5fc2b5761ff79`。

## 3. 实际执行顺序

```text
确认 corpus_current → corpus_formal_v1
  → 创建正式 collection snapshot
  → 下载到宿主机 data/backups/qdrant
  → 校验 snapshot SHA-256
  → 从卷外文件恢复临时 collection
  → 比较 point 数与完整投影指纹
  → 发布恢复副本到 corpus_current
  → readiness + 检索冒烟
  → 撤回恢复候选并回滚原正式 collection
  → readiness + 检索冒烟
  → 删除已脱离 alias 的临时 collection
```

脚本使用 `finally` 保证发生异常时优先将 alias 恢复到原正式 collection。删除动作只针对固定名称 `corpus_formal_v1_m6_restored`，不会删除正式源 collection 或持久卷。

## 4. 验收结果

| 检查 | 结果 |
| --- | --- |
| 卷外备份大小 | 481,792 bytes |
| Qdrant checksum | `b8cc181f877abfdc677e5f5a8b84d58bbf5fc32b44cc230f15bd47314210ca09` |
| 下载文件 SHA-256 | 与 Qdrant checksum 一致 |
| 恢复 point 数 | 29，与源 collection 一致 |
| 投影内容指纹 | 恢复前后 SHA-256 一致 |
| 恢复副本发布后 readiness | ready |
| 恢复副本发布后检索 | 目标证据命中且排第 1 |
| 撤回和回滚后 readiness | ready |
| 撤回和回滚后检索 | 目标证据命中且排第 1 |
| 最终 alias | `corpus_current → corpus_formal_v1` |
| 临时 collection | 已删除 |

外部备份保存在 `data/backups/qdrant/`，目录已加入 `.gitignore`。机器可读验收结果位于 `data/eval_results/m6-formal-recovery-v1.json`。

## 5. 发布、撤回与回滚语义

- **发布**：创建新的不可变 collection，通过校验后原子切换稳定 alias；
- **撤回候选版本**：不删除正在使用的源数据，而是把 alias 切回上一批准版本；
- **回滚**：确认旧版本可检索且 readiness 正常后，再清理已脱离 alias 的失败候选；
- **备份恢复**：snapshot 本身不包含 alias，恢复后必须依据发布记录显式重建 alias。

这套设计使用户和 Agent 始终查询 `corpus_current`，不需要知道底层 collection 名称，也避免通过覆盖原 collection 完成发布。

## 6. 运行命令

在当前正式 alias 指向已批准版本且应用 ready 时执行：

```powershell
.venv\Scripts\python.exe scripts\run_m6_formal_recovery_rehearsal.py
```

该命令不调用回答模型。重复执行会生成新的时间戳快照和卷外备份，因此正式环境应结合保留周期、异地复制、加密和访问控制制定备份策略。

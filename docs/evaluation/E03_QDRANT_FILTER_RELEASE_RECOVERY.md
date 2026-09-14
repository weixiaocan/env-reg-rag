# E-03 Qdrant 过滤、发布与恢复演练

## 1. 结论

M3 实验语料已在本地自托管 Qdrant 1.19.1 上完成结构化过滤、alias 发布切换、回滚和 collection snapshot 恢复验证。所有验证均通过真实 HTTP 服务执行，不是内存版 Qdrant 或模拟对象。

本轮证明了检索基础设施具备以下能力：

1. 地域、文种、效力状态可以组合过滤。
2. `as_of` 日期会被转换为 `effective_from <= as_of <= effective_to`，未知有效期的文档不会混入带日期的结果。
3. 应用只查询稳定 alias，发布新 collection 和回滚旧 collection 时不改变查询入口。
4. snapshot 可恢复为新的 collection；恢复后显式重建 alias，目标证据仍可检索。

这些结论只说明技术路径已验证。当前 58 个 chunk 仍来自 `experiment_only` 样本，本报告不批准 formal corpus v1。

## 2. 结构化过滤

真实服务集成测试使用多个相似候选，分别制造地域、文种、效力状态和有效期不匹配。查询条件同时指定：

```text
jurisdiction = 重庆
document_kind = policy
effective_status = current
as_of = 2025-06-01
```

结果只保留在该日期有效的重庆现行政策。未来正式语料必须提供 `effective_from` 和 `effective_to`；若无法核实，不应通过带日期的现行有效过滤。

## 3. alias 发布与回滚

发布服务以不可变 collection 为版本单位，查询端只使用 alias。测试顺序为：

```text
发布 v1 -> alias 查询命中 v1
发布 v2 -> 同一 alias 查询命中 v2
回滚 v1 -> 同一 alias 查询重新命中 v1
```

旧 collection 在切换时不会被覆盖或删除，因此可作为回滚目标。删除与创建 alias 在一次 Qdrant alias 更新请求中完成。

## 4. snapshot 恢复演练

完整演练使用 58 个实验 chunk、BGE dense、multilingual BM25 和 RRF：

- 原 collection：`m3_experiment_release_v1`
- 恢复 collection：`m3_experiment_restored_v1`
- 查询 alias：`m3_experiment_current`
- snapshot checksum：`cb77f438d7676e9083a0bb82d30a4a903e54ab99bddc0d4201802697239cc0a7`
- 冒烟问题：Golden Set `AI-010`
- 预期定位证据：`ev_58337104c115f4ddb491ad736f859ae7`

原版本发布后、恢复版本发布后以及回滚原版本后三次冒烟均通过，目标证据均为第 1 名。RRF 后续同分候选次序存在轻微变化，因此验收关注目标证据和使用策略，不把无关候选的全序列当作恢复一致性条件。

原始运行记录位于 `data/eval_results/m3-qdrant-release-recovery-v1.json`。

## 5. 边界与下一步

- Qdrant snapshot 不包含 alias；恢复流程必须依据 release manifest 重建 alias。
- 本轮是本地单节点恢复演练，不等于异地灾备。
- 当前实验样本尚未完成官方全文与本地文件一致性核验，不能创建 `corpus_current` 正式 alias。
- 下一步逐份完成来源门控，只把满足条件的 document version 写入 formal corpus manifest v1。

## 6. Formal corpus v1 正式发布

2026-09-07 经明确批准后，3 份已通过官方全文、SHA-256 一致性和现行状态门控的文档组成 formal corpus v1：

- `doc_f28bb600c89914f7`：GB/T 28592-2012；
- `doc_48429f3750209858`：武汉 DB4201/T 651-2021；
- `doc_4336797dedd12e80`：重庆雨污分流效果评价技术导则（试行）。

正式投影包含 29 个 chunk，保存于 `data/retrieval/formal-corpus-v1-chunks.jsonl`；manifest 保存于 `data/registry/formal-corpus-v1.json`。发布脚本先对 `corpus_formal_v1` 运行 AI-007、AI-008、AI-009 三条预发布冒烟，全部通过后创建 snapshot，再原子切换 `corpus_current` 并重复冒烟，三条目标证据在切换前后均为第 1 名。

正式发布状态、输入哈希、snapshot checksum 和逐条冒烟结果保存在 `data/registry/formal-corpus-v1-release.json`。其余 5 份实验文档仍未进入正式 collection。

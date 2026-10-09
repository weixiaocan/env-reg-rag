# Claude ↔ Codex 协作协议

角色：
- **Claude Code**：调度与独立审核。写任务卡、调用 Codex、审核产物、给 PASS/REJECT。不代写 Codex 的交付。
- **Codex**：执行者。只做当前任务卡范围内的事，交付代码/测试/产物和 `REPORT.md`。
- **负责人（人类）**：只在授权节点拍板（发布/切换指针、外部 LLM 评估调用、最终人工抽查）。

目录：
```
.claude/handoff/
  PROTOCOL.md          本文件
  tasks/Txx.md         任务卡（Claude 写，Codex 只读）
  reports/Txx.md       Codex 交付报告（每轮覆盖并在末尾追加轮次记录）
  reviews/Txx.md       Claude 审核意见（PASS / REJECT + 必改项）
```

Codex 规则：
1. 先读 `AGENTS.md`、本文件、当前任务卡；如有 `reviews/Txx.md` 也要读并逐条处理。
2. 只做任务卡"范围"内的事；"禁止"列表是硬约束。遇到范围外问题写进报告"发现但未处理"，不要顺手改。
3. 不切换 `data/registry/corpus-current.json`，不 `--publish`，不调用外部 LLM API，不删除 `data/raw/`、`data/registry/`、`eval/` 下任何文件，除非任务卡明确允许。
4. 不使用 `git reset --hard`、`git clean`、`git checkout -- .`、`push`、`--no-verify`。
5. 报告必须包含：做了什么、改动文件清单、运行过的命令及结果（测试通过/失败数原文）、产物路径与哈希、已知限制、发现但未处理。不写"应该可以"之类未验证结论。
6. 质量状态如实记录：不得伪造 `passed`；模型核对结论必须标注为 model review，不得写成人工审核。
7. **原图为准**：任何改动数据内容的任务，验收必须包含"对照 PDF 原图"的样本复测，报告列出至少 3 个修复前/后对照实例（原图文字、旧文本、新文本）。测试全绿和哈希一致不构成内容质量证据。
8. **额度意识**：执行额度有限。优先小步可验证；不要为格式整理、报告修辞消耗大量额度；遇到阻断及时停下写报告，不要盲目重试。
9. 每张卡最多 3 轮返工；第 3 轮仍未通过则停下，由 Claude 与负责人重新评估方向。

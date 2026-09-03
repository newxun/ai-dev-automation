# Contributing

**Language:** [English](#english) | [中文](#中文)

---

<a id="english"></a>

## English

This repo is a **personal automation toolkit**. Add artifacts only when they solve a real dev problem, can be reused, and are documented in English.

### Before adding an artifact

1. What real problem does it solve?
2. Will it be used again or evolve over time?
3. Does it automate repetitive work while keeping human judgment where it matters?

If unclear, note the idea in `docs/roadmap.md` first — don't create new directories prematurely.

### Workflow

1. Copy `templates/artifact-template.md`.
2. Place it in the best existing directory.
3. Fill in Problem, Goal, Use Cases, Usage, Limitations, Roadmap.
4. Add a minimal working example.
5. Run it on a real task, then iterate.

### Naming

- Lowercase English with hyphens: `ai-assisted-code-review`
- Name the problem or scenario, not a one-off task
- Avoid hype words: `ultimate-agent`, `all-in-one-workflow`

### Quality bar

- Grounded in real dev scenarios
- Clear usage instructions
- Explicit limitations
- Automation-first, with documented human checkpoints where needed
- Iteratable over time

### When to create a new directory

Only when an existing category already holds multiple stable entries:

- Multiple MCP items → `artifacts/mcp/`
- Multiple benchmarks → `artifacts/benchmarks/`
- Runnable scripts/CLIs → `artifacts/automation/` or `tools/`

### Maintenance

- Remove unused artifacts
- Merge duplicate prompts or checklists
- Promote repeated patterns into templates
- Log success and failure cases in the artifact itself

---

<a id="中文"></a>

## 中文

本仓库是 **个人自动化工具库**。仅在能解决真实研发问题、可复用、且以英文记录时，才新增 Artifact。

### 新增前自问

1. 它解决了哪个真实研发问题？
2. 是否能在未来再次使用或继续演进？
3. 是否自动化重复劳动，同时在关键决策处保留人工判断？

若答案不清晰，先把想法记到 `docs/roadmap.md`，不要急着建新目录。

### 推荐流程

1. 从 `templates/artifact-template.md` 复制模板。
2. 放到最合适的现有目录。
3. 补全 Problem、Goal、Use Cases、Usage、Limitations、Roadmap。
4. 添加最小可用示例。
5. 在真实任务中使用后再根据反馈调整。

### 命名建议

- 小写英文 + 连字符，例如 `ai-assisted-code-review`
- 名称描述问题或场景，而非一次性任务
- 避免过大词汇，例如 `ultimate-agent`、`all-in-one-workflow`

### 质量标准

- 面向真实研发场景
- 使用方式清晰
- 有明确边界和限制
- 自动化优先，必要时标注人工检查点
- 可被后续版本迭代

### 何时创建新目录

仅当现有目录已容纳多个稳定条目时：

- 多个 MCP 相关内容 → `artifacts/mcp/`
- 多个 benchmark → `artifacts/benchmarks/`
- 可运行脚本或 CLI → `artifacts/automation/` 或 `tools/`

### 维护节奏

- 删除不再使用的 Artifact
- 合并重复的 Prompt 或 Checklist
- 把重复模式沉淀为模板
- 在对应 Artifact 中补充成功与失败案例

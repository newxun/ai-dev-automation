# Artifacts

**Language:** [English](#english) | [中文](#中文)

---

<a id="english"></a>

## English

Core assets for the **ai-dev-automation** toolkit — skills, agents, workflows, scripts, and reusable automation building blocks.

An artifact can be a Prompt, Workflow, Checklist, Knowledge base entry, Benchmark, MCP integration, Agent, Skill, Template, Script, CLI, or Experiment. Start with existing categories; add new ones only when usage justifies it.

### Categories

| Directory | Purpose |
|-----------|---------|
| `skills/` | Installable/reusable Skills (primary surface area) |
| `agents/` | Agent playbooks for specific dev scenarios |
| `workflows/` | Multi-step automation with inputs, outputs, and checkpoints |
| `checklists/` | Repeatable quality gates |
| `knowledge/` | Patterns, anti-patterns, context engineering notes |
| `evaluations/` | Scenario tests for prompts, agents, and workflows |
| `prompts/` | Reusable prompt fragments |

### Placement rules

Prefer existing directories. Create a new category only when multiple stable entries already exist.

Every artifact should document:

- **Problem** — Why it exists
- **Goal** — What it solves
- **Use Cases** — When to use it
- **Usage** — How to run it
- **Limitations** — Known gaps
- **Roadmap** — Planned improvements

---

<a id="中文"></a>

## 中文

**ai-dev-automation** 工具库的核心资产区 —— Skills、Agents、Workflows、脚本及可复用自动化组件。

Artifact 可以是 Prompt、Workflow、Checklist、Knowledge、Benchmark、MCP、Agent、Skill、Template、Script、CLI、Experiment 等。优先使用现有分类，用量足够时再扩展。

### 分类

| 目录 | 用途 |
|------|------|
| `skills/` | 可安装/复用的 Skill（主要扩展面） |
| `agents/` | 面向特定研发场景的 Agent playbook |
| `workflows/` | 多步骤自动化，含输入、输出与检查点 |
| `checklists/` | 可重复使用的质量门禁 |
| `knowledge/` | 模式、反模式、上下文工程资料 |
| `evaluations/` | Prompt / Agent / Workflow 场景化验证 |
| `prompts/` | 可复用 Prompt 片段 |

### 放置规则

优先放入现有目录；仅当某类已有多个稳定条目时，再创建新分类。

每个 Artifact 建议包含：Problem、Goal、Use Cases、Usage、Limitations、Roadmap。

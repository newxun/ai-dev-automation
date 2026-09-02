# Personal Dev Automation Toolkit

**Language:** [English](#english) | [中文](#中文)

> **Note:** This repository is transitioning from a team-oriented artifact collection (`biweekly-artifacts`) to a personal, automation-first toolkit. A GitHub rename is planned; update your local remote after the rename.

---

<a id="english"></a>

## English

A personal repository for building **fully automated development workflows** — skills, agents, scripts, CLIs, MCP integrations, and reusable artifacts that compound over time.

This is not a one-off demo or a team reporting repo. It is a long-term **personal engineering lab**: tools are added incrementally, refined through real use, and composed into end-to-end automation.

### Vision

- **Automation-first:** Prefer workflows that run with minimal manual steps; document human checkpoints only where judgment is required.
- **English-first:** Tool names, skill definitions, CLI flags, prompts, and primary documentation are in English. Chinese translations live alongside when useful.
- **Personal, then shareable:** Optimized for one developer's daily work; anything worth keeping gets structured so it can be reused later.
- **Compounding assets:** Every artifact should be reusable, composable, and improvable — value comes from long-term accumulation, not one-off output.

### What belongs here

| Category | Examples |
|----------|----------|
| Skills & agents | Cursor/Claude skills, agent playbooks |
| Workflows | Multi-step automation with clear inputs/outputs |
| Scripts & CLIs | Runnable tools for repetitive tasks |
| MCP & integrations | Server configs, tool wrappers |
| Checklists & templates | Quality gates, artifact scaffolds |
| Evaluations | Scenario tests for prompts and workflows |

### Repository layout

```text
├── README.md                 # Bilingual overview (English default)
├── CONTRIBUTING.md           # How to add and maintain artifacts
├── artifacts/                # Core assets (skills, agents, workflows, …)
│   ├── skills/
│   ├── agents/
│   ├── workflows/
│   ├── checklists/
│   └── …
├── docs/                     # Principles, roadmap, design specs
└── templates/                # Copy-paste scaffolds for new artifacts
```

See [`artifacts/README.md`](artifacts/README.md) for placement rules and [`docs/principles.md`](docs/principles.md) for design principles.

### Each artifact should document

- **Problem** — Why it exists
- **Goal** — What it solves
- **Use cases** — When to use it
- **Usage** — How to run it
- **Limitations** — Known gaps and failure modes
- **Roadmap** — Planned improvements

Start from [`templates/artifact-template.md`](templates/artifact-template.md).

### Workflow

1. Start from a real friction point in daily development.
2. Scaffold with the template; implement in English.
3. Run it on a real task; capture failures and edge cases.
4. Iterate, merge, or retire — keep the toolbox lean.

### Long-term direction

- A library of **production-grade automation tools**
- Composable workflows across review, testing, triage, and release
- Evaluations that prove tools work before they become defaults
- Optional MCP servers and CLIs as the surface area grows

---

<a id="中文"></a>

## 中文

个人 **全自动研发流程** 工具库 —— 用于沉淀 Skills、Agents、脚本、CLI、MCP 集成及可复用资产，并随使用持续演进。

仓库定位已从团队双周汇报式的 Artifact 收集，转为 **个人自动化实验室**：工具逐步添加、在真实场景中打磨，并组合成端到端流程。

### 愿景

- **自动化优先：** 优先构建可自动运行的流程；仅在需要人工判断处保留明确检查点。
- **英文为主：** 工具名、Skill 定义、CLI 参数、Prompt 及主文档以英文为准；必要时提供中文对照。
- **个人优先、可复用：** 先服务个人日常研发；值得保留的内容会结构化，便于日后复用或分享。
- **复利积累：** 每个 Artifact 应可复用、可组合、可迭代 —— 价值来自长期积累，而非一次性产出。

### 适合放入仓库的内容

| 类别 | 示例 |
|------|------|
| Skills & Agents | Cursor/Claude Skills、Agent  playbook |
| Workflows | 多步骤自动化流程 |
| Scripts & CLIs | 消除重复劳动的可执行工具 |
| MCP & 集成 | 服务配置、工具封装 |
| Checklists & Templates | 质量门禁、文档模板 |
| Evaluations | Prompt / Workflow 场景化验证 |

### 目录结构

```text
├── README.md                 # 中英双语总览（默认英文）
├── CONTRIBUTING.md           # 新增与维护规范
├── artifacts/                # 核心资产区
│   ├── skills/
│   ├── agents/
│   ├── workflows/
│   ├── checklists/
│   └── …
├── docs/                     # 原则、路线图、设计说明
└── templates/                # 新建 Artifact 的模板
```

分类规则见 [`artifacts/README.md`](artifacts/README.md)，设计原则见 [`docs/principles.md`](docs/principles.md)。

### 每个 Artifact 建议包含

- **Problem** — 为什么存在
- **Goal** — 解决什么问题
- **Use cases** — 适用场景
- **Usage** — 如何使用
- **Limitations** — 已知限制与失败模式
- **Roadmap** — 后续计划

可从 [`templates/artifact-template.md`](templates/artifact-template.md) 复制模板开始。

### 使用方式

1. 从日常研发中的真实痛点出发。
2. 用模板搭建结构，实现以英文为主。
3. 在真实任务中运行，记录失败案例与边界。
4. 持续迭代、合并或淘汰，保持工具箱精简。

### 长期方向

- 可投入日常使用的 **自动化工具库**
- 可组合的 Review、测试、分诊、发布等流程
- 工具默认化前的场景化评估
- 随规模扩展的 MCP 服务与 CLI

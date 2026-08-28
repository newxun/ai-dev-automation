# Skills

这里存放面向真实研发场景的 Agent Skill：在**主对话**里被模型按需加载并遵循的可复用工作流与方法，遵循 Augment Humans, Not Replace Humans。

## 何时用 Skill（而不是 Agent / Subagent）

- **Skill**：人在环、跨轮、可能跨会话的主线工作流——需要向用户提问、请求确认、逐步交接。以渐进披露组织：常驻的只是精简内核与触发描述，细节按需读入。
- **Subagent**：非交互、隔离、自治的专项子任务——边界明确、可独立调查、无需用户确认（如复杂依赖冲突、特定构建失败、独立历史调查）。适合被主线工作流在需要时派发，而不是承载整个人在环流程。

## 当前 Skill

- `requirement-consistency-check/`：在 `development-readiness` 之前的独立前置步骤，按顺序核对需求文档自身是否前后一致、需求文档与原型图是否对得上、需求文档+原型图与设计稿是否有明显出入，帮助需求相关方在开发前就"以哪个为准"达成一致。
- `development-readiness/`：让临时接手陌生仓库的开发者，在编码前达到「真实、可验证、可交接」的开发就绪状态（项目熟悉 + 环境验证 + 任务就绪），并产出跨会话交接文档。
- `design-fidelity-repair/`：在有蓝湖链接或设计稿图片的 UI 实现基本完成后，通过原始设计源、浏览器截图和独立视觉诊断发现并自动修复明显设计还原偏差。
- `testing/`：在开发完成后、场景验收前，对照开发就绪阶段确定的验收标准产出可复现的功能测试证据（自动化测试 + 按需浏览器功能测试），不承担截图式视觉判断。
- `scenario-acceptance/`：综合原始需求、项目全局约束、任务契约、代码差异、测试证据与代码 Review 结论，判断最终交付能否验收，并把暴露出的项目事实变化回流给开发就绪阶段。
- `yunxiao-devops-workflows/`：在已接入的云效（Yunxiao）MCP 之上，规范开发前同步需求/任务/评论、发起流水线与排查失败日志、拆任务与登记统计工时这三条高频协同工作流的取证顺序与写操作确认边界。
- `skill-bug-record/`：记录执行某个 skill 时发现的、关于该 skill 定义本身的问题，写入共享日志文件。
- `skill-bug-triage/`：处理 `skill-bug-record` 记录里状态为待处理的问题，判断是否需要修改对应 skill 定义并回写状态。
- `task-issue-record/`：记录开发后发现的任务交付物问题（UI、业务、逻辑、代码质量、技术规范等任意类型），追加进该任务的问题账本，只记录不修复。
- `task-issue-triage/`：处理任务问题账本里状态为待处理的条目，判断是否需要修复、实施修复并逐条即时回写状态（已修复/不修复/延后）。

### 需求核对 → 开发就绪 → 按需设计还原 → 测试 → 场景验收工作流

`requirement-consistency-check` 是这条材料链最前面的一道关卡：只有核对通过（或"有条件可用"且已确认）的需求文档，才适合作为权威材料进入 `development-readiness` 任务的 `raw/` 材料区。两者是独立 Skill、独立产出（`docs/requirement-consistency-check/` 与 `docs/development-readiness/` 各自成文），不引入自动联动，只在文档间做人可读的交叉引用。

主线 Skill 共享同一条材料链，覆盖编码前到验收的全过程；开发实现和代码质量 Review 复用已有能力：

```text
requirement-consistency-check（需求核对）
    ↓
development-readiness（开发就绪）
    ↓
已有开发能力（Coding Agent）
    ├─→ design-fidelity-repair（有设计稿的 UI 任务，按需修复）
    ↓
testing（测试） ──┐
                  ├─→ scenario-acceptance（场景验收）
已有代码 Review ──┘
```

Development Readiness、Testing 与 Scenario Acceptance 用同一个任务 slug 在各自目录下落盘报告
（`docs/development-readiness/`、`docs/testing/`、`docs/scenario-acceptance/`），不引入额外的索引或状态机；初始设计背景见
[`../../docs/superpowers/specs/2026-08-20-readiness-testing-acceptance-handoff.md`](../../docs/superpowers/specs/2026-08-20-readiness-testing-acceptance-handoff.md)
和
[`../../docs/superpowers/specs/2026-08-20-readiness-testing-acceptance-implementation-design.md`](../../docs/superpowers/specs/2026-08-20-readiness-testing-acceptance-implementation-design.md)。

有蓝湖链接或设计稿图片的 UI 任务可在开发后、Testing 前使用 `design-fidelity-repair`。它会修改代码并
单独落盘 `docs/design-fidelity-repair/tasks/<timestamp>-<task>.md`；Testing 仍只验证功能，
Scenario Acceptance 可按任务风险消费该报告。设计见
[`../../docs/superpowers/specs/2026-08-28-design-fidelity-repair-design.md`](../../docs/superpowers/specs/2026-08-28-design-fidelity-repair-design.md)。

### 任务问题账本（不依赖上述工作流被调用）

`task-issue-record` / `task-issue-triage` 为「开发后发现的问题」提供一个不挑问题类型、不依赖特定开发
工作流的落点：不管开发过程是否用了上面的主线 Skill，都可以随时记录和处理。账本文件与 `development-readiness`
的任务报告同处 `docs/development-readiness/tasks/`（单独用 `issues/` 子目录，与既有的 `raw/` 子目录同级），
但命名只跟任务走（`issues/<task-id-or-slug>.md`，不带会话时间戳），因为它是跨会话、长期追加的文档，与
「一次会话一份报告」的模式不同。设计背景见
[`../../docs/superpowers/specs/2026-08-28-task-issue-ledger-and-ui-baseline-design.md`](../../docs/superpowers/specs/2026-08-28-task-issue-ledger-and-ui-baseline-design.md)。

## 放置规则

每个 Skill 应包含：

- `SKILL.md`：含 frontmatter（`name` + 用于自动触发的 `description`），正文为常驻精简内核与「何时读哪个 reference」的指针。
- 按需加载的 `reference/`（可选）：把详细清单、协议、模板等大部头下沉，保持 `SKILL.md` 精简。
- 使用说明、输入要求、输出格式、已知限制（可放在配套 `README.md`）。

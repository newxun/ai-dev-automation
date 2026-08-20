# Skills

这里存放面向真实研发场景的 Agent Skill：在**主对话**里被模型按需加载并遵循的可复用工作流与方法，遵循 Augment Humans, Not Replace Humans。

## 何时用 Skill（而不是 Agent / Subagent）

- **Skill**：人在环、跨轮、可能跨会话的主线工作流——需要向用户提问、请求确认、逐步交接。以渐进披露组织：常驻的只是精简内核与触发描述，细节按需读入。
- **Subagent**：非交互、隔离、自治的专项子任务——边界明确、可独立调查、无需用户确认（如复杂依赖冲突、特定构建失败、独立历史调查）。适合被主线工作流在需要时派发，而不是承载整个人在环流程。

## 当前 Skill

- `development-readiness/`：让临时接手陌生仓库的开发者，在编码前达到「真实、可验证、可交接」的开发就绪状态（项目熟悉 + 环境验证 + 任务就绪），并产出跨会话交接文档。
- `testing/`：在开发完成后、场景验收前，对照开发就绪阶段确定的验收标准产出可复现的测试证据（自动化测试 + 按需浏览器功能测试 + 条件化视觉测试）。
- `scenario-acceptance/`：综合原始需求、项目全局约束、任务契约、代码差异、测试证据与代码 Review 结论，判断最终交付能否验收，并把暴露出的项目事实变化回流给开发就绪阶段。
- `yunxiao-devops-workflows/`：在已接入的云效（Yunxiao）MCP 之上，规范开发前同步需求/任务/评论、发起流水线与排查失败日志、拆任务与登记统计工时这三条高频协同工作流的取证顺序与写操作确认边界。

### 开发就绪 → 测试 → 场景验收工作流

三个 Skill 共享同一条材料链，覆盖编码前到验收的全过程；开发实现和代码质量 Review 复用已有能力，不在这三个 Skill 中重做：

```text
development-readiness（开发就绪）
    ↓
已有开发能力（Coding Agent）
    ↓
testing（测试） ──┐
                  ├─→ scenario-acceptance（场景验收）
已有代码 Review ──┘
```

三者用同一个任务 slug 在各自目录下落盘报告（`docs/development-readiness/`、`docs/testing/`、`docs/scenario-acceptance/`），不引入额外的索引或状态机；设计背景与已决策项见
[`../../docs/superpowers/specs/2026-08-20-readiness-testing-acceptance-handoff.md`](../../docs/superpowers/specs/2026-08-20-readiness-testing-acceptance-handoff.md)
和
[`../../docs/superpowers/specs/2026-08-20-readiness-testing-acceptance-implementation-design.md`](../../docs/superpowers/specs/2026-08-20-readiness-testing-acceptance-implementation-design.md)。

## 放置规则

每个 Skill 应包含：

- `SKILL.md`：含 frontmatter（`name` + 用于自动触发的 `description`），正文为常驻精简内核与「何时读哪个 reference」的指针。
- 按需加载的 `reference/`（可选）：把详细清单、协议、模板等大部头下沉，保持 `SKILL.md` 精简。
- 使用说明、输入要求、输出格式、已知限制（可放在配套 `README.md`）。

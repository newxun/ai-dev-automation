# AI Assisted Code Review

## Problem

Code Review 容易受到时间、上下文切换和审查者经验差异影响。AI 可以帮助快速整理改动、发现明显风险、生成审查初稿，但不能替代开发者对业务语义、架构取舍和发布风险的判断。

## Goal

用 AI 辅助 Code Review 的前置分析，帮助开发者：

- 更快理解变更范围。
- 发现潜在缺陷和遗漏测试。
- 生成结构化 Review 初稿。
- 保留人工最终判断。

## Use Cases

- 审查中小型 Pull Request。
- 在提交前自查自己的改动。
- 为复杂变更整理风险点。
- 为 Review 评论生成更清晰的表达初稿。

## Usage

### Inputs

准备以下上下文：

- 变更 diff。
- 相关需求或 issue。
- 关键测试输出。
- 需要特别关注的风险，例如兼容性、性能、安全、数据迁移。

### Steps

1. 让 AI 总结变更范围。
2. 让 AI 按风险类别检查 diff。
3. 使用 `artifacts/checklists/ai-assisted-code-review.md` 做人工复核。
4. 让 AI 生成 Review 评论初稿。
5. 开发者确认哪些评论真实有效、哪些需要删除或改写。

### Suggested Prompt

```text
你是一位谨慎的 Code Reviewer。请基于下面的 diff 和上下文进行审查。

目标：
1. 先总结本次变更做了什么。
2. 找出可能导致 bug、回归、测试缺失或可维护性问题的点。
3. 只提出有明确依据的问题，不要为了评论而评论。
4. 每个问题都包含：位置、风险、原因、建议。
5. 最后列出你不确定、需要人工确认的点。

上下文：
<粘贴需求、issue、设计说明或约束>

Diff：
<粘贴 diff>
```

## Human Confirmation

必须由开发者确认：

- AI 指出的风险是否真实存在。
- 评论是否符合项目上下文和团队约定。
- 是否需要阻塞合并。
- 是否需要补充测试或回滚设计。

## Limitations

- AI 可能误判业务语义。
- AI 可能忽略跨仓库、运行时配置或部署环境影响。
- AI 对未提供的上下文没有可靠判断能力。
- AI 生成的评论需要人工筛选，不能直接批量发布。

## Downstream Consumption

[`scenario-acceptance`](../../skills/scenario-acceptance/README.md) Skill 会消费本工作流的输出作为验收阶段「代码质量」证据的一部分。它不要求固定 Schema，但需要能看出：

- 结论来源（谁、以什么方式审查）。
- 是否存在阻塞问题及具体内容（对应下方 Merge Readiness 小节的「阻塞问题」）。
- 审查所基于的代码版本（diff 对应的 Commit 或 PR 版本）。

保留这三类信息（哪怕只是 PR 评论或口头小结），场景验收阶段就能直接引用，不需要重新审查；缺失时会被记为证据缺口，按任务风险决定是否阻断验收。

## Roadmap

- 补充不同类型 PR 的 Review Prompt，例如 bugfix、refactor、migration。
- 增加安全、性能、测试覆盖专项检查清单。
- 记录真实 Review 案例和误报案例。
- 评估不同模型在 Code Review 场景下的表现。
- 如果本工作流未来演进为结构化 Skill，重新校准与 Scenario Acceptance 的消费接口。

## Example Output Format

```text
Summary:
- ...

Findings:
1. [Severity] file:line
   Risk:
   Reason:
   Suggestion:

Open Questions:
- ...
```

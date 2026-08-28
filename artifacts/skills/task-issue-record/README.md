# Task Issue Record

## Problem

开发后自行走查、或用户在使用过程中发现的问题（不限 UI，也包括业务、逻辑、代码质量、技术规范）经常
没有稳定落点。以往常用临时 review 文档记录，事后找不到；开发阶段也常常使用「开发完就结束」的工作流，
不会自动产出可复盘的问题清单。

## Goal

为「开发后发现的各类问题」提供一个稳定、可追加、可复盘的落点：只记录事实，不做裁决或修复，交给
[`task-issue-triage`](../task-issue-triage/README.md) 之后处理。

记录与处理拆成两个 Skill，是因为记录侧的边界必须绝对干净——如果同一个 Skill 既能记录又能修复，AI
在记录过程中会倾向于「顺手修掉」，导致账本残缺，而账本残缺正是「事后无法复盘」的根因。设计背景见
[`../../../docs/superpowers/specs/2026-08-28-task-issue-ledger-and-ui-baseline-design.md`](../../../docs/superpowers/specs/2026-08-28-task-issue-ledger-and-ui-baseline-design.md)。

## Use Cases

- 用户在验收或走查时发现一个问题，希望先记下来，不希望 AI 当场顺手修改。
- 一次开发会话结束前，希望把会话中确认未解决的问题归集成清单，供后续处理或复盘。
- 需要跨会话、跨阶段持续追加同一任务的问题，而不是每次都开新文件。

## Not For

- 判断问题是否成立、是否需要修复（属于 `task-issue-triage`）。
- 修复任何代码、配置或文档。
- 记录某个 Skill 定义本身的问题（属于 `skill-bug-record`）。
- 记录工具错误、网络问题、依赖缺失等与任务交付物问题无关的执行失败。

## Contents

- [`SKILL.md`](SKILL.md)：Skill 入口，含硬边界、账本落点、条目格式与工作流。

## Usage

### Claude Code / Codex 等

将本目录放到能发现 Skill 的位置，或作为主 Agent 指令直接使用；需要文件读写能力。

### Suggested Start

用户主动提出问题：

```text
刚才验收时发现一个问题：<描述现象、涉及位置>。请记进这个任务的问题账本，先不要修。
```

会话结束归集：

```text
这次开发会话到此为止，请从本次会话里归集确认还没解决的问题，记进任务问题账本。
```

## Inputs

必需：

- 问题现象（预期 vs 实际）。
- 涉及位置（文件 / 页面 / 模块）。

可选：

- 任务标识或 slug（未提供且无法从 `development-readiness` 报告推断时会询问）。
- 分类（未提供时按现象自行归类，仍需用户确认）。

## Outputs

```
docs/development-readiness/tasks/<task-id-or-slug>-issues.md
```

与该任务在 `development-readiness` 阶段的任务报告同处一个目录，文件名只跟任务走，不跟会话走；
同一任务的所有问题持续追加进同一份文件。

## Limitations

- 不做严重性、优先级或修复方案的判断。
- 会话归集依赖用户逐条确认，不能替用户判断「是否真的未解决」。
- 不预先假设该任务一定经过 `development-readiness`；没有对应任务报告时需要用户提供任务标识。

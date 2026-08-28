# Task Issue Triage

## Problem

问题被记进账本之后，仍需要有人判断是否成立、是否要修，并且要能只处理一部分、随时中断，而不是要求
一次性处理完才算数；处理过程也不能污染账本里原始记录的现象描述。

## Goal

读取 [`task-issue-record`](../task-issue-record/README.md) 产出的任务问题账本中「待处理」的条目，逐条
判断并实施修复（或说明不修 / 延后的理由），处理一条即回写一条状态，支持只处理部分条目。设计背景见
[`../../../docs/superpowers/specs/2026-08-28-task-issue-ledger-and-ui-baseline-design.md`](../../../docs/superpowers/specs/2026-08-28-task-issue-ledger-and-ui-baseline-design.md)。

## Use Cases

- 开发者在一次会话中集中处理某个任务积累下来的问题清单。
- 只想先处理某一类问题（例如先处理全部「代码质量」条目），其余留到下次。
- 需要对影响面较大的修复先说明方案，再决定是否实施。

## Not For

- 发现和记录新问题（属于 `task-issue-record`）。
- 处理 Skill 定义本身的问题（属于 `skill-bug-triage`）。
- 在没有确凿依据时臆造问题成立与否的判断。

## Contents

- [`SKILL.md`](SKILL.md)：Skill 入口，含硬边界、状态模型、账本落点与工作流。

## Usage

### Claude Code / Codex 等

将本目录放到能发现 Skill 的位置，或作为主 Agent 指令直接使用；需要文件读写、代码搜索能力，涉及修复
时还需要执行必要验证的能力。

### Suggested Start

```text
请处理这个任务问题账本里的待处理条目：<task-id-or-slug>。
```

只处理部分条目：

```text
这次只处理账本里分类为「代码质量」的条目，其余先留着。
```

## Inputs

必需：

- 任务标识或 slug，用于定位 `docs/development-readiness/tasks/<task-id-or-slug>-issues.md`。

可选：

- 本次要处理的子集（分类或具体条目）。

## Outputs

原地更新同一份账本文件：每条被处理的条目状态变为 `已修复` / `不修复` / `延后`，checkbox 打勾，并追加
处理结果与处理时间；未处理的条目保持 `待处理` 不变。

## Limitations

- 不保证一次性清空账本；这是设计上的允许行为，不是缺陷。
- 找不到涉及位置对应的文件时只能标记「不修复」并说明理由，不代为猜测。
- 是否需要用户确认修复方案取决于影响面判断，仍可能出现边界情况需要人工介入判断。

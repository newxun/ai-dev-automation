# Departure Handover

## Problem

Leaving a role means handing over everything you own, and most of it is not written down anywhere. Access, contacts, recurring duties and hard-won traps live only in your head. The repository holds leads about the rest, but they cannot be taken at face value: commit messages are often unconventional, so a message alone does not establish what a branch or change was for, and some branches were quietly abandoned yet look exactly like live ones. On top of that, the full scope of a person's duties is far more than one conversation can cover, so a single-shot write-up either stays shallow or never finishes.

## Goal

Produce handover material a successor can act on alone, built the only way that holds up: **analysis proposes leads with evidence, the departing person rules on each one, and only confirmed items become handover content**. The ledger is incremental and survives across sessions, and every session opens by reporting coverage and what is still open — so "how much of my job is actually handed over" is always answerable.

Design background: [`../../../docs/superpowers/specs/2026-09-22-departure-handover-design.md`](../../../docs/superpowers/specs/2026-09-22-departure-handover-design.md).

## Use Cases

- You have resigned and need to hand over in-flight work, project knowledge, access, contacts, recurring duties and tacit know-how.
- You maintain several repositories and want one ledger covering all of them, with each item labelled by project.
- You want the unfinished branches and stale TODOs under your name surfaced with evidence, so you can decide item by item what is worth handing over and what was always throwaway.
- You need to pick the handover back up next week and know immediately what is left.

## Not For

- Onboarding the successor into the codebase (that is the successor's own problem, with its own tools).
- Implementing, fixing or cleaning up anything before you leave.
- Deciding on your behalf whether a branch, task or duty matters.
- Storing credentials. It records which system and which transfer path, never the secret.
- Consuming or updating other skills' artifacts — it is deliberately independent of `development-readiness` and `project-familiarization-agent`, whose outputs are temporary and may be inaccurate.

## Contents

- [`SKILL.md`](SKILL.md) — entry point: hard boundaries, ledger layout, status model, categories, evidence tags, session loop, sweep protocol.
- [`reference/analysis.md`](reference/analysis.md) — which signals may be trusted and how far, exact read-only commands, dedup keys, re-run rules.
- [`reference/interview.md`](reference/interview.md) — question bank per category, pacing rules, when an item is done.
- [`inventory-template.md`](inventory-template.md) — ledger scaffold: coverage, repositories analysed, per-category tables, ruling log.
- [`item-template.md`](item-template.md) — item scaffold: shared skeleton plus a per-category block.

## Usage

Needs file read/write, shell, git and code search. Invoke it explicitly — it is not auto-triggered.

### Suggested start

First session:

```text
我要离职了，开始做交接盘点。交接目录用 <绝对路径>，先分析这个仓库里我名下的线索。
```

Later sessions:

```text
继续交接，目录在 <绝对路径>。先告诉我还差什么。
```

Producing the deliverable:

```text
把已经写完的交接条目导出成一份文档。
```

## Inputs

Required:

- `<handover-root>` — an absolute path **outside** the work repository. Asked every session, because a ledger outside the repository cannot be discovered.

Optional:

- Which repositories to sweep, and over what time window (defaults to the last 12 months, adjustable to your tenure).
- A connected work-tracking MCP, used to pull open items under your name. Absent, the sweep continues and the gap is recorded.

## Outputs

```text
<handover-root>/
├── inventory.md                  # every swept item: status, evidence, coverage
├── items/<category>-<slug>.md    # one deep-dive per item being handed over
└── export/handover-<date>.md     # on-demand deliverable, assembled from written items
```

`items/` files are the only source of content; `inventory.md` carries status and a one-line summary. The export contains only completed items, plus the coverage figure and an explicit list of what is not yet handed over.

## Limitations

- **Not exhaustive.** Access, contacts, recurring duties and most tacit knowledge leave no trace in code, so they come out of interview. The skill guarantees that what it found carries evidence and that what it could not find is marked `[needs-confirmation]` — not that nothing was missed.
- **Every ruling is yours.** Keep-or-drop calls, and the meaning of any lead, require your confirmation; the skill will not resolve an ambiguous branch by guessing.
- **No successor sign-off.** An item is complete when you say the write-up is complete; the skill does not track whether the successor can actually run it.
- **Path must be supplied each session.** The ledger lives outside the repository by design, which is also why it cannot be auto-located.
- **Read-only against the work repository.** It never modifies or even checks out anything there, so it cannot tidy up branches as part of the handover.

## Roadmap

- A successor-verified tier, if document-only completion proves too weak in practice.
- Rendering the export for sharing outside the repository, if markdown turns out to be inconvenient to hand to a manager.
- A cross-repository index, if the per-item `Project` field stops being enough at scale.

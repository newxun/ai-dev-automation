---
name: skill-bug-triage
description: Review pending entries in the shared skill bug log, decide for each whether the referenced skill's own definition needs a fix, apply the smallest fix when warranted, and write back the resulting status (resolved or dismissed).
disable-model-invocation: true
---

# Skill Bug Triage

Read pending entries from the shared skill-bug log, decide for each whether the referenced skill's definition needs a fix, apply the smallest fix when warranted, and write back the resulting status.

## Hard boundaries

1. A single log entry does not justify rewriting an entire skill; apply only the smallest fix that addresses the reported symptom.
2. If a fix would change an existing hard boundary, trigger scope, or established workflow, present the proposed change and wait for confirmation before applying it. Typos, ambiguous wording, or clearly missing boundaries can be fixed directly.
3. If the referenced skill or file can't be found, or the described scenario no longer applies, mark the entry `dismissed` with a reason instead of guessing at a target file.
4. Never delete existing entries. Only update the status in the heading and append a resolution.
5. It's fine to process only some entries (one skill, or specific entries) in a single pass; leave the rest `pending`.

## Log location

```
${XDG_STATE_HOME:-$HOME/.local/state}/skill-bugs/log.md
```

## Status and resolution

Each entry's heading is `<timestamp> · <skill name> · <status>`, where status is `pending` / `resolved` / `dismissed`. When closing an entry, update the status and append:

```markdown
- Resolution: <resolved: what changed and in which file; or dismissed: why no fix is needed>
- Resolved at: <UTC timestamp>
```

## Workflow

1. Read the log file and list all `pending` entries grouped by skill. If the file is missing or empty, say so and stop.
2. Confirm scope for this pass (default: all pending entries, or a subset named by the user).
3. For each entry, locate the relevant file in the referenced skill (`SKILL.md` or `reference/**`), identify the root cause, and decide: fix / dismiss / needs confirmation.
4. Apply the fix directly when it doesn't fall under boundary 2; otherwise present the plan first.
5. Update that entry's status and resolution immediately after handling it, rather than batching updates at the end.
6. Summarize what was fixed, what was dismissed and why, and how many entries remain pending.

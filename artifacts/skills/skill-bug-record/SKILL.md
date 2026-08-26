---
name: skill-bug-record
description: Log an issue found in another skill's own definition (unclear instructions, missing boundaries, missing reference coverage, inaccurate trigger conditions, output mismatch) as a new entry in a shared log file for later triage. Does not judge whether the issue needs fixing and does not modify any skill file.
disable-model-invocation: true
---

# Skill Bug Record

Append one issue about another skill's own definition to a shared log, for `skill-bug-triage` to process later. Only record the facts; never decide whether or how to fix anything.

## What to record

- Instructions or boundaries that were unclear and forced guessing during execution
- A trigger description that misfired (fired when it shouldn't have, or didn't fire when it should have)
- A scenario not covered by any reference file, resolved with an ad hoc judgment call
- Output that didn't match what a downstream consumer (user or another skill) expected
- A hard boundary or confirmation protocol that turned out missing, redundant, or contradictory

Do not record tool errors, network issues, missing dependencies, or other execution failures unrelated to the skill's own definition.

## Log location

```
${XDG_STATE_HOME:-$HOME/.local/state}/skill-bugs/log.md
```

Create the file and its parent directory if missing. Append only; never rewrite existing entries.

## Entry format

```markdown
## <UTC timestamp YYYY-MM-DDTHH-mm-ssZ> · <skill name> · pending

- Symptom: <what was done, what was expected, what actually happened>
- Likely cause: <unclear instructions / missing boundary / missing reference / inaccurate trigger / output mismatch / other (explain)>
- Note: <optional context to help relocate this later>
```

New entries always use status `pending`.

## Workflow

1. Confirm which skill and what symptom you're recording; ask rather than guessing if either is unclear.
2. Read (or create) the log file and append one new entry in the format above, using a freshly generated timestamp.
3. Show the entry you just appended.

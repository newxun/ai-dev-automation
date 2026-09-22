# `<item name>`

> One item of the handover. Shared skeleton first, then the section for this item's category.
> Keep the departing person's own wording for intent, history and risk.

- Category: `wip` | `project` | `access` | `people` | `routine` | `tacit`
- Project: `<which repository / system / team this belongs to>`
- Status: `confirmed` | `written`
- Successor: `<name, or "undecided">`
- Key: `<dedup key from the inventory>`
- Last updated: `<YYYY-MM-DD>`

## What it is, and why it exists

`<Plain description a successor can repeat back. Include why it exists at all — that is usually the part nobody else knows.>`

## Current state

`<Where things stand right now. What works, what does not, what was never started.>`

## What the successor has to do

1. `<Actionable step, not a goal.>`
2. `<…>`

## Risks and traps

- `<What breaks when touched, and the safe path around it. Tag each line with its evidence: [confirmed] / [lead-git] / [lead-file].>`

## Evidence and entry points

- `<path / branch / task ID / link>` — `<why it matters>`

## Open points

- `<Anything still unresolved, including "I don't remember". Do not smooth these over — an honest gap is more useful to the successor than a confident guess.>`

---

## Category section

Keep only the block matching this item's category; delete the rest.

### `wip`

- Progress: `<what fraction is real, and what "done" would mean>`
- Next step: `<the single next action>`
- Blockers: `<what is stuck, and who unblocks it>`
- Deadline or commitment: `<date, promise, external dependency — or "none">`
- Do not do: `<what a successor should avoid doing to this work>`

### `project`

- Module boundaries: `<what is in scope, what is explicitly not>`
- Run and verify: `<commands, required services, config, fastest safety check>`
- High-risk areas: `<paths and why>`
- Historical compromises: `<the choice, and the constraint that forced it>`
- Dead or compatibility-only parts: `<what can be ignored, what must be kept>`
- Others who touch it: `<people or teams>`

### `access`

- System: `<name>`
- Privilege level needed: `<same as mine / lower — be specific>`
- Transfer or re-application path: `<who approves, what the request looks like>`
- Credential location: `<password manager entry, ops platform, teammate — a pointer only>`
- Deadline: `<what breaks when my account is disabled, and when>`

> Never record a password, key, token or private key here. A pointer only.

### `people`

- Who: `<name, role, team>`
- They own: `<scope>`
- Come to them for: `<topics>`
- They need from us: `<expectations, running commitments>`
- Channel and cadence: `<how contact actually happens>`

### `routine`

- Trigger: `<schedule or event>`
- Steps: `<what actually has to be done>`
- Done correctly looks like: `<observable result>`
- If missed: `<consequence, and who notices>`
- Could be automated or dropped instead: `<yes/no and why>`

### `tacit`

- Symptom: `<what someone would see>`
- Cause: `<as far as known — mark uncertainty rather than hiding it>`
- Safe path or workaround: `<what to do instead>`
- Do not touch without asking: `<what, and who to ask>`

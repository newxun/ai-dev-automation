# Handover Inventory

> Ledger of everything to hand over. Status and a one-line summary live here; the content lives in `items/`.
> Statuses: `candidate` (a lead, unconfirmed) · `confirmed` (will be handed over) · `dropped` (will not, reason kept) · `written` (item file complete).

- Handover root: `<absolute path to this directory>`
- Owner: `<departing person, and the git identities counted as theirs>`
- Last updated: `<YYYY-MM-DD>`
- Successor: `<name, or "undecided">`
- Handover deadline: `<YYYY-MM-DD>`

## Handover order

Set in the opening interview and confirmed by the user. One section at a time; mark the current one.

| # | Section | State |
| --- | --- | --- |
| 1 | `<e.g. project: m-ai-vrm>` | `current` |
| 2 | `<e.g. wip: m-ai-vrm>` | `pending` |
| 3 | `<e.g. access>` | `pending` |

## Coverage

- Written: `<n>` / `<total − dropped>` (`<pct>`%) — `<arithmetic from counted rows, e.g. 3 / (56 − 31) = 3 / 25 = 12%>`
- Open: `<n>` confirmed awaiting write-up, `<n>` candidates awaiting a ruling
- Dropped: `<n>`

## Repositories analysed

| Repository | Mainline(s), user-confirmed | Analysed from | Last analysed | Baseline commit |
| --- | --- | --- | --- | --- |
| `<path or remote>` | `<e.g. origin/master, origin/develop>` | `<window, e.g. last 12 months>` | `<YYYY-MM-DD>` | `<short SHA>` |

## Sources not covered

- `<e.g. work-tracking MCP not connected in any session so far — in-flight items from it were never swept>`

## Items

One table per category. Add a row per item; never delete a row — `dropped` rows exist so re-runs stop re-asking.

### wip — in-flight work

| Item | Status | Tag | Key | File | One-line summary / reason |
| --- | --- | --- | --- | --- | --- |
| `<short name>` | `candidate` | `[lead-git]` | `git:branch:<name>` | — | `<what the evidence shows, not what it means — e.g. "master: patch-equivalent (git cherry); develop: not-absorbed">` |

### project — project and code knowledge

| Item | Status | Tag | Key | File | One-line summary / reason |
| --- | --- | --- | --- | --- | --- |
| `<module>` | `confirmed` | `[confirmed]` | `git:path:<path>` | `items/project-<slug>.md` | `<what a successor gets from this item>` |

### access — access and accounts

| Item | Status | Tag | Key | File | One-line summary / reason |
| --- | --- | --- | --- | --- | --- |
| `<system>` | `candidate` | `[needs-confirmation]` | `manual:access:<slug>` | — | `<privilege level and transfer path, never the credential>` |

### people — contacts and relationships

| Item | Status | Tag | Key | File | One-line summary / reason |
| --- | --- | --- | --- | --- | --- |

### routine — recurring duties

| Item | Status | Tag | Key | File | One-line summary / reason |
| --- | --- | --- | --- | --- | --- |

### tacit — tacit know-how

| Item | Status | Tag | Key | File | One-line summary / reason |
| --- | --- | --- | --- | --- | --- |

## Ruling log

Optional, one line per decision that would otherwise be hard to reconstruct — mainly drops and reversals.

| Date | Item | Ruling | Reason |
| --- | --- | --- | --- |
| `<YYYY-MM-DD>` | `<item>` | `dropped` | `<why it is not handed over>` |

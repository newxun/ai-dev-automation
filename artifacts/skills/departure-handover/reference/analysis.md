# Analysis Reference

How to mine a work repository for handover leads. Everything here produces `candidate` rows only.

## Ground rule

Signals differ in how much they can be trusted, and none of them is ground truth. Order of preference, most objective first:

1. **Which paths the person changed, how often, how recently.** Structural and hard to misread.
2. **Branch and PR state**: merged or not, last commit date, whether the remote still exists.
3. **Ownership hints**: `CODEOWNERS`, reviewer patterns, directory conventions.
4. **Markers attributed by blame**: `TODO`, `FIXME`, `HACK`, `XXX`, workaround and compatibility notes written by that person.
5. **Free text**: commit messages, code comments, repo docs. Attach them as recall aids with a link, and say where they came from. Never use them to state what a branch or change "is for" — commit hygiene varies, text goes stale, and a plausible message is not evidence.

Whatever the signal, the row stays `candidate` until the user rules on it.

## Step 0 — establish identity and scope

People commit under several names and emails, so do not assume one.

```bash
git config user.name && git config user.email
git log --format='%an <%ae>' | sort | uniq -c | sort -rn | head -30
```

Show the list and ask which identities are theirs. Then ask which repository (or repositories) this sweep covers, and record each one in the `Repositories analysed` section of `inventory.md`.

Also capture the baseline for the sweep, so a later re-run can be honest about what changed:

```bash
git rev-parse --abbrev-ref HEAD && git rev-parse --short HEAD && git status --short
```

## Step 1 — change hotspots → `project` candidates

```bash
# files, by number of the person's commits touching them
git log --author="<email>" --since="<n> months ago" --name-only --format='' \
  | grep -v '^$' | sort | uniq -c | sort -rn | head -50

# same, rolled up to a module level
git log --author="<email>" --since="<n> months ago" --name-only --format='' \
  | grep -v '^$' | cut -d/ -f1-2 | sort | uniq -c | sort -rn | head -30

# when a hotspot was last touched, and by whom
git log -1 --format='%ad %an' -- <path>
```

Group nearby paths into one candidate per module rather than one per file — the unit of handover is "this module and what I know about it", not a file list. Default window is 12 months; ask the user if a different window fits their tenure.

## Step 2 — branches and PRs → `wip` candidates

Every branch is a candidate, with the same three facts attached and no interpretation:

```bash
git for-each-ref --sort=-committerdate \
  --format='%(refname:short) | %(committerdate:iso8601) | %(authorname)' \
  refs/heads refs/remotes/origin

git branch -a --no-merged origin/<default-branch>      # not yet in the mainline
git merge-base --is-ancestor <branch> origin/<default-branch>; echo $?   # 0 = already merged
git ls-remote --heads origin <branch>                  # empty = remote branch is gone
```

Present each as: last commit date / merged into mainline or not / remote still present or not. Then ask the user to rule: hand it over, drop it, or the successor must continue it. Do not infer abandonment from age, naming or a silent message — some branches are deliberately discarded and look identical to live ones.

Open PRs, when a forge CLI is available and authenticated:

```bash
gh pr list --author @me --state open --json number,title,updatedAt,headRefName
```

If the CLI is missing or unauthenticated, skip it and note the gap; do not treat its absence as "no open PRs".

## Step 3 — markers by blame → `tacit` and `wip` candidates

```bash
rg -n --glob '!**/{node_modules,dist,build,vendor,.git}/**' \
  'TODO|FIXME|HACK|XXX|workaround|deprecated|临时|兼容|先这样'

git blame -L <line>,<line> --porcelain -- <file> | head -3   # attribute a hit
```

Keep only hits attributed to the person's identities. The marker text is a recall aid, not a finding: ask what the note was actually about and whether it still matters.

## Step 4 — ownership hints → `project` and `people` candidates

Search `CODEOWNERS`, `OWNERS`, `.github/` templates and any team docs for their handle. Ownership listed in a file means they were listed, not that they still own it — ask.

## Step 5 — optional MCP enrichment → `wip` candidates

If a work-tracking MCP (for example Yunxiao) is connected in this session, pull items still open under their name — features, tasks, defects — and add them as `[lead-mcp]` candidates with their IDs and links. If nothing is connected, write one line under `Sources not covered` in `inventory.md` saying this source was not swept, and move on. It is never a hard dependency.

## Step 6 — what analysis cannot reach

`access`, `people`, `routine` and most of `tacit` leave no repository trace. Do not fabricate candidates for them from code. Seed them as `[needs-confirmation]` rows straight from the interview bank in `reference/interview.md`.

## Dedup keys for re-runs

A re-run appends only leads that have never been seen. Each row stores a stable key:

| Kind | Key | Note |
| --- | --- | --- |
| Branch or PR | `git:branch:<name>` / `git:pr:<number>` | name is stable; last-commit date is not part of the key |
| Module or path | `git:path:<path>` | store the rolled-up module path, not each file |
| Marker | `git:marker:<path>#<normalised marker text>` | line numbers move, so they are not part of the key |
| Work item | `mcp:<system>:<id>` | |
| Interview-seeded | `manual:<category>:<slug>` | |

Rules:

- Match by key. A key already present in `inventory.md` is never added again, whatever its current status — including `dropped`, which exists precisely to stop the re-asking.
- Rows already `confirmed`, `dropped` or `written` are never overwritten, downgraded or re-ordered by a re-run.
- Re-runs only append to the candidate pool and refresh the `Last analysed` line per repository.
- Report what the re-run added, and say plainly when it added nothing.

## Safety

Read-only analysis only: `git log`, `git blame`, `git for-each-ref`, `git ls-remote`, ripgrep, forge CLI reads. Never check out, fetch-prune, rebase, stash, clean or otherwise touch the work repository's state, and never write a file inside it. Do not print environment variables or scan credential directories; if a secret turns up, record its name and location in the `access` item and nothing more.

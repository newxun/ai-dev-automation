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

## Scope and depth

Analysis serves the current section of the `Handover order`; it is never a whole-repository census run up front.

- **Scope.** Run only the steps that feed the current section, limited to its project. Step 0 is the only step that runs during the opening interview.
- **Metadata tier (candidates).** Leads are listed from metadata only: paths, counts, dates, merge and remote state, marker text. Do not read diffs, file contents or config to describe a candidate.
- **Content tier (confirmed only).** Reading code, diffs, config or docs to fill an item happens only for `confirmed` rows, and only for the item currently being written.
- **Session cap.** At most one section's analysis and 10 new candidate rows per session; a collapsed block of absorbed branches (Step 2) counts as one, and the stash summary does not count. If more leads exist, say how many were held back and leave them for the next pass.

## Delegating exploration to subagents

Exploration is noisy and belongs outside the main conversation, which is reserved for the interview, rulings and writing. When the host can spawn subagents, delegate: mainline evidence, absorption checks for branches and stashes, hotspots, marker scans, and content-tier reading for the item being written. Without subagents, run the same steps inline and keep only the returned table.

Every brief states:

- Repository path, confirmed identities, confirmed mainline(s), and the current section's scope.
- The steps to run from this file, and that it is read-only under `## Safety` and the hard boundaries in `SKILL.md`.
- The exact return shape: the table defined by that step, one row per lead, each with its evidence and method (for absorption, the status vocabulary above). A content-tier brief returns facts with file paths for the item's sections.
- What it must not return: raw command output, interpretation of what a branch or change "is for", keep-or-drop suggestions, secret values.

Treat the returned table as leads, exactly like your own analysis. Check that it has the requested shape and that counts match the rows before showing it; re-dispatch rather than patching in guesses.

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

### Mainline(s) — before any merge judgement

"Merged" only means something relative to a mainline, and a repository may have more than one (for example `master` for production plus `develop`, or one long-lived branch per customer or release line). The mainline is itself a lead: list the evidence and let the user confirm.

```bash
git symbolic-ref --short refs/remotes/origin/HEAD            # remote default branch
git for-each-ref --sort=-committerdate --format='%(refname:short)' refs/remotes/origin
git tag --sort=-creatordate --format='%(refname:short)' | head -20
git branch -r --contains <recent-release-tag>                # which lines carry releases
git log --merges --first-parent --format='%s' <candidate> | head -20   # what gets merged into it
```

Present each candidate mainline with its evidence (default branch, carries release tags, receives merges, age) and ask which ones count. Record the confirmed mainline(s) per repository in `Repositories analysed`. No branch or stash gets an absorption status until this is confirmed.

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

Only branches and stashes in the current section's repository are examined. Each gets an **absorption status per confirmed mainline** — whether its content already reached that mainline by any route, not only by merge.

Priority: a branch whose content reached **no** mainline is the most likely handover candidate, so it gets the attention. Branches absorbed into at least one mainline are secondary. Stashes get a light check only.

### Absorption status (fixed vocabulary)

| Status | Meaning |
| --- | --- |
| `merged` | The tip is an ancestor of the mainline. |
| `patch-equivalent` | Not an ancestor, but every commit has a patch-identical counterpart in the mainline (cherry-picked or rebased). |
| `content-absorbed` | Commits do not match one-to-one, but merging it into the mainline would change nothing (typically a squash merge). |
| `partial` | Some commits have counterparts, others do not — report `n/m`. |
| `not-absorbed` | None of the above. |
| `undetermined` | The check could not decide (tool unavailable, conflicts, hand-edited after picking). Never round this to another status. |

Use only these values. Every status carries the method that produced it. The commands below are the reference methods; an equivalent read-only method is acceptable if it is named in the result.

```bash
# inventory: last commit, author, remote still present
git for-each-ref --sort=-committerdate \
  --format='%(refname:short) | %(committerdate:iso8601) | %(authorname)' \
  refs/heads refs/remotes/origin
git ls-remote --heads origin <branch>                              # empty = remote gone

# merged
git merge-base --is-ancestor <branch> <mainline>; echo $?          # 0 = merged

# patch-equivalent / partial: "-" = has a counterpart upstream, "+" = does not
git cherry <mainline> <branch>

# content-absorbed (git >= 2.38): resulting tree equals the mainline's tree
git merge-tree --write-tree <mainline> <branch>
git rev-parse <mainline>^{tree}
```

Stashes are a light check, not a focus: run only the tree check below, read-only, and no content-tier reading unless the user asks for a specific stash.

```bash
git stash list --format='%H | %gd | %ci | %gs'
git merge-tree --write-tree --merge-base=<stash-sha>^1 <mainline> <stash-sha>   # git >= 2.40; compare with <mainline>^{tree}
git stash show --include-untracked --stat <stash-sha>              # which files, for the row summary only
```

A stash with untracked files (`<stash-sha>^3`) is only partly covered by the tree check; say so in its line.

### Presenting for a ruling

Per repository, in this order:

1. **Not absorbed into any mainline** — every mainline is `not-absorbed`, `partial` or `undetermined`. One row each: name / last commit / author / remote present / status per mainline / method. These come first and are what the session cap is spent on.
2. **Absorbed into at least one mainline** — one collapsed block the user can rule on at once, still showing the status per mainline so "in `develop`, not yet in `master`" stays visible.
3. **Stashes** — one summary line (total, how many absorbed into some mainline). Expand only the unabsorbed ones, one short line each (date, files from `--stat`), and do not count them toward the session cap. They become candidate rows only if the user asks.

An absorbed status is evidence, not a verdict: it does not mean the branch or stash may be dropped, and a `not-absorbed` one is not necessarily live. Ask the user to rule — hand it over, drop it, or the successor must continue it. Do not infer abandonment from age, naming or a silent message — some branches are deliberately discarded and look identical to live ones.

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
| Stash | `git:stash:<sha>` | `stash@{n}` shifts as stashes are added, so use the commit SHA |
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

Read-only analysis only: `git log`, `git blame`, `git for-each-ref`, `git ls-remote`, `git cherry`, `git stash list` / `git stash show`, `git merge-tree --write-tree`, ripgrep, forge CLI reads. (`merge-tree --write-tree` stores unreachable objects that `gc` removes; it changes no ref, index or working tree, so it is allowed.) Never check out, fetch-prune, rebase, apply/pop/drop a stash, clean or otherwise touch the work repository's state, and never write a file inside it. Do not print environment variables or scan credential directories; if a secret turns up, record its name and location in the `access` item and nothing more.

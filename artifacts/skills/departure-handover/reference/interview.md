# Interview Reference

Question bank for extracting what the repository cannot show. Use it in two places: to seed `[needs-confirmation]` rows for the four interview-only categories, and to deep-dive any `confirmed` row into an `items/` file.

## Pacing rules

- One item at a time during a deep-dive. The user is handing over their whole job; a wall of questions gets shallow answers.
- Ask at most three questions per turn, and prefer questions with selectable answers.
- When an answer is "I don't remember", write that down as an open point in the item instead of guessing or filling it with a plausible-sounding reconstruction.
- Stop a deep-dive when the item answers this test: **could a successor act on this without asking the departing person anything?** Not when every field is full.
- Never let an unanswered question block the whole session — park the item, keep its status, move to the next one.

## Seeding questions (interview-only categories)

Ask these once per category during a sweep, and turn each answer into its own `candidate` or `confirmed` row.

### `access`

- Which systems do you sign into for work that a successor will also need? Include the boring ones: VPN, wiki, ticket system, CI, cloud console, database, monitoring, log platform, artifact registry, third-party dashboards.
- Where are you an admin or approver rather than an ordinary user?
- Which service accounts, bot tokens, API keys or certificates are registered under your name or your email?
- For each: who grants or approves it, and where does the credential live today (password manager entry, ops platform, a teammate)?
- Anything that will silently break when your account is disabled — scheduled jobs, alert routing, signed certificates, OAuth apps, mail rules?

Record the system, the privilege level, the transfer or re-application path, and where the credential lives. **Never record the credential itself.**

### `people`

- Who do you talk to regularly outside your own team, and what for?
- Who comes to you for decisions or answers — and about what?
- Which of those questions can only be answered by you today?
- Who is the business owner, the module owner and the release owner for each thing you hold?
- Any relationship with context a newcomer would get wrong — history, sensitivity, a preferred channel?

### `routine`

- What do you do on a schedule: daily, weekly, monthly, per release, per sprint?
- What do you do on a trigger rather than a schedule — an alert, a request, an incident, a monthly close?
- Which scheduled jobs, alerts, reports or dashboards land in your inbox or run under your account?
- For each: what actually has to be done, and what happens if a cycle is missed?
- Which of these does nobody else currently know how to do?

### `tacit`

- What breaks in surprising ways when touched?
- Where is the code deliberately odd, and what is the reason behind it?
- Which incidents shaped how things are done now, and what is the workaround that survived?
- What would a competent newcomer reasonably try that would go wrong here?
- What have you been meaning to warn someone about but never wrote down?
- Which conventions are real but undocumented — naming, branching, deploy timing, who must be told before what?

## Deep-dive questions by category

Use these once a row is `confirmed`, to fill its item file.

### `wip`

- What is this trying to achieve, in one sentence a successor can repeat?
- Where exactly did you stop — what works, what is half-done, what was never started?
- What is the single next step?
- What is blocking it, and who unblocks it?
- Any deadline, commitment or external dependency attached?
- Which branch, task ID, PR or document holds the current state?
- What would you tell the successor **not** to do with it?

### `project`

- What does this module do, and where are its boundaries?
- How do you run and verify it locally — commands, required services, config?
- What is the fastest way to tell whether a change here is safe?
- Which parts are high-risk, and why?
- Which design choices were compromises, and what constraint forced them?
- What here is dead, deprecated, or kept alive only for compatibility?
- Who else touches it?

### `access`

- Exactly what does a successor need: same privilege level, or less?
- Who approves the request, and what does the request look like?
- Is there a hard deadline tied to your account being disabled?
- Anything registered to your personal identity that must be re-registered rather than transferred?

### `people`

- What does this person own, and what do they need from your side?
- Which running conversations or commitments does the successor inherit?
- What is the right channel and cadence?

### `routine`

- What triggers it, and what are the steps?
- What does "done correctly" look like?
- What happens if it is skipped or done late — and who notices?
- Can it be automated or dropped instead of handed over? Say so explicitly if yes.

### `tacit`

- What is the symptom someone would see?
- What is the underlying cause, as far as you know?
- What is the workaround or the safe path?
- What should nobody touch without asking first, and who is "first"?

## Writing the item

Fill `item-template.md` from the answers. Keep the user's own words for anything about intent, history or risk — paraphrasing tacit knowledge is where it gets lost. Mark every unresolved point under `Open points` rather than smoothing it over, then ask the user to confirm nothing is missing before the row becomes `written`.

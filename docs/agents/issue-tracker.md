# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --json number,title,body,labels,comments --jq '{number, title, body, labels: [.labels[].name], comments: [.comments[].body]}'`. `--jq` without `--json` is refused outright -- `cannot use --jq without specifying --json`. For a human read, `gh issue view <number> --comments` on its own is fine.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

**Name the repository; do not rely on inference.** Every command above
works unqualified in the three local checkouts only because each carries
`remote.origin.gh-resolved` in its git config — a per-checkout pin, not a
property of the clone. A fresh clone does not inherit it, and every checkout
here also has `upstream` pointing at `andoma/movian`, so an unpinned `gh` has
a second candidate to choose. Pass `-R Buksa/movian`, export `GH_REPO`, or run
`gh repo set-default Buksa/movian` once per checkout. Reads are as worth
pinning as writes: an unnoticed read of the wrong tracker is a decision made
on someone else's issues.

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external PRs as feature requests; `/triage` reads this flag.)_

When set to `yes`, PRs run through the same labels and states as issues, using the `gh pr` equivalents:

- **Read a PR**: `gh pr view <number> --comments` and `gh pr diff <number>` for the diff.
- **List external PRs for triage**: `authorAssociation` is **not** a `gh pr list --json` field, so that route returns nothing. Go through the API, which does carry it: `gh api 'repos/Buksa/movian/pulls?state=open' --jq '[.[] | {number, title, user: .user.login, assoc: .author_association}]'`, then keep only `CONTRIBUTOR`, `FIRST_TIME_CONTRIBUTOR` or `NONE` and drop `OWNER`/`MEMBER`/`COLLABORATOR`.
- **Comment / label / close**: `gh pr comment`, `gh pr edit --add-label`/`--remove-label`, `gh pr close`.

GitHub shares one number space across issues and PRs, so a bare `#42` may be either — resolve with `gh pr view 42` and fall back to `gh issue view 42`.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a single issue with **child** issues as tickets.

- **Map**: a single issue labelled `wayfinder:map`, holding the Notes / Decisions-so-far / Fog body. `gh issue create --label wayfinder:map`.
- **Child ticket**: an issue linked to the map as a GitHub sub-issue (`gh api` on the sub-issues endpoint). Where sub-issues aren't enabled, add the child to a task list in the map body and put `Part of #<map>` at the top of the child body. Labels: `wayfinder:<type>` (`research`/`prototype`/`grilling`/`task`). Once claimed, the ticket is assigned to the driving dev.
- **Blocking**: GitHub's **native issue dependencies** — the canonical, UI-visible representation. Add an edge with `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`, where `<blocker-db-id>` is the blocker's numeric **database id** (`gh api repos/<owner>/<repo>/issues/<n> --jq .id`, _not_ the `#number` or `node_id`). GitHub reports `issue_dependencies_summary.blocked_by` (open blockers only — the live gate). Where dependencies aren't available, fall back to a `Blocked by: #<n>, #<n>` line at the top of the child body. A ticket is unblocked when every blocker is closed.
- **Frontier query**: `gh issue list --json` exposes **no** blocker field — not `issue_dependencies_summary`, and not `blockedBy` — so the predicate has to come from the API, which returns the summary on every issue in a list read: `gh api 'repos/Buksa/movian/issues?state=open&per_page=100' --jq '[.[] | select(has("pull_request")|not) | {n: .number, assignee: .assignee.login, blocked: .issue_dependencies_summary.blocked_by}]'`. Scope to the map's sub-issues, drop anything with `blocked > 0` or an assignee; first in map order wins.

### Verified on this repository (2026-09-07)

- All five `wayfinder:*` labels exist and were each applied to a real issue and
  removed again. Names and colours mirror `Buksa/movian-plugin-sdk`.
- **Native dependencies are available here**, so the `Blocked by:` body
  fallback above does not apply. A round trip was run end to end: the POST
  recipe works, and the blocker's numeric database id really is required --
  `#number` is not accepted.
- **`issue_dependencies_summary` lags a write.** Read immediately after
  creating an edge it still reports `blocked_by: 0`, and catches up within a
  few seconds. A session that adds a blocker and then runs the frontier query
  in the same breath will see the ticket as unblocked and hand it out. The
  authoritative read is the list endpoint, correct immediately:
  `gh api repos/<owner>/<repo>/issues/<n>/dependencies/blocked_by`. Use the
  summary for a bulk frontier scan, the list endpoint whenever a write just
  happened.

- **Claim**: `gh issue edit <n> --add-assignee @me` — the session's first write.
- **Resolve**: `gh issue comment <n> --body "<answer>"`, then `gh issue close <n>`, then append a context pointer (gist + link) to the map's Decisions-so-far.

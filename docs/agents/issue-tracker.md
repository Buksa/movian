# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --json number,title,body,labels,comments --jq '{number, title, body, labels: [.labels[].name], comments: [.comments[].body]}'`. `--jq` without `--json` is refused outright -- `cannot use --jq without specifying --json`. For a human read, `gh issue view <number> --comments` on its own is fine.
- **List issues**: `gh issue list --state open --limit 200 --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters. **`--limit` is not optional here**: it defaults to 30 and truncates in silence — `gh issue list --state all` returns 30 where the same command with `--limit 200` returns 118. For a listing that must be exhaustive rather than merely large, use the paginated API form under Frontier query below.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

**Name the repository; do not rely on inference.** An unqualified `gh`
resolves through `remote.origin.gh-resolved`, which is per-checkout git config
that a fresh clone does not inherit. This project's clones commonly carry an
`upstream` remote pointing at the fork's parent, so an unpinned `gh` has a
second candidate to choose and no reason to prefer either. Pass
`-R Buksa/movian`, export `GH_REPO`, or run `gh repo set-default Buksa/movian`
once per checkout. Reads deserve pinning as much as writes: an unnoticed read
of the wrong tracker is a decision made on someone else's issues.

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external PRs as feature requests; `/triage` reads this flag.)_

When set to `yes`, PRs run through the same labels and states as issues, using the `gh pr` equivalents:

- **Read a PR**: `gh pr view <number> --comments` and `gh pr diff <number>` for the diff.
- **List external PRs for triage**: `authorAssociation` is **not** a `gh pr list --json` field, so that route returns nothing. Go through the API, which carries `author_association`, and **exclude the internal values** rather than listing the external ones: `gh api --paginate --slurp 'repos/Buksa/movian/pulls?state=open&per_page=100' | jq '[.[][] | select(.author_association | IN("OWNER","MEMBER","COLLABORATOR") | not) | {number, user: .user.login, assoc: .author_association}]'` — paginated for the same reason as the frontier query below, and piped to an external `jq` for the same reason too. The enum is `OWNER, MEMBER, COLLABORATOR, CONTRIBUTOR, FIRST_TIME_CONTRIBUTOR, FIRST_TIMER, MANNEQUIN, NONE` -- an allow-list silently drops `FIRST_TIMER` and `MANNEQUIN`, which are exactly the outside submissions this is looking for, and would drop any value GitHub adds later.
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
- **Blocking**: GitHub's **native issue dependencies** — the canonical, UI-visible representation. Add an edge with `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`, where `<blocker-db-id>` is the blocker's numeric **database id** (`gh api repos/<owner>/<repo>/issues/<n> --jq .id`, _not_ the `#number` or `node_id`). GitHub reports `issue_dependencies_summary.blocked_by` (open blockers only — the live gate). A ticket is unblocked when every blocker is closed. The upstream template offers a `Blocked by: #<n>` body line for trackers without native dependencies; it is deliberately **not** carried here, because this repository has them (verified below) and a frontier query that read the summary would silently ignore such a line if anyone wrote one.
- **Frontier query**: read the predicate from the API, not from
  `gh issue list`. `--json` there has never exposed
  `issue_dependencies_summary`, and its `blockedBy`/`blocking` fields are
  recent — absent on gh 2.92.0, present on 2.96.0 — so a documented command
  that depends on them silently changes meaning with the CLI version. The API
  returns the summary on every issue in a list read and is exhaustive besides:

  ```sh
  gh api --paginate --slurp \
      'repos/Buksa/movian/issues?state=open&per_page=100' \
    | jq '[.[][] | select(has("pull_request")|not)
           | {n: .number, assignee: .assignee.login,
              blocked: .issue_dependencies_summary.blocked_by}]'
  ```

  Scope to the map's sub-issues, drop anything with `blocked > 0` or an
  assignee; first in map order wins. **Then re-check the winner through
  `.../issues/<n>/dependencies/blocked_by` before claiming it.** The summary
  this scan reads lags a write (see below), and a dispatcher cannot know
  whether some other session added a blocker moments ago — advising the
  *writer* to use the authoritative endpoint does nothing for a reader who
  was not the writer. One extra request on one issue, against handing out a
  blocked ticket.

  Two things this shape exists for. `per_page=100` alone stops at one page,
  so a map wider than that loses children before the scoping step and the
  frontier comes back empty for the wrong reason. And `--slurp` **cannot be
  combined with `--jq`** — gh refuses with "the `--slurp` option is not
  supported with `--jq`" — so `--paginate --jq` emits one result per page
  rather than one array, and the filter has to run in an external `jq` over
  `.[][]`. The endpoint also returns pull requests, which is what the
  `has("pull_request")` guard drops.

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

- **Claim**: `gh issue edit <n> --add-assignee @me` — the session's first write. **This is a convention, not a lock.** GitHub issues take multiple assignees and `--add-assignee` adds rather than tests, so two sessions that scan before either assigns will both succeed and both proceed. The protocol is inherited from `/wayfinder` and is not changed here; with one developer it is a caveat rather than a bug, but do not treat an assignee as proof no one else is working the ticket. Re-read after assigning if two sessions might be live — the session's first write.
- **Resolve**: `gh issue comment <n> --body "<answer>"`, then `gh issue close <n>`, then append a context pointer (gist + link) to the map's Decisions-so-far.

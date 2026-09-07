# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those
roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the
corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

## Provenance

All five exist on `Buksa/movian`. `wontfix` shipped with the repository; the
other four were created on 2026-09-07 and each was verified by applying and
removing it on a real issue, because `gh issue edit --add-label` fails on a
label that does not exist and this table is worth nothing if it names one that
does not.

They are a different axis from the labels that describe work: `wip:*` for
state (`wip:dispatched`, `wip:verifying`, `wip:rework`) and `area:*` /
`type:*` / `priority:*` for subject. A triage role and a `type:` are both
applicable to the same issue.

`/triage` is for issues you did not file. Most issues here were opened by the
maintainer or by an agent working to a DoD and need no triage pass; the labels
are in place for the ones that arrive from outside.

The `wayfinder:*` family that `/wayfinder` needs was created here on
2026-09-07 as well, mirroring `Buksa/movian-plugin-sdk`'s names and colours so
the two repositories read the same. See the wayfinding section of
`issue-tracker.md`.

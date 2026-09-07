# Triage Labels

The engineering skills speak in terms of five canonical triage roles. This file
maps those roles to the label strings this repository actually has.

| Role in the skills | Label here | Meaning |
| --- | --- | --- |
| `needs-triage`    | **not created** | Maintainer needs to evaluate this issue |
| `needs-info`      | **not created** | Waiting on reporter for more information |
| `ready-for-agent` | **not created** | Fully specified, ready for an AFK agent |
| `ready-for-human` | **not created** | Requires human implementation |
| `wontfix`         | `wontfix`       | Will not be actioned |

**Four of the five do not exist on `Buksa/movian`.** That is the state of the
tracker, not an oversight in this table: `gh label list` returns `bug`,
`documentation`, `duplicate`, `enhancement`, `good first issue`, `help wanted`,
`invalid`, `question`, `wontfix`, the `area:*` / `type:*` / `priority:*` /
`wip:*` families, and `security`. `gh issue edit --add-label` fails on a label
that does not exist, so a skill that applies one of the four will error rather
than mislabel.

**Why they were never created.** `/triage` exists for issues you did not file —
incoming bug reports and feature requests. Every issue here was opened by the
maintainer or by an agent working to a DoD, so the flow has never run. The
labels this repository does use describe *work state* (`wip:dispatched`,
`wip:verifying`, `wip:rework`) and *subject* (`area:*`, `type:*`), which is a
different axis from triage role.

**If `/triage` is ever wanted here**, create the four labels first and replace
the right-hand column above with their names. Until then a skill asking for a
triage role has no label to apply, and should say so rather than inventing one.

Sibling repositories differ: `Buksa/movian-plugin-sdk` carries the
`wayfinder:*` family that `/wayfinder` needs, and this one does not.

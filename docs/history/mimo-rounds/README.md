# Round lessons from the retired orchestration pipeline

Seven retrospectives written between 2026-07-20 and 2026-08-02, one per round of
work on issues #129 through #152. Each was extracted after the round's PR merged,
from the merged diff, the issue thread, and the verifier and reviewer reports.

They are kept because they record *why* things in `support/devtools/` are shaped
the way they are — the failure that forced each design, with the PR and commit
that landed it. `146-round-lessons.md` is the clearest example: it explains why
wedge capture is launch-attached and same-session, and what the earlier reactive
`gdb -p <pid>` approach produced instead (empty dumps, lost to a PID race).

## What these are not

The pipeline that produced them — a dispatcher, an orchestrator skill set, a
cross-vendor verifier roster and a set of guard hooks — is **retired**, and none
of it is in this repository. Those files were reviewed on their way here and
fourteen defects were confirmed and left unfixed, because fixing instructions for
a runner nobody runs is not work worth doing. They are preserved on the
`archive/mimocode-pipeline` branch and nowhere else.

So: read these as a record of what happened, not as instructions. Where a digest
names a skill, a dispatch command or a `.mimocode/` path, that thing is on the
archive branch, not here.

## Index

| File | Round | Subject |
| --- | --- | --- |
| `129-131-round-lessons.md` | #129, #131 | verifier evidence auditing; cross-cwd metadata boundaries |
| `135-138-round-lessons.md` | #135, #138 | TypeScript calibration; GLW completion verification |
| `136-round-lessons.md` | #136 | reference-declaration calibration fixtures |
| `139-round-lessons.md` | #139 | the wedge race; why external GDB attach cannot win it |
| `144-145-round-lessons.md` | #144, #145 | mdev lifecycle instrumentation |
| `146-round-lessons.md` | #146 | launch-attached same-session wedge capture (PR #151) |
| `152-round-lessons.md` | #152 | non-creating HTTP prop lookup; diagnostic consumers |

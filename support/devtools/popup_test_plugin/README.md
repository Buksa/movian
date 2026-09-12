# popup_test_plugin

Dev-only fixture for movian#242 (a route parked on a popup must not be
reported ready) and movian#247 (seeding a plugin's own setting). Never
packaged; it lives under `support/` and is loaded with `mdev run -p`.

## Routes

| route | what it does |
| --- | --- |
| `popuptest:clean` | finishes normally — the control |
| `popuptest:blocking` | raises a popup from inside its handler and parks |
| `popuptest:gated` | asks only when the `askFirst` setting is true |

`popuptest:clean` is not decoration. A red on either of the others proves
only that the plugin failed to load unless the clean route is green in the
same run.

## The #247 pair, as two commands

Seeding is a launch-time write, so the evidence is two runs that differ in
exactly one flag. The seeded half alone proves nothing: a route that never
asks looks identical to one whose question was seeded away.

Both halves start from nothing. Run A without the reset and, on the second
execution of the pair, `mdev run` refuses because B's instance is still
alive, `mdev open` then reuses B's SEEDED profile, and the control comes
back exit 0 -- the control passing for the reason it exists to rule out.

The removals are chained with `&&`, not `;`. `mdev stop` exits 0 when
nothing is running, so the clean case is unaffected; when it REFUSES --
a live pid it cannot confirm as ours -- `;` would delete the state file
out from under that process and leave an orphan nothing can stop.

```sh
# A -- no seed: the gate asks, the route parks, the wait refuses (exit 1)
mdev stop --name seed247 && rm -rf /tmp/mdev/seed247
mdev run -p support/devtools/popup_test_plugin --name seed247 popuptest:clean
mdev open --name seed247 popuptest:gated ; echo "exit=$?"

# B -- seeded: same route, same everything else, reaches page-ready (exit 0)
mdev stop --name seed247 && rm -rf /tmp/mdev/seed247
mdev run -p support/devtools/popup_test_plugin --name seed247 \
    --plugin-setting devtools_popup_test:popuptest:askFirst=false \
    popuptest:clean
mdev open --name seed247 popuptest:gated ; echo "exit=$?"

mdev stop --name seed247        # leave nothing behind for the next pair
```

A records that run as:

```
mdev: page not ready after 20s: nav_event_seen=True url='popuptest:gated'
  loading='(void)' title='(void)' (open issued 3 times)
  -- the popup queue could not be read
```

and B as `nodes: 0`, with the seeded file reading `{"askFirst": 0}`.

A's refusal is the right one and its reason is not. There IS a popup
pending, so the count would refuse too, but the count is never reached: an
`mdev open` issued immediately after `mdev run` takes its popup baseline
before the instance's HTTP API is listening, that read fails, and an
unreadable baseline fails closed for the whole call. Filed as movian#249,
with the measurement that separates it -- the same open on the same
instance, once warm, exits 0 in 1s. B is unaffected because a seeded route
publishes a definite `loading = 0` and never reaches the popup check at all.

So the pair still discriminates, in both directions, and for A the outcome
is sound while the diagnosis is not. Do not read A's message as evidence
about the queue.

`--plugin-setting` takes the id from `plugin.json` — `devtools_popup_test`
— and appends the `@dev` the core adds for a `-p` load. The group is the id
passed to `settings.globalSettings()`, here `popuptest`.

## Why this is not a smoke

`support/devtools/mdevlib/smoke.py` says, beside `_ensure_running_with`: a
definition cannot declare a seed, and the relaunch reconciliation is keyed
on the plugin set, so two smokes differing only in their seed would not
relaunch.

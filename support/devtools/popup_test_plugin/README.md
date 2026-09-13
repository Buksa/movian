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

A start URL proves nothing about the plugin. `mdev run` appends it to
Movian's argv and `launch()` returns when the HTTP port appears, so nothing
waits on it: if the plugin failed to load in A and loaded in B, A would
still exit 1 and B still exit 0, and the pair would read as a seeding
difference. So each half OPENS the clean route first, and that open is the
control that the plugin loaded and its routes answer.

```sh
# A -- no seed: the gate asks, the route parks, the wait refuses (exit 1)
mdev stop --name seed247 && rm -rf /tmp/mdev/seed247
mdev run -p support/devtools/popup_test_plugin --name seed247
mdev open --name seed247 popuptest:clean ; echo "clean=$?"   # must be 0
mdev open --name seed247 popuptest:gated ; echo "gated=$?"   # must be 1

# B -- seeded: same routes, same everything else, both reach page-ready
mdev stop --name seed247 && rm -rf /tmp/mdev/seed247
mdev run -p support/devtools/popup_test_plugin --name seed247 \
    --plugin-setting devtools_popup_test:popuptest:askFirst=false
mdev open --name seed247 popuptest:clean ; echo "clean=$?"   # must be 0
mdev open --name seed247 popuptest:gated ; echo "gated=$?"   # must be 0

mdev stop --name seed247        # leave nothing behind for the next pair
```

Read each `exit=` line. A passes only if `clean=0` AND `gated=1`: a `clean`
of 1 says the plugin never loaded, which would make `gated=1` mean nothing.

A records that run as:

```
mdev: page not ready after 20s: nav_event_seen=True url='popuptest:gated'
  loading='(void)' title='(void)' (open issued 3 times)
  -- 1 popup(s) pending, 0 of them already up before this open;
     the route is parked until one is answered
```

and B as `nodes: 0`, with the seeded file reading `{"askFirst": 0}`.

That message names the count, which it could not do until movian#249 was
fixed. Before that, this sequence printed "the popup queue could not be
read" instead -- not because the instance was unreachable, but because
`global/popups` does not exist until something raises a popup, and a 404
and a refused connection both reached `pending_popups` as the same `None`.
An unreadable queue fails closed for the whole wait, by design, so A's
outcome was right and its reason was not.

Both halves of the message are named because they are different facts.
`1 popup(s) pending` is the route parking, which is what A is for; `0 of
them already up before this open` is the attribution, which is what makes
it A's popup rather than a bystander's. B never reaches either, because a
seeded route publishes a definite `loading = 0` and skips the popup check
entirely.

`--plugin-setting` takes the id from `plugin.json` — `devtools_popup_test`
— and appends the `@dev` the core adds for a `-p` load. The group is the id
passed to `settings.globalSettings()`, here `popuptest`.

## Why this is not a smoke

`support/devtools/mdevlib/smoke.py` says, beside `_ensure_running_with`: a
definition cannot declare a seed, and the relaunch reconciliation is keyed
on the plugin set, so two smokes differing only in their seed would not
relaunch.

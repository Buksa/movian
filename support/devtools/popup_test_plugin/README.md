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

```sh
# A -- no seed: the gate asks, the route parks, the wait refuses (exit 1)
mdev run -p support/devtools/popup_test_plugin --name seed247 popuptest:clean
mdev open --name seed247 popuptest:gated ; echo "exit=$?"

# B -- seeded: same route, same everything else, reaches page-ready (exit 0)
mdev stop --name seed247 ; rm -rf /tmp/mdev/seed247
mdev run -p support/devtools/popup_test_plugin --name seed247 \
    --plugin-setting devtools_popup_test:popuptest:askFirst=false \
    popuptest:clean
mdev open --name seed247 popuptest:gated ; echo "exit=$?"
```

A recorded that run as:

```
mdev: page not ready after 20s: ... url='popuptest:gated' loading='(void)'
  -- 1 popup(s) pending, 0 of them already up before this open
```

and B as `title: not asked`, with the seeded file reading `{"askFirst": 0}`
and the plugin logging `askFirst = false`.

`--plugin-setting` takes the id from `plugin.json` — `devtools_popup_test`
— and appends the `@dev` the core adds for a `-p` load. The group is the id
passed to `settings.globalSettings()`, here `popuptest`.

## Why this is not a smoke

`support/devtools/mdevlib/smoke.py` says, beside `_ensure_running_with`: a
definition cannot declare a seed, and the relaunch reconciliation is keyed
on the plugin set, so two smokes differing only in their seed would not
relaunch.

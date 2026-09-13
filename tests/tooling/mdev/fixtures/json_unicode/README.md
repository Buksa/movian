# json_unicode — two parsers, one manifest (movian#250)

A dev plugin whose `plugin.json` spells its `id` and `title` with **uppercase**
hex escapes, so the two parsers that read that file can be compared against
each other.

The core reads it with the C decoder (`src/misc/json.c`, via
`plugins.c:628`) and builds the fqid from the result. Duktape reads the same
bytes again through `JSON.parse(Plugin.manifest)` — handed across at
`plugins.c:726-727`, published at `ecmascript.c:900`. Duktape's hex table is
its own and was never wrong, so it is a genuine second producer rather than a
re-run of the same code.

This is not a gate: it needs a built Movian, and the gate for #250 is the
hermetic C test at `tests/tooling/json/test_json_escapes.c`, which CI compiles
and runs. This fixture is what you run when you want to see the defect the way
a user meets it.

## Running it

```sh
mdev run -p tests/tooling/mdev/fixtures/json_unicode --name ju
mdev open --name ju jsonunicode:test
mdev props --name ju global/navigators/current/currentpage/model/metadata/subtitle --depth 0
mdev props --name ju global/navigators/current/currentpage/model/nodes --depth 3
```

`subtitle` is `AGREE` or `DISAGREE`. The log line prefix also carries the
core's answer on its own — the plugin's own trace is tagged with the fqid the
C parser built.

## Measured

On the stand, same fixture, same manifest, binary rebuilt between the two runs:

```
before (json.c:72 = *s - 'F' + 10)     after (*s - 'A' + 10)
  DISAGREE                               AGREE
  c_fqid=test_json_E@dev                 c_fqid=test_json_J@dev
  duktape_id=test_json_J                 duktape_id=test_json_J
  duktape_title=... test É               duktape_title=... test É
```

The Duktape column does not move: it was right before the fix and right after.
The C column moves to meet it. That is the whole claim of #250 in four lines.

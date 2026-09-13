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

## The second item is the other direction

The page carries a second item bound with `bindVideoMetadata`. It is **not** a
second demonstration of the defect, and calling it one would be wrong.

TMDB is the heaviest consumer of the changed decoder — four entry points, all
`htsmsg_json_deserialize2` (`tmdb.c:257, 315, 430, 575`), every response
through it. The binding reaches them: `page.js:25` → `es_metadata.c:92` →
`metadata_bind_video_info` (`mlp.c:1849`) → the lazy query pipeline → TMDB
over HTTP with the built-in key.

It cannot trigger the defect, and that was **measured, not assumed**: the live
API returns `"title":"Amélie"` as raw UTF-8, with zero `\uXXXX` escapes in
either the search response or `/configuration`. Reaching the defective branch
through TMDB needs a mocked response that escapes uppercase, which #250 lists
separately.

What it proves is blast radius. On the fixed binary:

```
*1/  title = Amélie          <- TMDB's own string, overwriting the item's
     year  = 2001               literal title, with the é intact
     metadata/icons: 6 children, tmdb:image:poster:/... paths resolved
```

The busiest consumer of the decoder still binds metadata end to end, and a
non-ASCII title survives. A fix that damaged ordinary UTF-8 handling would
show here immediately.

Two things not to misread:

- The item's title in the model is `Amélie`, not the `TMDB metadata: Amélie`
  the fixture wrote. That difference is the evidence: the binding replaced it
  with what came back from TMDB.
- Walking that subtree with `mdev props` logs `HTTPSRV 404` lines for the
  icon children, because their names are TMDB paths containing `:` and `/`
  and the prop API cannot address them. That is the reader, not Movian.

This half needs network. The manifest half above does not.

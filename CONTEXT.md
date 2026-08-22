# Movian

A media player and its plugin runtime. This glossary covers the vocabulary of
the **plugin API surface and the tooling that describes it** — the area where
the same word has repeatedly meant two different things and cost real
debugging time.

Terms here are concepts specific to this project. General programming
vocabulary does not belong, however much the project uses it.

## The described surface

**Native module**:
A `duk_function_list_entry` table registered by `ES_MODULE` in
`src/ecmascript/es_*.c`, reachable from plugin code as `native/<name>`.
_Avoid_: builtin, C module

**Core module**:
A JavaScript module shipped in `res/ecmascript/modules/**`, reachable as
`movian/<name>` or by a bare name. Built on top of native modules; what a
plugin usually calls.
_Avoid_: stdlib, runtime module, wrapper

**Artifact**:
`generated/movian-metadata.json` and the `generated/*.d.ts` rendered from it —
the machine-written description of the plugin API. Never edited by hand.
_Avoid_: bundle, declarations (when the generated files are meant)

**Handle**:
A wrapped C pointer that crosses into JavaScript through
`es_get_native_obj()` or `es_resource_get()`. It has no JavaScript shape and
is emitted as a distinct opaque brand per class.
_Avoid_: object, pointer, native object

**Arity**:
The `nargs` field of a `duk_function_list_entry`. It is what the C declares,
not what `Function.length` reports at runtime — every native callable reports
0 there.

## Checking

**Oracle**:
`support/devtools/api-introspector/runtime-api.json` — a captured dump of what
a running Movian actually exposes. A snapshot, not a live query, and therefore
only as good as its **stamp**: an `inputs` block digesting all 49 files the
capture was taken from — the CommonJS modules, the apiversion-1 bootstrap and
the introspector with its manifest, all read from disk at run time, plus the C
the natives are registered in, which the binary carries. Comments and
whitespace between tokens are excluded, so the stamp goes red when a member
could have moved and not when a docblock did. `gen.py --check` refuses an
unstamped or stale oracle rather than comparing against it. `gen.py
--adopt-oracle` is the only way to move the stamp: it takes a fresh capture,
tells one from a copy of the committed file by the `capturedAt` every run
writes, and refuses a capture whose build no longer matches the compiled
sources — `mdev run` launches the binary that is there and does not rebuild.
_Avoid_: runtime API, capture (unqualified)

**Accepted corpus**:
The hand-written, reviewed `.d.ts` files under
`support/devtools/metadata/tests/reference/`. They are calibration inputs and
the answer key against which a derivation is judged — never a second public
type library, and never shipped.
_Avoid_: oracle, reference declarations, fixtures (which are the generated-dts
test inputs, a different thing)

**Gate**:
A check wired into `.github/workflows/gates.yml` that can fail a push. A check
that exists but nothing runs is not a gate.

**Coverage floor**:
The pinned set of artifact members each of which, when removed, must break a
fixture. It measures that the fixtures still bite.

**Smoke (mdev)**:
A declarative scenario under a `smokes/` directory, run by `mdev smoke`. Its
readiness contract requires the navigator `Opening` trace **and** a usable
`global/userinterfaces/ui/framerate` — live frame dispatch.

**Smoke (shell runner)**:
One of `support/*-smoke/run-*.sh`. Its readiness contract is only
`http-server: Listening on port N`; it never asks about GLW. Strictly weaker
than an mdev smoke, and the two are not interchangeable evidence.

**mdev**:
The developer CLI. `bin/mdev` in the plugin SDK is a thin launcher carrying
only `core` and `doctor`; every other subcommand is implemented in the core
checkout that `mdev core` resolves to. So "mdev is out of date" usually means
that core checkout is out of date, not the SDK.

## Derivation

**Derived**:
A fact read out of C or JavaScript source by the generator. Carries a source
anchor: a file and the exact line, verified to still contain the anchor text.

**Curated**:
A fact that cannot be derived, written by hand into a `curated_*.json` sidecar
with the same source anchors and a recorded reason.

**Coercion is not contract**:
The rule that a declared type states what a caller is *meant* to pass, not the
full set the runtime will silently convert. `duk_to_string` accepts anything;
the declaration still says `string`.

**Contested slot**:
An argument index the C body reads in more than one way. Its candidates are
recorded in the artifact and its emitted type stays `any`, because whether the
union is closed is a control-flow property the scan cannot see.

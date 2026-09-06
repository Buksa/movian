# A differential needs a different producer; a census needs an independent enumerator

`gen.py --check` runs several checks that read the source tree a second time
after `build_artifact()` has already read it. An architecture review called
that duplication and proposed unifying the walks. That is right for one check
and wrong for another, and the difference is not obvious from the code, so it
is written down here.

## Two kinds of check, two rules

A **differential** asks whether two sides agree. Its guarantee comes entirely
from where the second side came from: the runtime capture (`_check_runtime_
oracle`), the C accessors the native signatures are derived from, `tsc` in
`check_reference_dts.py`. **Re-running our own scanner is not a second side.**
Both results come from the same code, so the check can only see what that code
already agrees with itself about.

A **census** asks whether we looked at everything. It has no second producer
and does not need one — but its **enumerator must be independent of the
builder**, and is usually cruder. `_check_object_return_coverage` enumerates
with `RETURN_OBJECT_RE` over masked text and then asks which export region
covers each hit; that is how it can report `unattributed`, a site the
generator never read. A site nothing looked at leaves no trace in the
artifact, so nothing else in the build could ever mention it.

## What this removed

`_check_commonjs_shape_coverage` compared a source inventory against an
artifact inventory, and built the source side by calling
`scan_commonjs_exports` and `scan_commonjs_shapes` -- the same two scanners
that built the artifact. It was therefore blind exactly where the scanners
are blind. `cmd_check` already proves the artifact is what the scanners
produce now, by comparing a fresh build against the committed file, so the
only disagreement left for this check to find was between its own two
projection helpers.

It and its two inventories are gone, along with one full corpus walk per
`--check` -- measured at ~0.8s of ~31s, the rest being the `tsc` subprocesses.

That nothing was lost was measured too, not argued. Deleting one prototype
method from the committed artifact -- `movian/page` `Page.appendItem`, exactly
the drift this check existed to notice -- is still caught twice:

    METADATA DRIFT
    RUNTIME ORACLE CROSS-CHECK DRIFT
      module=movian/page shape=Page member=appendItem missing-from=artifact

once by the fresh-versus-committed comparison, and once by the oracle, which
is the producer that makes it a differential at all.

## What this did not remove, and why a coarse census was not built instead

The capability the removal gives up is "a member the scanner never sees". It
is already covered by a real producer: the runtime oracle compares 242 members
and reports drift 0. The residual is 35 members the capture cannot reach,
each named in `RUNTIME_ORACLE_UNREACHABLE` with a reason.

Building a coarse source-side census to cover those 35 was measured rather
than argued. A deliberately crude enumerator over the CommonJS corpus --
`exports.X`, `X.prototype.Y`, `this.Z` over masked text -- reports **12
disagreements with the artifact and all twelve are false**: private instance
state that is deliberately undeclared (the `private-instance-state` class in
`curated_core_module_diagnostics.json`, 38 accounted diagnostics), three
`__proto__` assignments, and five assignments to local variables that the
regex cannot tell from a shape receiver. Zero real findings, and a curation
file would be needed to make it readable -- curation describing the
enumerator's crudeness rather than any fact about the plugin API.

The two sides it would insure against also cannot fail together: the scanner
reads text, the oracle reads a running process. That is what "different
producer" buys.

The honest way to close the residual 35 is to make the capture reach them.
`RUNTIME_ORACLE_UNREACHABLE` says so itself about the six `http.Request`
members: they leave the list "by the introspector attempting the
construction, not by anyone editing the excuse".

## Consequence

A future architecture review will see the corpus walked more than once and
propose unifying it again. For a differential, agree. For a census, the second
walk is the check, and unifying it with the builder deletes the only thing it
could ever find.

# Lifecycle Resource Balance Report

## Analysis model

`support/devtools/gdb/lifecycle_analyze.py` uses two conservative modes.

1. Without a plugin reload marker it compares only binary-inventory
   create/destroy pairs. A partial duration stream or a mismatched pair is
   `UNKNOWN`; it is not a leak claim.
2. For each `plugins_reload_dev_plugin` event it creates a bounded reload
   window. It matches the M7 lifecycle contracts for plugin, ECMAScript plugin,
   ES context, ES resource, and service lifetimes. ES-resource observations are
   deduplicated by captured pointer when available. Missing pointer evidence
   is `UNKNOWN`.

Thread-create breakpoints run in the creator thread. Because that event does
not contain the new thread's TID, creator TIDs are not compared with
thread-exit TIDs. Count mismatches remain `UNKNOWN` with
`identityCorrelation=not-available`.

## Observed reload balance

The durable S3/plugin-reload stream has one reload window. Its observed
counts are:

| Contract | Created | Destroyed | Result |
|---|---:|---:|---|
| plugin | 1 (reload call represents the new plugin) | 1 | `PASS` |
| ECMAScript plugin | 1 | 1 | `PASS` |
| ES context | 1 | 1 | `PASS` |
| ES resource | 5 | 5 | `PASS` |
| service | 1 | 1 | `PASS` |

This is resource evidence for the reload window, not proof that the later
application shutdown is healthy. The analyzer result for the complete S3 run
is `UNKNOWN` because init/fini/thread evidence is incomplete despite the
resource window passing.

The plugin-reload/unload extended scenario has the same balanced window and
an action-level `PASS`; its overall scenario remains `FAIL` due the inferior's
shutdown exit code 8.

## Repeated reload

The three-cycle stream produced three windows:

- window 1: plugin, ECMAScript plugin, ES context, ES resource (5/5), and
  service pairs balanced;
- window 2: the same five pair groups balanced;
- window 3: plugin/ECMAScript/context/service pairs balanced, but the resource
  side lacks complete pointer observations and is `UNKNOWN`.

The repeated run therefore remains `FAIL`/`UNKNOWN` rather than being
promoted to `PASS`. The action contract itself passed all three HTTP reloads;
that is recorded separately.

## Streams without a reload window

| Stream | Resource result | Reason |
|---|---|---|
| Startup | `UNKNOWN` | Duration stop; callout and prop-subscription destroys were not reached. |
| S4 shutdown | `UNKNOWN / NOT_INSTRUMENTED` | No plugin reload window and no complete pair proof. |
| UI reload | `UNKNOWN / NOT_INSTRUMENTED` | UI action does not establish resource teardown. |
| Safe forced error | `UNKNOWN / NOT_INSTRUMENTED` | HTTP 404 recovery is not a resource balance proof. |

Observed thread counts are intentionally not treated as exact identity
balance: startup created 17/exited 4; S3 created 30/exited 50; S4 created
29/exited 49; repeated reload created 33/exited 19. These are evidence gaps in
the current breakpoint contract, not asserted leaks.

## Tests and safety

Analyzer tests cover balanced pairs, incomplete pairs, reload pointer matching,
missing observations, nonmonotonic JSONL, malformed/truncated lines, and
`UNKNOWN` precedence. Real artifacts were validated before analysis. No
missing breakpoint or missing shutdown event is counted as cleanup success.

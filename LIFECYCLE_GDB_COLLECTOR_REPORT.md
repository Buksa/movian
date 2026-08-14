# Lifecycle GDB Collector Report

## Collector contract

`support/devtools/gdb/movian_lifecycle.py` launches the selected Movian binary
through GDB and writes lifecycle observations to a separate JSONL file. The
collector:

- reads the generated binary-bound inventory;
- arms only symbols evidenced in that binary;
- records `seq` and monotonic nanoseconds;
- records `enter`, `create`, and `destroy` observations;
- captures GDB thread ID, OS TID, thread name, inferior number/PID,
  arguments, object pointers, and a bounded stack;
- emits collector, inferior-exit, thread-exit, and wedge records in the same
  ordered stream;
- keeps GDB console text in `movian.log`, never mixed into JSONL;
- records ownership, cleanup outcome, GDB return code, and final process state.

The opt-in attach path is enabled only by `MOVIAN_MDEV_ALLOW_GDB=1`; the
ordinary path leaves the environment unset. Breakpoint deletion is skipped
after an inferior-exit event because GDB removes those breakpoints with the
inferior; this avoids normal-exit register errors while preserving the final
collector record.

## Real GDB evidence

All artifacts below are durable under `/home/uzver/lifecycle-artifacts`.

| Run | JSONL lines | HTTP ready | Important result |
|---|---:|---:|---|
| Startup collection | 248 | yes | Valid stream; duration cleanup left no Movian inferior. Final lifecycle observations were not reached. |
| S3 plugin reload | 298 | yes | GDB return 0; inferior exit code 8 from GLW shutdown; required reload symbols present. |
| S4 shutdown | 211 | yes | Required `app_shutdown`/`main_fini` observations present; inferior exit code 8. |
| UI reload | 220 | yes | HTTP ReloadUI returned 200 and control remained alive; final shutdown failed. |
| Plugin reload/unload | 298 | yes | Reload action and resource marker passed; final shutdown failed. |
| Repeated reload | 371 | yes | Three reload actions and three reload events; GDB returned 1 during final failure. |
| Safe forced error | 209 | yes | Invalid endpoint returned 404 while control remained alive; final shutdown failed. |
| Wedge proof | 122 | yes | One successful launch-attached wedge capture; 146 frames across 16 threads. |

Every real JSONL artifact passed `movian_lifecycle.py validate`; malformed,
truncated, nonmonotonic, and empty-stream cases are covered by unit tests.

## Wedge capture

Durable wedge files:

- `/home/uzver/lifecycle-artifacts/wedge/events.jsonl`
- `/home/uzver/lifecycle-artifacts/wedge/wedge-response.json`
- `/home/uzver/lifecycle-artifacts/wedge/wedge-dump.txt`

Before requesting `SIGSTOP`, the run proved:

- Movian `comm` was `movian`;
- the persistent path was exact and owned by the named profile;
- `/proc/<inferior>/status` reported the launch GDB PID as `TracerPid`;
- HTTP `/api/prop/global` returned 200.

The response reports `status=success`, `threadCount=16`,
`frameCount=146`, and `movianFramePresent=true`. The JSONL wedge record keeps
the request ID, session ID, GDB/inferior PIDs, classification, correlation,
and emergency-eject state.

## Cleanup discipline

The collector verifies command line ownership before process cleanup. The
scenario runs ended with `finalOwnedRemains=false`; a final `/proc` check found
no `movian` matching the disposable persistent paths and no matching GDB
process. The wedge proof was also checked independently after host cleanup.

`cleanup-not-clean:not-owned` in a naturally terminating scenario is retained
as evidence that the host no longer owns the PID; it is not silently converted
to success. The overall scenario status remains `FAIL` when the inferior exits
abnormally.

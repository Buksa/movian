# Lifecycle Scenario Report

## Scenario matrix

The action result and final inferior result are intentionally separate. An
HTTP action can pass while the run remains `FAIL` because a later shutdown
crashes. This distinction prevents a false lifecycle success.

| Scenario | Action evidence | Final result | Cleanup |
|---|---|---|---|
| Startup collection | HTTP ready; 248 valid events | `UNKNOWN` analysis because duration stop did not reach fini evidence | No owned Movian remains |
| S3 plugin reload | Reloaded fixture; resource marker present; required load/unload symbols present | `FAIL`; inferior exit code `[8]` and GLW SIGSEGV during Quit | `finalOwnedRemains=false` |
| S4 graceful shutdown | `app_shutdown` and `main_fini` observed | `FAIL`; inferior exit code `[8]` and GLW SIGSEGV during Quit | `finalOwnedRemains=false` |
| UI reload | ReloadUI HTTP 200; HTTP remained alive; required GLW symbols observed | `FAIL`; final Quit reaches the same shutdown failure | `finalOwnedRemains=false` |
| Plugin reload/unload | Reload HTTP action and fixture resource marker passed | `FAIL`; GDB return 0 but inferior exit code `[8]` | `finalOwnedRemains=false` |
| Repeated reload | Three successful reload actions; three `plugins_reload_dev_plugin` events | `FAIL`; final shutdown/collector termination not clean | `finalOwnedRemains=false` |
| Safe forced error | Invalid endpoint returned HTTP 404; HTTP remained alive | `FAIL`; final Quit reaches the same shutdown failure | `finalOwnedRemains=false` |
| Real wedge proof | Controlled SIGSTOP captured successfully; 146 frames/16 threads; Movian frame present | Expected controlled-stop cleanup result, not a PASS application shutdown | No owned Movian or matching GDB remains |

Durable scenario event and summary files are under
`/home/uzver/lifecycle-artifacts/scenarios`.

## Shutdown failure is baseline behavior

A direct launch of the same debug ELF with `MOVIAN_MDEV_ALLOW_GDB` unset,
`DISPLAY=:0`, and `WAYLAND_DISPLAY=wayland-0` received HTTP Quit and returned
code 8. Its log contains:

```text
Shutdown requested, returncode = 0
Caches flushed
arch stop=0
CRASH: Signal: 11 in thread glw
Fault address ... 0x60 (Address not mapped)
```

The GDB S3/S4 and extended runs show the same GLW-thread failure. The
lifecycle branch does not disguise this as a collector failure or change the
core GLW shutdown behavior. The opt-in hook is therefore not the cause proven
by these runs.

## Scenario implementation

- `run_lifecycle_scenarios.py` retains M7 S3/S4 behavior and uses current
  environment-overridable binary, plugin, and inventory paths.
- `run_lifecycle_extended.py` adds four explicit action contracts:
  `ui-reload`, `plugin-reload-unload`, `repeated-reload`, and
  `safe-forced-error`.
- The extended runner serializes binary HTTP bodies as lengths, archives event
  streams and summaries, and returns nonzero when the overall run is not a
  PASS. It does not return zero merely because an intermediate action passed.

## Status semantics

- `PASS` means the requested evidence and the whole run's cleanup/exit contract
  passed.
- `FAIL` means a required action, ordering, balance, process, or exit contract
  was observed to fail.
- `UNKNOWN` means required evidence was not reached or is not instrumented.
- Missing breakpoints are not cleanup evidence and never establish `PASS`.

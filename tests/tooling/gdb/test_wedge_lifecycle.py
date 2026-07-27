#!/usr/bin/env python3
"""Focused deterministic coverage for issue #146 same-session wedge capture."""

import contextlib
import io
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "support" / "devtools" / "gdb"))

from movian_lifecycle import (  # noqa: E402
    EMERGENCY_EJECT_STATES,
    EmergencyEjectTracker,
    WEDGE_PROTOCOL,
    _gdb_cmdfile,
    all_eject_mandatory_bound,
    build_argparser,
    instance_state,
    is_eject_mandatory,
    run_launch,
    validate_events,
    validate_wedge_event,
)
from support.devtools.mdevlib import smoke  # noqa: E402


class FakeInstance:
    def __init__(self, directory: Path, state: dict, live_pid: int | None = 1234):
        self.dir = directory
        self.name = "exec-146-test"
        self._state = state
        self._live_pid = live_pid

    def load_state(self) -> dict:
        return self._state

    def live_pid(self) -> int | None:
        return self._live_pid

    def owns_pid(self, pid: int) -> bool:
        return pid == self._live_pid


def success_event() -> dict:
    return {
        "seq": 7,
        "monotonicNs": 200,
        "category": "wedge",
        "event": "wedge-capture",
        "symbol": None,
        "trigger": "health-step",
        "classification": "instance-health-wedge",
        "classificationDetail": "GET /api/screenshot/raw failed: 504",
        "classifiedMonotonicNs": 100,
        "correlation": {
            "subsystem": "screenshot-health",
            "resource": "/api/screenshot/raw",
        },
        "emergencyEject": {
            "state": "not-requested",
            "observed": True,
            "requested": False,
            "armed": False,
            "fired": False,
        },
        "session": {
            "id": "session-1",
            "gdbPid": 444,
            "inferiorPid": 1234,
            "attachedAtLaunch": True,
        },
        "remainingThreads": [
            {"gdbId": 1, "name": "movian", "osTid": 1234},
            {"gdbId": 2, "name": "glw", "osTid": 1235},
        ],
        "capture": {
            "status": "success",
            "dumpPath": "/tmp/mdev/exec-146-test/thread-backtrace.txt",
            "threadCount": 2,
            "frameCount": 20,
            "movianFramePresent": True,
            "detail": "captured 20 frames across 2 threads",
        },
        "thread": {"gdbId": 1, "name": "movian", "osTid": 1234},
        "arguments": {},
        "objects": {},
        "stack": [],
    }


class WedgeSchemaTest(unittest.TestCase):
    def test_success_schema_is_jsonl_valid(self) -> None:
        event = success_event()
        self.assertEqual(validate_wedge_event(event), [])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            path.write_text(json.dumps(event) + "\n", encoding="utf-8")
            result = validate_events(path)
        self.assertEqual(result["bad"], [])
        self.assertEqual(result["categories"], {"wedge": 1})

    def test_missing_resource_is_rejected(self) -> None:
        event = success_event()
        event["correlation"].pop("resource")
        self.assertIn(
            "correlation.resource must be a non-empty string",
            validate_wedge_event(event),
        )


class CommandFileTest(unittest.TestCase):
    def test_control_runs_after_inferior_stop(self) -> None:
        control = {
            "requestPath": "/tmp/mdev/exec-146-test/wedge-request.json",
            "responsePath": "/tmp/mdev/exec-146-test/wedge-response.json",
            "sessionId": "session-1",
        }
        path, _ = _gdb_cmdfile(
            "/tmp/collector.py", "/tmp/events.jsonl", "/tmp/inventory.json",
            "/tmp/mdev/exec-146-test", "/tmp/persistent", "/tmp/cache",
            "/tmp/movian", "page:home", None, 0, 0, "",
            "gdb-collector", control=control,
        )
        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
        finally:
            os.unlink(path)
        run_index = lines.index("run")
        command = next(
            line for line in lines
            if line.startswith("movian-lifecycle-wedge-control ")
        )
        self.assertGreater(lines.index(command), run_index)
        tokens = shlex.split(command)
        self.assertEqual(tokens[tokens.index("--session") + 1], "session-1")
        self.assertEqual(
            tokens[tokens.index("--request") + 1], control["requestPath"]
        )


class AttachedCaptureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.bundle = self.directory / "smoke-fail" / "health-test"
        self.bundle.mkdir(parents=True)
        self.control = {
            "protocol": WEDGE_PROTOCOL,
            "sessionId": "session-1",
            "gdbPid": 444,
            "gdbStartTicks": 99,
            "requestPath": str(self.directory / "wedge-request.json"),
            "responsePath": str(self.directory / "wedge-response.json"),
        }
        self.state = {
            "collector": True,
            "collectorControl": self.control,
            "pid": 1234,
        }
        self.inst = FakeInstance(self.directory, self.state)

    def test_success_uses_launch_gdb_via_pidfd_without_reactive_attach(self) -> None:
        sent: list[tuple] = []

        def send_stop(pidfd: int, sig: int) -> None:
            sent.append((pidfd, sig))
            request = json.loads(
                Path(self.control["requestPath"]).read_text(encoding="utf-8")
            )
            dump = Path(request["dumpPath"])
            dump.write_text(
                "capture-status: success\n" +
                "\n".join("#%d frame" % n for n in range(20)) + "\n",
                encoding="utf-8",
            )
            response = {
                "protocol": WEDGE_PROTOCOL,
                "sessionId": request["sessionId"],
                "requestId": request["requestId"],
                "gdbPid": 444,
                "inferiorPid": 1234,
                "status": "success",
                "dumpPath": request["dumpPath"],
                "threadCount": 4,
                "frameCount": 20,
                "movianFramePresent": True,
                "detail": "captured 20 frames across 4 threads",
            }
            Path(self.control["responsePath"]).write_text(
                json.dumps(response), encoding="utf-8"
            )

        with (
            mock.patch.object(smoke, "_gdb_process_matches", return_value=True),
            mock.patch.object(smoke, "_proc_tracer_pid", return_value=444),
            mock.patch.object(smoke.os, "pidfd_open", return_value=99),
            mock.patch.object(
                smoke.signal, "pidfd_send_signal", side_effect=send_stop),
            mock.patch.object(smoke.os, "close"),
            mock.patch.object(smoke.os, "kill") as numeric_kill,
            mock.patch.object(smoke.subprocess, "run") as reactive_attach,
        ):
            result = smoke._capture_wedge_backtrace(
                self.inst,
                self.bundle,
                classification_detail="GET /api/screenshot/raw failed: 504",
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["source"], "launch-attached-gdb")
        self.assertEqual(result["threadCount"], 4)
        self.assertEqual(result["frameCount"], 20)
        # SIGSTOP traveled through the pidfd, never a numeric os.kill.
        self.assertEqual(sent, [(99, signal.SIGSTOP)])
        numeric_kill.assert_not_called()
        reactive_attach.assert_not_called()

    def test_pidfd_open_failure_fails_closed_without_reactive_attach(self) -> None:
        with (
            mock.patch.object(smoke, "_gdb_process_matches", return_value=True),
            mock.patch.object(smoke, "_proc_tracer_pid", return_value=444),
            mock.patch.object(
                smoke.os, "pidfd_open", side_effect=OSError("no pidfd")),
            mock.patch.object(smoke.signal, "pidfd_send_signal") as send_stop,
            mock.patch.object(smoke.os, "kill") as numeric_kill,
            mock.patch.object(smoke.subprocess, "run") as reactive_attach,
        ):
            result = smoke._capture_wedge_backtrace(self.inst, self.bundle)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["source"], "launch-attached-gdb")
        send_stop.assert_not_called()
        numeric_kill.assert_not_called()
        reactive_attach.assert_not_called()

    def test_post_pidfd_revalidation_failure_fails_closed(self) -> None:
        # Pre-check passes (444) but the inferior is no longer traced by the
        # launch GDB once re-checked after the pidfd is open (999): no signal
        # is sent and there is no reactive attach.
        sent: list[tuple] = []
        with (
            mock.patch.object(smoke, "_gdb_process_matches", return_value=True),
            mock.patch.object(
                smoke, "_proc_tracer_pid", side_effect=[444, 999]),
            mock.patch.object(smoke.os, "pidfd_open", return_value=99),
            mock.patch.object(
                smoke.signal, "pidfd_send_signal",
                side_effect=lambda *a: sent.append(a)),
            mock.patch.object(smoke.os, "close"),
            mock.patch.object(smoke.os, "kill") as numeric_kill,
            mock.patch.object(smoke.subprocess, "run") as reactive_attach,
        ):
            result = smoke._capture_wedge_backtrace(self.inst, self.bundle)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["source"], "launch-attached-gdb")
        self.assertEqual(sent, [])
        numeric_kill.assert_not_called()
        reactive_attach.assert_not_called()
    def test_post_pidfd_owns_pid_false_fails_closed(self) -> None:
        # Initial owns_pid passes (liveness check in _capture_wedge_backtrace)
        # but after pidfd opens, the re-check inside _capture_from_attached_gdb
        # fails: no signal is sent and there is no reactive attach.
        sent: list[tuple] = []
        with (
            mock.patch.object(
                self.inst, "owns_pid", side_effect=[True, False]),
            mock.patch.object(smoke, "_gdb_process_matches", return_value=True),
            mock.patch.object(smoke, "_proc_tracer_pid", return_value=444),
            mock.patch.object(smoke.os, "pidfd_open", return_value=99),
            mock.patch.object(
                smoke.signal, "pidfd_send_signal",
                side_effect=lambda *a: sent.append(a)),
            mock.patch.object(smoke.os, "close"),
            mock.patch.object(smoke.os, "kill") as numeric_kill,
            mock.patch.object(smoke.subprocess, "run") as reactive_attach,
        ):
            result = smoke._capture_wedge_backtrace(self.inst, self.bundle)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["source"], "launch-attached-gdb")
        self.assertEqual(sent, [])
        numeric_kill.assert_not_called()
        reactive_attach.assert_not_called()

    def test_invalid_collector_never_falls_back_to_gdb_attach(self) -> None:
        self.state.pop("collectorControl")
        with mock.patch.object(smoke.subprocess, "run") as reactive_attach:
            result = smoke._capture_wedge_backtrace(self.inst, self.bundle)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["source"], "launch-attached-gdb")
        reactive_attach.assert_not_called()


class OrderingAndCleanupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def test_healthy_run_does_not_enter_wedge_path(self) -> None:
        inst = FakeInstance(self.directory, {}, live_pid=None)
        definition = {
            "name": "health",
            "steps": [{"do": "health"}],
        }
        with (
            mock.patch.object(smoke.harness, "read_log", return_value=""),
            mock.patch.object(
                smoke,
                "_execute_step",
                return_value=("healthy", "", "hash", {"screenshotLatencyMs": 9}),
            ),
            mock.patch.object(smoke, "_capture_wedge_backtrace") as capture,
        ):
            ok, wedge, transcript, bundle = smoke._run_one(
                inst, definition, lambda _view, _fixture: "page:home"
            )
        self.assertTrue(ok)
        self.assertFalse(wedge)
        self.assertIsNone(bundle)
        self.assertEqual(
            transcript["steps"][0]["metrics"]["screenshotLatencyMs"], 9
        )
        capture.assert_not_called()

    def test_capture_timeout_still_precedes_owned_cleanup(self) -> None:
        inst = FakeInstance(self.directory, {}, live_pid=None)
        bundle = self.directory / "bundle"
        bundle.mkdir()
        transcript: dict = {}
        calls: list[str] = []

        def capture(*_args, **_kwargs):
            calls.append("capture")
            return {"status": "timeout", "source": "launch-attached-gdb"}

        def stop(_inst):
            calls.append("owned-cleanup")
            return "stopped-clean"

        def cleanup(_inst):
            calls.append("gdb-cleanup")
            return {"status": "exited", "gdbPid": 444}

        with (
            mock.patch.object(
                smoke, "_capture_wedge_backtrace", side_effect=capture),
            mock.patch.object(
                smoke, "stop_wedged_instance", side_effect=stop),
            mock.patch.object(
                smoke, "_cleanup_collector_debugger", side_effect=cleanup),
        ):
            stop_outcome, gdb_outcome = smoke._capture_and_stop_wedge(
                inst, bundle, transcript, "health-step", "screenshot 504"
            )

        self.assertEqual(calls, ["capture", "owned-cleanup", "gdb-cleanup"])
        self.assertEqual(stop_outcome, "stopped-clean")
        self.assertEqual(gdb_outcome["status"], "exited")
        self.assertEqual(
            transcript["ordering"],
            [
                "classified",
                "capture:timeout",
                "owned-cleanup:stopped-clean",
                "gdb-cleanup:exited",
            ],
        )

    def test_stuck_launch_gdb_is_bounded_and_force_killed(self) -> None:
        control = {"gdbPid": 444, "gdbStartTicks": 99}
        inst = FakeInstance(
            self.directory,
            {"collector": True, "collectorControl": control},
            live_pid=None,
        )
        clock = {"now": 0.0}
        alive = {"value": True}
        sent: list[int] = []

        def monotonic() -> float:
            return clock["now"]

        def sleep(seconds: float) -> None:
            clock["now"] += seconds

        def kill(_pid: int, sig: int) -> None:
            sent.append(sig)
            if sig == signal.SIGKILL:
                alive["value"] = False

        with (
            mock.patch.object(
                smoke, "_gdb_process_matches",
                side_effect=lambda _control: alive["value"],
            ),
            mock.patch.object(smoke.time, "monotonic", side_effect=monotonic),
            mock.patch.object(smoke.time, "sleep", side_effect=sleep),
            mock.patch.object(smoke.os, "kill", side_effect=kill),
        ):
            result = smoke._cleanup_collector_debugger(inst, timeout=0.1)

        self.assertEqual(result["status"], "force-killed")
        self.assertEqual(sent, [signal.SIGTERM, signal.SIGKILL])
        self.assertAlmostEqual(clock["now"], 0.3)


class CrashPropagationTest(unittest.TestCase):
    """A real bounded GDB subprocess must deliver a crash signal verbatim
    through the wedge-control loop instead of suppressing it with signal 0.
    This falsifies the pre-rework crash-suppression behavior."""

    def test_sigsegv_is_delivered_and_target_does_not_survive(self) -> None:
        gdb_bin = shutil.which("gdb")
        if gdb_bin is None:
            self.skipTest("gdb not installed")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        request_path = os.path.join(temporary.name, "wedge-request.json")
        response_path = os.path.join(temporary.name, "wedge-response.json")
        module_path = ROOT / "support" / "devtools" / "gdb" / "movian_lifecycle.py"
        script_lines = [
            "set pagination off",
            "set confirm off",
            "source " + str(module_path),
            "file /bin/sh",
            "set args -c " + shlex.quote(
                "kill -SEGV $$; echo SURVIVED; exit 0"),
            "run",
            "movian-lifecycle-wedge-control --request %s --response %s "
            "--session crash" % (
                shlex.quote(request_path), shlex.quote(response_path)),
        ]
        fd, cmdfile = tempfile.mkstemp(suffix=".gdb")
        try:
            with os.fdopen(fd, "w") as f:
                f.write("\n".join(script_lines) + "\n")
            completed = subprocess.run(
                [gdb_bin, "-q", "--batch", "-x", cmdfile],
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=30, check=False)
        finally:
            os.unlink(cmdfile)
        output = completed.stdout + completed.stderr
        # The crash signal is delivered verbatim (the inferior terminates with
        # SIGSEGV) and the post-crash echo never runs.
        self.assertNotIn("SURVIVED", output)
        self.assertIn("terminated with signal SIGSEGV", output)

class ProcessedRequestTest(unittest.TestCase):
    """A later SIGSTOP with an already-consumed request ID must not be
    suppressed with signal 0; the inferior stays stopped for GDB exit."""

    def test_already_consumed_sigstop_returns_without_suppression(self) -> None:
        gdb_bin = shutil.which("gdb")
        if gdb_bin is None:
            self.skipTest("gdb not installed")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state_dir = Path(temporary.name)
        request_path = str(state_dir / "wedge-request.json")
        response_path = str(state_dir / "wedge-response.json")
        dump_path = str(state_dir / "thread-backtrace.txt")
        module_path = ROOT / "support" / "devtools" / "gdb" / "movian_lifecycle.py"
        shell_cmd = (
            "kill -STOP $$; echo AFTER_FIRST_STOP; "
            "kill -STOP $$; echo AFTER_SECOND_STOP; exit 0")
        # After `run` returns the inferior is stopped with SIGSTOP.  Create
        # the request file with the real GDB and inferior PIDs so the wedge
        # control will match it.
        create_req = (
            "python import json, os, gdb; "
            "req = {'protocol': '" + WEDGE_PROTOCOL + "', "
            "'sessionId': 'processed-test', "
            "'gdbPid': os.getpid(), "
            "'inferiorPid': int(gdb.selected_inferior().pid), "
            "'requestId': 'req-1', "
            "'trigger': 'health-step', "
            "'classification': 'instance-health-wedge', "
            "'classificationDetail': 'test', "
            "'subsystem': 'test', "
            "'resource': '/test', "
            "'dumpPath': '" + dump_path + "', "
            "'classifiedMonotonicNs': 100}; "
            "json.dump(req, open('" + request_path + "', 'w'), indent=2)")
        script_lines = [
            "set pagination off",
            "set confirm off",
            "source " + str(module_path),
            "file /bin/sh",
            "set args -c " + shlex.quote(shell_cmd),
            "run",
            create_req,
            "movian-lifecycle-wedge-control --request %s --response %s "
            "--session processed-test" % (
                shlex.quote(request_path), shlex.quote(response_path)),
        ]
        fd, cmdfile = tempfile.mkstemp(suffix=".gdb")
        try:
            with os.fdopen(fd, "w") as f:
                f.write("\n".join(script_lines) + "\n")
            completed = subprocess.run(
                [gdb_bin, "-q", "--batch", "-x", cmdfile],
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=30, check=False)
        finally:
            os.unlink(cmdfile)
        output = completed.stdout + completed.stderr
        # The first SIGSTOP was captured and resumed with signal 0 so the
        # shell printed AFTER_FIRST_STOP.  The second SIGSTOP with the
        # same requestId was NOT resumed, so AFTER_SECOND_STOP was never
        # reached and the shell never exited cleanly.
        self.assertIn("AFTER_FIRST_STOP", output)
        self.assertNotIn("AFTER_SECOND_STOP", output)


class StaleControlClearTest(unittest.TestCase):
    """A non-ENOENT failure to clear a stale control path must abort the
    launch before the GDB Popen, not be swallowed."""

    def test_unremovable_request_aborts_before_popen(self) -> None:
        gdb_bin = shutil.which("gdb")
        if gdb_bin is None:
            self.skipTest("gdb not installed")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state_dir = Path(temporary.name)
        # wedge-request.json is a directory: unlink raises IsADirectoryError,
        # which is NOT FileNotFoundError, so the launch must fail closed here.
        (state_dir / "wedge-request.json").mkdir()
        (state_dir / "wedge-response.json").write_text(
            "stale\n", encoding="utf-8")
        import movian_lifecycle as ml
        args = build_argparser().parse_args([
            "launch", "--name", "stale-clear-test",
            "--mode", "gdb-collector",
            "--binary", "/bin/true",
            "--gdb", gdb_bin,
            "--persistent", str(state_dir / "persistent"),
            "--cache", str(state_dir / "cache"),
        ])
        summary_buf = io.StringIO()
        with (
            mock.patch.object(ml, "instance_state", return_value=str(state_dir)),
            mock.patch.object(ml, "find_inferior_pid", return_value=None),
            mock.patch.object(ml.subprocess, "Popen") as popen,
            contextlib.redirect_stdout(summary_buf),
        ):
            code = run_launch(args)
        self.assertEqual(code, 1)
        popen.assert_not_called()
        self.assertIn("stale-control-clear-failed", summary_buf.getvalue())


class EmergencyEjectTrackerTest(unittest.TestCase):
    """State transitions and invariants for the emergency-eject tracker."""

    def test_initial_state_is_unobserved(self) -> None:
        t = EmergencyEjectTracker()
        self.assertEqual(t.state, "unobserved")
        snap = t.snapshot()
        self.assertFalse(snap["observed"])
        self.assertFalse(snap["requested"])
        self.assertFalse(snap["armed"])
        self.assertFalse(snap["fired"])

    def test_observe_transitions_to_not_requested(self) -> None:
        t = EmergencyEjectTracker()
        t.observe()
        self.assertEqual(t.state, "not-requested")
        snap = t.snapshot()
        self.assertTrue(snap["observed"])
        self.assertFalse(snap["requested"])
        self.assertFalse(snap["armed"])
        self.assertFalse(snap["fired"])

    def test_full_eject_chain(self) -> None:
        t = EmergencyEjectTracker()
        t.observe()
        t.on_request()
        self.assertEqual(t.state, "requested")
        t.on_arm(42)
        self.assertEqual(t.state, "armed")
        t.on_exit(42)
        self.assertEqual(t.state, "fired")
        snap = t.snapshot()
        self.assertTrue(all(snap[k] for k in
                           ("observed", "requested", "armed", "fired")))

    def test_arm_records_tid(self) -> None:
        t = EmergencyEjectTracker()
        t.observe()
        t.on_request()
        t.on_arm(99)
        self.assertEqual(t._eject_tid, 99)

    def test_normal_thread_arch_exit_does_not_fire(self) -> None:
        """arch_exit on a different thread must not set fired."""
        t = EmergencyEjectTracker()
        t.observe()
        t.on_request()
        t.on_arm(42)
        t.on_exit(99)  # different TID
        self.assertEqual(t.state, "armed")
        snap = t.snapshot()
        self.assertFalse(snap["fired"])

    def test_out_of_order_transitions_are_ignored(self) -> None:
        t = EmergencyEjectTracker()
        t.on_request()  # skip observe
        self.assertEqual(t.state, "unobserved")
        t.observe()
        t.on_arm(1)  # skip request
        self.assertEqual(t.state, "not-requested")
        t.on_exit(1)  # skip arm
        self.assertEqual(t.state, "not-requested")

    def test_unobserved_snapshot_booleans(self) -> None:
        t = EmergencyEjectTracker()
        snap = t.snapshot()
        self.assertEqual(snap["state"], "unobserved")
        self.assertFalse(snap["observed"])

    def test_all_states_are_allowed(self) -> None:
        for state in EMERGENCY_EJECT_STATES:
            self.assertIn(state, ("unobserved", "not-requested",
                                  "requested", "armed", "fired"))

    def test_no_repeat_fires(self) -> None:
        """Double on_exit on the same TID after fired must be a no-op."""
        t = EmergencyEjectTracker()
        t.observe()
        t.on_request()
        t.on_arm(42)
        t.on_exit(42)
        self.assertEqual(t.state, "fired")
        t.on_exit(42)
        self.assertEqual(t.state, "fired")

    def test_request_without_observe_is_noop(self) -> None:
        t = EmergencyEjectTracker()
        t.on_request()
        self.assertEqual(t.state, "unobserved")

    def test_arm_without_request_is_noop(self) -> None:
        t = EmergencyEjectTracker()
        t.observe()
        t.on_arm(1)
        self.assertEqual(t.state, "not-requested")


class EmergencyEjectSchemaValidationTest(unittest.TestCase):
    """Schema validation for the new emergencyEject fields."""

    def _event(self, **overrides) -> dict:
        ev = success_event()
        ev["emergencyEject"] = {
            "state": "not-requested",
            "observed": True,
            "requested": False,
            "armed": False,
            "fired": False,
        }
        ev["emergencyEject"].update(overrides)
        return ev

    def test_valid_not_requested(self) -> None:
        ev = self._event()
        self.assertEqual(validate_wedge_event(ev), [])

    def test_valid_fired(self) -> None:
        ev = self._event(state="fired", observed=True,
                         requested=True, armed=True, fired=True)
        self.assertEqual(validate_wedge_event(ev), [])

    def test_rejects_inconsistent_observed(self) -> None:
        ev = self._event(state="unobserved", observed=True)
        errs = validate_wedge_event(ev)
        self.assertTrue(any("observed" in e and "inconsistent" in e
                            for e in errs))

    def test_rejects_inconsistent_armed(self) -> None:
        ev = self._event(state="requested", armed=True)
        errs = validate_wedge_event(ev)
        self.assertTrue(any("armed" in e and "inconsistent" in e
                            for e in errs))

    def test_rejects_inconsistent_fired(self) -> None:
        ev = self._event(state="armed", fired=True)
        errs = validate_wedge_event(ev)
        self.assertTrue(any("fired" in e and "inconsistent" in e
                            for e in errs))

    def test_rejects_unknown_state(self) -> None:
        ev = self._event(state="bogus")
        errs = validate_wedge_event(ev)
        self.assertTrue(any("emergencyEject.state" in e for e in errs))

    def test_rejects_missing_observed(self) -> None:
        ev = self._event()
        del ev["emergencyEject"]["observed"]
        errs = validate_wedge_event(ev)
        self.assertTrue(any("observed" in e for e in errs))

    def test_rejects_missing_armed(self) -> None:
        ev = self._event()
        del ev["emergencyEject"]["armed"]
        errs = validate_wedge_event(ev)
        self.assertTrue(any("armed" in e for e in errs))


class HostRequestNoEjectFieldTest(unittest.TestCase):
    """The host wedge request must NOT include emergencyEject; the GDB
    collector builds it from inferior probes."""

    def test_smoke_request_has_no_eject_field(self) -> None:
        """smoke._capture_from_attached_gdb must not put emergencyEject in
        the host request dict."""
        import inspect
        src = inspect.getsource(smoke._capture_from_attached_gdb)
        self.assertNotIn("emergencyEject", src)

    def test_wedge_event_requires_eject_from_collector(self) -> None:
        """A wedge event without emergencyEject must fail validation."""
        ev = success_event()
        del ev["emergencyEject"]
        errs = validate_wedge_event(ev)
        self.assertTrue(any("emergencyEject" in e for e in errs))


class InventoryEntryTest(unittest.TestCase):
    """inventory.json must contain shutdown_eject and arch_exit for the
    emergency-eject tracker probes."""

    def test_shutdown_eject_in_inventory(self) -> None:
        inv_path = ROOT / "support" / "devtools" / "gdb" / "inventory.json"
        inv = json.loads(inv_path.read_text(encoding="utf-8"))
        ids = {e["id"] for e in inv["entries"]}
        self.assertIn("shutdown_eject", ids)
        self.assertIn("arch_exit", ids)
        # Both must be core-init category for the observe() call
        for entry in inv["entries"]:
            if entry["id"] in ("shutdown_eject", "arch_exit"):
                self.assertEqual(entry["category"], "core-init")


class MandatoryPredicateTest(unittest.TestCase):
    """Pure module-level predicate tests for emergency-eject mandatory
    symbols.  No GDB fake required -- these operate on plain data."""

    def test_is_eject_mandatory_true_for_app_shutdown(self):
        self.assertTrue(is_eject_mandatory("app_shutdown"))

    def test_is_eject_mandatory_true_for_shutdown_eject(self):
        self.assertTrue(is_eject_mandatory("shutdown_eject"))

    def test_is_eject_mandatory_true_for_arch_exit(self):
        self.assertTrue(is_eject_mandatory("arch_exit"))

    def test_is_eject_mandatory_false_for_non_mandatory(self):
        self.assertFalse(is_eject_mandatory("main_init"))

    def test_all_eject_mandatory_bound_all_three(self):
        class Bp:
            def __init__(self, sym, bound=True):
                self.symbol = sym
                self.bound = bound
        armed = [Bp("app_shutdown"), Bp("shutdown_eject"), Bp("arch_exit")]
        self.assertTrue(all_eject_mandatory_bound(armed))

    def test_all_eject_mandatory_bound_partial(self):
        class Bp:
            def __init__(self, sym, bound=True):
                self.symbol = sym
                self.bound = bound
        armed = [Bp("app_shutdown"), Bp("shutdown_eject")]
        self.assertFalse(all_eject_mandatory_bound(armed))

    def test_all_eject_mandatory_bound_unbound_symbol(self):
        class Bp:
            def __init__(self, sym, bound=True):
                self.symbol = sym
                self.bound = bound
        armed = [Bp("app_shutdown"), Bp("shutdown_eject"),
                 Bp("arch_exit", bound=False)]
        self.assertFalse(all_eject_mandatory_bound(armed))

    def test_all_eject_mandatory_bound_empty(self):
        self.assertFalse(all_eject_mandatory_bound([]))


if __name__ == "__main__":
    unittest.main()

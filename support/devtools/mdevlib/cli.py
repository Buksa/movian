"""mdev subcommand implementations and argument parsing (issue #85).

Exit codes: 0 = verified success, 2 = stale-process guard refusal,
1 = any other failure (one-line reason on stderr).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from . import harness
from . import lspdoctor
from . import viewdoc
from .harness import Instance, MdevError


def emit(args: argparse.Namespace, data: dict, human: str) -> None:
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    elif human:
        print(human)


# ---------------------------------------------------------------------------
# run / stop
# ---------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    inst = Instance(args.name)
    own_pid = inst.live_pid()
    foreign, collisions = harness.classify_foreign(inst, own_pid)

    # Same-dir collision: a live movian pid is using this instance's own
    # --persistent path but state.json didn't confirm it as `own_pid`
    # (stale/corrupted state.json, or a race). Refuse -- this is not a
    # foreign instance, it's ambiguity about our own state (issue #94).
    if collisions:
        raise harness.collision_refusal(inst, collisions)

    if own_pid is not None:
        if not args.force:
            raise MdevError(
                "instance %r already running (pid %d); "
                "use --force to restart it" % (args.name, own_pid),
                exit_code=2,
            )
        harness.kill_owned_pid(inst, own_pid)

    # Foreign movian instances (not ours, not a same-dir collision) are
    # safe to coexist with: isolated profile + dynamic port, no state.json
    # overlap. Warn instead of refusing (issue #94).
    if foreign:
        print(harness.coexist_warning(foreign), file=sys.stderr)

    inst.ensure_dirs()

    if args.dev_flags:
        flags = harness.parse_dev_flags(args.dev_flags)
        settings_dir = inst.persistent / "settings"
        settings_dir.mkdir(parents=True, exist_ok=True)
        (settings_dir / "dev").write_text(
            json.dumps(flags), encoding="utf-8"
        )

    argv = harness.build_argv(inst, args.plugin, args.skin,
                           args.libav_log, args.start_url)
    state = harness.launch(inst, argv)
    emit(args, state,
         "started %s pid=%d port=%d log=%s"
         % (args.name, state["pid"], state["port"], state["log"]))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    inst = Instance(args.name)
    pid = inst.live_pid()
    if pid is None:
        emit(args, {"name": args.name, "stopped": False,
                    "reason": "not running"},
             "instance %r is not running" % args.name)
        return 0
    harness.kill_owned_pid(inst, pid)
    if inst.owns_pid(pid):
        raise MdevError("pid %d still alive after SIGKILL" % pid)
    state = inst.load_state() or {}
    state.pop("pid", None)
    state.pop("port", None)
    inst.save_state(state)
    emit(args, {"name": args.name, "stopped": True, "pid": pid},
         "stopped %s (pid %d)" % (args.name, pid))
    return 0


# ---------------------------------------------------------------------------
# open
# ---------------------------------------------------------------------------

def cmd_open(args: argparse.Namespace) -> int:
    inst = Instance(args.name)
    result = harness.open_and_wait(inst, args.url, timeout=args.timeout)
    emit(args, result,
         "url:   %s\ntitle: %s\ntype:  %s\nnodes: %d"
         % (result["url"], result["title"], result["type"], result["nodes"]))
    return 0


# ---------------------------------------------------------------------------
# shot
# ---------------------------------------------------------------------------

def cmd_shot(args: argparse.Namespace) -> int:
    inst = Instance(args.name)
    path = harness.take_shot(inst, args.out)
    emit(args, {"path": str(path), "bytes": path.stat().st_size},
         str(path))
    return 0


# ---------------------------------------------------------------------------
# props
# ---------------------------------------------------------------------------

def collect_props(base: str, path: str, depth: int) -> dict:
    parsed = harness.get_prop(base, path)
    if parsed is None:
        return {"path": path, "error": "not found"}
    node = {"path": path, "value": parsed.get("value")}
    if parsed.get("value") == "directory":
        children = parsed.get("children", [])
        node["child_count"] = len(children)
        if depth > 0:
            node["children"] = {}
            for index, name in enumerate(children):
                ref = "*%d" % index if name == "<unnamed>" else name
                node["children"][ref] = collect_props(
                    base, path + "/" + ref, depth - 1
                )
    return node


def print_props(node: dict, indent: int = 0) -> None:
    pad = "  " * indent
    label = node["path"] if indent == 0 else node["path"].rsplit("/", 1)[-1]
    if "error" in node:
        print("%s%s: <%s>" % (pad, label, node["error"]))
    elif node.get("value") == "directory":
        print("%s%s/ (directory, %d children)"
              % (pad, label, node.get("child_count", 0)))
        for child in node.get("children", {}).values():
            print_props(child, indent + 1)
    else:
        print("%s%s = %s" % (pad, label, node.get("value")))


def cmd_props(args: argparse.Namespace) -> int:
    inst = Instance(args.name)
    base = inst.base_url()
    tree = collect_props(base, args.path.strip("/"), args.depth)
    if tree.get("error"):
        raise MdevError("prop not found: %s" % args.path)
    if args.json:
        print(json.dumps(tree, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_props(tree)
    return 0


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------

def cmd_log(args: argparse.Namespace) -> int:
    inst = Instance(args.name)
    if not inst.log_path.is_file():
        raise MdevError("no log file: %s" % inst.log_path)
    text = harness.read_log(inst)
    lines = text.splitlines()
    if args.errors:
        lines = harness.error_lines(text)
    if args.tail:
        lines = lines[-args.tail:]
    if args.json:
        print(json.dumps({"log": str(inst.log_path), "lines": lines,
                          "matched": len(lines) if args.errors else None},
                         ensure_ascii=False, indent=2))
    else:
        for line in lines:
            print(line)
    if args.errors and lines:
        print("%d error line(s) matched" % len(lines), file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# reload
# ---------------------------------------------------------------------------

def _maybe_shot(args: argparse.Namespace) -> str | None:
    """Screenshot path when --shot was passed, else None (shared by every
    shot-on-success reporter)."""
    if getattr(args, "shot", False):
        return str(harness.take_shot(Instance(args.name)))
    return None


def report_reload(args: argparse.Namespace, ok: bool,
                  errors: list[str]) -> int:
    if ok:
        shot = _maybe_shot(args)
        emit(args, {"reload": "ok", "shot": shot},
             "RELOAD OK" + (("\nshot: " + shot) if shot else ""))
        return 0
    if args.json:
        print(json.dumps({"reload": "error", "errors": errors},
                         ensure_ascii=False, indent=2))
    else:
        for line in errors:
            print(line)
    return 1


def cmd_reload(args: argparse.Namespace) -> int:
    inst = Instance(args.name)
    if getattr(args, "js", False):
        ok, per_plugin = harness.do_reload_js(inst)
        return report_reload_js(args, ok, per_plugin)
    ok, errors = harness.do_reload(inst)
    return report_reload(args, ok, errors)


def report_reload_js(args: argparse.Namespace, ok: bool,
                     per_plugin: list[dict]) -> int:
    """Report a `mdev reload --js` result: one line per `-p` dev plugin
    (issue #93 contract: "prints per-plugin result")."""
    lines = [
        "%s: %s%s" % (
            "OK" if p["ok"] else "FAILED",
            p["plugin"] or "(unattributed)",
            "" if p["ok"] else (" -- " + p["detail"]),
        )
        for p in per_plugin
    ]
    if ok:
        shot = _maybe_shot(args)
        emit(args, {"reload_js": "ok", "plugins": per_plugin, "shot": shot},
             "\n".join(lines) + "\nRELOAD JS OK"
             + (("\nshot: " + shot) if shot else ""))
        return 0
    if args.json:
        print(json.dumps({"reload_js": "error", "plugins": per_plugin},
                         ensure_ascii=False, indent=2))
    else:
        for line in lines:
            print(line)
    return 1


# ---------------------------------------------------------------------------
# watch
# ---------------------------------------------------------------------------

def scan_watch_files(
    root: Path, include_js: bool
) -> tuple[dict[Path, float], dict[Path, float]]:
    """One directory traversal returning ({*.view: mtime}, {*.js /
    plugin.json: mtime}) -- cmd_watch polls this twice a second, so a
    single os.walk pass instead of one rglob per pattern (issue #93)."""
    views: dict[Path, float] = {}
    plugin: dict[Path, float] = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        parent = Path(dirpath)
        for name in filenames:
            if name.endswith(".view"):
                target = views
            elif include_js and (name.endswith(".js")
                                 or name == "plugin.json"):
                target = plugin
            else:
                continue
            path = parent / name
            try:
                target[path] = path.stat().st_mtime
            except OSError:
                continue  # deleted between listing and stat
    return views, plugin


def _changed_paths(root: Path, seen: dict[Path, float],
                   current: dict[Path, float]) -> list[str]:
    return sorted(
        str(p.relative_to(root)) for p in
        (set(current) ^ set(seen))
        | {p for p in current if p in seen and current[p] != seen[p]}
    )


def _resolve_watch_root(args: argparse.Namespace, inst: Instance) -> Path:
    if args.dir is not None:
        root = Path(args.dir)
        if not root.is_absolute():
            root = harness.REPO_ROOT / root
        return root
    if not args.js:
        return harness.REPO_ROOT / "glwskins" / "flat"
    # --js with no --dir: default to this instance's own -p plugin dir,
    # but only when it is unambiguous.
    state = inst.load_state() or {}
    dirs = harness.plugin_dirs_from_argv(state.get("argv") or [])
    if len(dirs) != 1:
        raise MdevError(
            "--js needs --dir <plugin-dir> when the instance has %d "
            "dev plugins (need exactly 1 to default unambiguously)"
            % len(dirs)
        )
    return Path(dirs[0])


def _watch_tick(args: argparse.Namespace, inst: Instance, stamp: str,
                changed_view: list[str], changed_js: list[str]) -> list[str]:
    """Run the reload flow(s) for one tick's changes; return report lines.

    A mixed tick (JS and views changed together) runs BOTH flows: the
    ReloadData page reload alone would re-render against GLW's still-
    cached OLD .view parses -- and do_reload_js never scans for GLW view
    errors -- so a view syntax error would go unreported (false green).
    JS first (it resets page state), then ReloadUI to flush and re-parse
    the views.
    """
    lines: list[str] = []
    if changed_js:
        ok, per_plugin = harness.do_reload_js(inst, settle=1.2)
        if ok:
            line = "[%s] JS %s: RELOAD JS OK" % (stamp, ", ".join(changed_js))
            if args.shot and not changed_view:
                line += " shot=%s" % harness.take_shot(inst)
        else:
            failed = next(p for p in per_plugin if not p["ok"])
            line = "[%s] JS %s: FAILED: %s" % (
                stamp, ", ".join(changed_js), failed["detail"])
        lines.append(line)
        if not changed_view:
            return lines

    # Tighter settle than plain `mdev reload` so the report lands
    # within 2 s of the file change (0.5 s poll + 1.2 s settle);
    # view errors surface within milliseconds of ReloadUI anyway.
    ok, errors = harness.do_reload(inst, settle=1.2)
    if ok:
        line = "[%s] %s: RELOAD OK" % (stamp, ", ".join(changed_view))
        if args.shot:
            line += " shot=%s" % harness.take_shot(inst)
    else:
        line = "[%s] %s: %d error(s): %s" % (
            stamp, ", ".join(changed_view), len(errors), errors[0])
    lines.append(line)
    return lines


def cmd_watch(args: argparse.Namespace) -> int:
    inst = Instance(args.name)
    inst.base_url()  # verify instance is up before entering the loop
    root = _resolve_watch_root(args, inst)
    if not root.is_dir():
        raise MdevError("watch dir not found: %s" % root)

    seen_view, seen_js = scan_watch_files(root, args.js)
    extra = (" + %d JS/plugin.json file(s)" % len(seen_js)) if args.js else ""
    print("watching %d .view file(s)%s under %s (Ctrl-C to stop)"
          % (len(seen_view), extra, root))
    try:
        while True:
            time.sleep(0.5)
            current_view, current_js = scan_watch_files(root, args.js)
            changed_view = _changed_paths(root, seen_view, current_view)
            changed_js = _changed_paths(root, seen_js, current_js)
            seen_view, seen_js = current_view, current_js

            if not changed_view and not changed_js:
                continue

            stamp = time.strftime("%H:%M:%S")
            try:
                for line in _watch_tick(args, inst, stamp,
                                        changed_view, changed_js):
                    print(line, flush=True)
            except MdevError as error:
                # One flaky reload/screenshot must not kill a long-running
                # watch; only bail when the instance itself is gone.
                if inst.live_pid() is None:
                    raise MdevError(
                        "instance %r died: %s" % (args.name, error))
                print("[%s] reload failed (will retry on next change): %s"
                      % (stamp, error), flush=True)
    except KeyboardInterrupt:
        return 0


# ---------------------------------------------------------------------------
# preview (issue #87 -- viewpreview dev plugin)
# ---------------------------------------------------------------------------

def resolve_repo_path(path_str: str) -> Path:
    """Absolute-ize a CLI-supplied path against REPO_ROOT (not just CWD),
    matching cmd_watch's --dir handling."""
    p = Path(path_str)
    if not p.is_absolute():
        p = harness.REPO_ROOT / p
    return p.resolve()


def viewpreview_route(view_path: str, fixture_path: str | None) -> str:
    """Build the `viewpreview:show:<base64 JSON>` route for `view_path`
    (+ optional fixture). Paths are resolved to absolute filesystem paths
    here -- the plugin itself does the existence/readability checks so a
    missing file surfaces as visible page text + a `viewpreview:` log
    line, not a bare mdev-side error (see support/devtools/viewpreview/
    README.md "error surfacing")."""
    config: dict[str, Any] = {
        "view": str(resolve_repo_path(view_path)),
        "type": "raw",
    }
    if fixture_path:
        config["fixture"] = str(resolve_repo_path(fixture_path))
    payload = json.dumps(config).encode("utf-8")
    return "viewpreview:show:" + base64.b64encode(payload).decode("ascii")


def cmd_preview(args: argparse.Namespace) -> int:
    inst = harness.ensure_running(args.name, [str(harness.VIEWPREVIEW_DIR)])
    route = viewpreview_route(args.view, args.fixture)

    # Flush GLW's per-path view cache before opening: GLW only re-parses
    # a .view on ReloadUI (glw_load_universe, src/ui/glw/glw.c:2522), so
    # a re-preview of a just-edited file would otherwise render the stale
    # cached parse -- and report it error-free (false green).
    #
    # The log offset must be taken BEFORE the flush: when the previous
    # page already shows this same view (the normal iterate loop), the
    # flush itself re-parses the edited file and traces its error right
    # then -- the open below is then served from that already-loaded
    # cache entry without a second trace. View errors are filtered to the
    # target view's path below, so re-parse errors from unrelated views
    # in the skin can't leak into this preview's report.
    base = inst.base_url()
    offset = harness.log_size(inst)
    flush = harness.http_request(base, "/api/input/action/ReloadUI",
                                 timeout=5.0, method="POST")
    if not flush.get("ok"):
        raise MdevError(
            "POST /api/input/action/ReloadUI (view-cache flush) failed: %s"
            % (flush.get("error") or flush.get("status")))
    time.sleep(0.4)  # let the universe reload land before the open below

    result = harness.open_and_wait(inst, route, timeout=20.0)

    # Settle window: a prop change (page.metadata.glwview) is dispatched
    # to the GLW thread asynchronously, so a GLW view-parse error (or our
    # own "viewpreview: ERROR:" line, which the JS route handler can also
    # emit slightly after loading/title go ready) can land a beat after
    # open_and_wait already sees the page as ready. Poll briefly rather
    # than reading the log delta once immediately (same idea as
    # do_reload's settle window).
    view_path = str(resolve_repo_path(args.view))
    deadline = time.monotonic() + 1.5
    errors: list[str] = []
    while time.monotonic() < deadline:
        delta = harness.read_log_delta(inst, offset)
        errors = harness.viewpreview_error_lines(delta) + [
            line for line in harness.view_error_lines(delta)
            if view_path in line
        ]
        if errors:
            break
        time.sleep(0.2)

    if errors:
        if args.json:
            print(json.dumps({"preview": "error", "errors": errors,
                              "result": result},
                             ensure_ascii=False, indent=2))
        else:
            print("PREVIEW ERROR")
            for line in errors:
                print(line)
        return 1

    shot_path = None
    if args.shot:
        # raw.view's loader crossfades over 0.2s (glwskins/flat/pages/
        # raw.view: "effect: blend; time: 0.2;"); page-ready fires as
        # soon as the JS side sets loading=false, which is before that
        # blend visually finishes. A short settle avoids a screenshot
        # that still shows the previous preview mid-transition.
        time.sleep(0.3)
        shot_path = str(harness.take_shot(inst))

    emit(args,
         {**result, "shot": shot_path},
         "url:   %s\ntitle: %s\ntype:  %s\nnodes: %d%s"
         % (result["url"], result["title"], result["type"], result["nodes"],
            ("\nshot:  " + shot_path) if shot_path else ""))
    return 0


# ---------------------------------------------------------------------------
# viewdoc (issue #88 -- GLW reference-doc drift detector)
# ---------------------------------------------------------------------------

def cmd_viewdoc(args: argparse.Namespace) -> int:
    if not args.check:
        # No --check: dump the source-side inventories (handy for doc work).
        inv = viewdoc.inventory()
        enum_values = viewdoc.attribute_enum_values()
        if args.json:
            print(json.dumps({**inv, "attributeEnumValues": enum_values},
                             ensure_ascii=False, indent=2))
        else:
            for kind, names in inv.items():
                print("%s (%d): %s" % (kind, len(names), " ".join(names)))
            for name, values in enum_values.items():
                print("attribute %s values: %s"
                      % (name, " | ".join(values)))
        return 0

    result = viewdoc.run_check()
    drift_lines: list[str] = []
    for kind, diff in result.items():
        for name in diff["missing_from_doc"]:
            drift_lines.append("missing-from-doc (%s): %s"
                               % (kind.rstrip("s"), name))
        for name in diff["gone_from_source"]:
            drift_lines.append("gone-from-source (%s): %s"
                               % (kind.rstrip("s"), name))

    if args.json:
        print(json.dumps({"viewdoc": "error" if drift_lines else "ok",
                          "result": result},
                         ensure_ascii=False, indent=2))
    else:
        for kind, diff in result.items():
            print("%s: source=%d documented=%d"
                  % (kind, diff["source_count"], diff["documented_count"]))
        for line in drift_lines:
            print(line)
        if not drift_lines:
            print("VIEWDOC OK")
    return 1 if drift_lines else 0


# ---------------------------------------------------------------------------
# lsp (issue #100 -- editor integration preflight)
# ---------------------------------------------------------------------------

def cmd_lsp_doctor(_args: argparse.Namespace) -> int:
    return lspdoctor.run()


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mdev",
        description="Single-entrypoint Movian dev/test harness "
                    "(isolated launch, open, shot, props, log, reload, watch)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--name", default="dev",
                        help="instance name; state in /tmp/mdev/<name>/ "
                             "(default: dev)")
    common.add_argument("--json", action="store_true",
                        help="machine-readable JSON output")

    run = sub.add_parser(
        "run", parents=[common],
        help="launch an isolated Movian instance",
        description="Launch an isolated Movian instance under "
                    "/tmp/mdev/<name>/. Coexists with any foreign movian "
                    "process (own isolated profile + dynamic port): prints "
                    "a one-line warning naming the foreign pid(s) and "
                    "proceeds. Exit 2 is reserved for (a) this same --name "
                    "already alive (use --force to restart it) and (b) a "
                    "live pid using this instance's own --persistent path "
                    "that state.json can't confirm as ours (corrupted/"
                    "stale state -- investigate, don't --force blindly). "
                    "`mdev stop`/`--force` only ever signal the pid "
                    "recorded in this instance's own state.json, never a "
                    "foreign pid.")
    run.add_argument("-p", "--plugin", action="append", default=[],
                     metavar="DIR", help="plugin dev directory; repeatable")
    run.add_argument("--skin", metavar="DIR",
                     help="skin directory (GLW --skin)")
    run.add_argument("--dev-flags", metavar="K=1,K2=1",
                     help="seed <persistent>/settings/dev before launch")
    run.add_argument("--libav-log", action="store_true",
                     help="pass --libav-log to movian")
    run.add_argument("--force", action="store_true",
                     help="restart the instance this state dir owns; "
                          "never kills foreign pids")
    run.add_argument("start_url", nargs="?",
                     help="URL to open at startup")
    run.set_defaults(func=cmd_run)

    stop = sub.add_parser("stop", parents=[common],
                          help="stop the instance owned by this state dir")
    stop.set_defaults(func=cmd_stop)

    opn = sub.add_parser("open", parents=[common],
                         help="open a route and wait for page-ready")
    opn.add_argument("url")
    opn.add_argument("--timeout", type=float, default=20.0,
                     help="page-ready timeout in seconds (default: 20)")
    opn.set_defaults(func=cmd_open)

    shot = sub.add_parser("shot", parents=[common],
                          help="take a screenshot via /api/screenshot/raw")
    shot.add_argument("--out", metavar="PATH",
                      help="output file (default: shots/<ts>.<ext>)")
    shot.set_defaults(func=cmd_shot)

    props = sub.add_parser("props", parents=[common],
                           help="pretty-print an /api/prop subtree")
    props.add_argument("path", metavar="SLASH-PATH",
                       help="e.g. global/navigators/current/currentpage")
    props.add_argument("--depth", type=int, default=1,
                       help="directory recursion depth (default: 1)")
    props.set_defaults(func=cmd_props)

    log = sub.add_parser("log", parents=[common],
                         help="dump/tail the instance log")
    log.add_argument("--tail", type=int, default=0, metavar="N",
                     help="only the last N lines (default: all)")
    log.add_argument("--errors", action="store_true",
                     help="only error-signal lines; exit 1 if any matched")
    log.set_defaults(func=cmd_log)

    reload_ = sub.add_parser(
        "reload", parents=[common],
        help="ReloadUI and grep the log for view errors",
        description="Plain `mdev reload` sends ReloadUI (views-only, "
                    "unchanged). `--js` instead sends ReloadData (issue "
                    "#93): reloads every `-p` dev plugin's ECMAScript "
                    "(core plugins_reload_dev_plugin(), src/plugins.c) "
                    "and reloads the current page as a side effect -- "
                    "page state resets, so it is opt-in rather than the "
                    "default. Exit 0 only when every `-p` plugin reports "
                    "reloaded.")
    reload_.add_argument("--shot", action="store_true",
                         help="screenshot after a clean reload")
    reload_.add_argument("--js", action="store_true",
                         help="reload dev-plugin JS via ReloadData instead "
                              "of views via ReloadUI; resets page state")
    reload_.set_defaults(func=cmd_reload)

    watch = sub.add_parser(
        "watch", parents=[common],
        help="auto-reload when .view (or, with --js, plugin JS) files "
             "change",
        description="Polls `--dir` for changed `*.view` files and runs "
                    "the `reload` (ReloadUI) flow on change (default: "
                    "glwskins/flat). With `--js`, ALSO polls the same "
                    "root for `*.js`/`plugin.json` and runs the `reload "
                    "--js` (ReloadData) flow instead when those change -- "
                    "default root becomes this instance's own `-p` "
                    "plugin dir (only when there is exactly one; pass "
                    "--dir explicitly otherwise). A tick with both kinds "
                    "of changes runs the JS reload first, then the view "
                    "reload (ReloadUI is what re-parses changed views "
                    "and surfaces their errors).")
    watch.add_argument("--dir", default=None,
                       help="directory to watch (default: glwskins/flat, "
                            "or this instance's own -p plugin dir with "
                            "--js)")
    watch.add_argument("--shot", action="store_true",
                       help="screenshot after each clean reload")
    watch.add_argument("--js", action="store_true",
                       help="also watch *.js/plugin.json and run the "
                            "ReloadData flow on change")
    watch.set_defaults(func=cmd_watch)

    # Own --name/--json (not `common`): the default instance name is
    # "preview", not "dev" (issue #87 contract).
    preview = sub.add_parser(
        "preview",
        help="render one .view file in isolation via the viewpreview dev "
             "plugin (auto-starts a 'preview' instance if needed)")
    preview.add_argument("--name", default="preview",
                         help="instance name; state in /tmp/mdev/<name>/ "
                              "(default: preview)")
    preview.add_argument("--json", action="store_true",
                         help="machine-readable JSON output")
    preview.add_argument("view", metavar="VIEW-PATH",
                         help=".view file to render, any path (see "
                              "support/devtools/viewpreview/README.md)")
    preview.add_argument("--fixture", metavar="JSON",
                         help="fixture JSON file (schema v1, see "
                              "support/devtools/viewpreview/README.md)")
    preview.add_argument("--shot", action="store_true",
                         help="screenshot after a clean render")
    preview.set_defaults(func=cmd_preview)

    # No instance/--name: viewdoc reads files only, never talks to a
    # running Movian.
    viewdoc_ = sub.add_parser(
        "viewdoc",
        help="diff the GLW attribute/function tables against the "
             "movian-view-design reference docs (issue #88)",
        description="Reads attribute and expression-function names from "
                    "generated/movian-metadata.json's glw.attributes / "
                    "glw.functions (issue #98's generated artifact -- run "
                    "support/devtools/metadata/gen.py to (re)build it from "
                    "glw_view_attrib.c's attribtab[] / glw_view_eval.c's "
                    "funcvec[]), and (with --check) diffs them against the "
                    "names documented in the movian-view-design skill's "
                    "glw-widget-catalog.md / glw-view-language.md. Reports "
                    "missing-from-doc (in the artifact, undocumented) and "
                    "gone-from-source (documented, not in the artifact); "
                    "exit 1 on any drift. Without --check, dumps the "
                    "artifact-side inventories.")
    viewdoc_.add_argument("--check", action="store_true",
                          help="diff artifact tables against the docs; "
                               "exit 1 on any drift")
    viewdoc_.add_argument("--json", action="store_true",
                          help="machine-readable JSON output")
    viewdoc_.set_defaults(func=cmd_viewdoc)

    lsp = sub.add_parser(
        "lsp",
        help="movian-lsp editor-integration tools",
    )
    lsp_sub = lsp.add_subparsers(dest="lsp_command", required=True)
    doctor = lsp_sub.add_parser(
        "doctor",
        help="check movian-lsp prerequisites and one stdio initialize round-trip",
    )
    doctor.set_defaults(func=cmd_lsp_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except MdevError as error:
        print("mdev: %s" % error, file=sys.stderr)
        return error.exit_code
    except KeyboardInterrupt:
        return 130

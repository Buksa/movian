"""mdev subcommand implementations and argument parsing (issue #85).

Exit codes: 0 = verified success, 2 = stale-process guard refusal,
1 = any other failure (one-line reason on stderr).
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path
from typing import Any

from . import harness
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
    all_pids = harness.movian_pids()
    foreign = [p for p in all_pids if p != own_pid]

    if foreign:
        raise MdevError(
            "refusing to start: live movian process(es) not owned by "
            "instance %r: pid %s (their state is not in %s). "
            "Stop them from their own instance; --force never kills "
            "foreign pids." % (
                args.name,
                ", ".join(str(p) for p in foreign),
                inst.state_path,
            ),
            exit_code=2,
        )

    if own_pid is not None:
        if not args.force:
            raise MdevError(
                "instance %r already running (pid %d); "
                "use --force to restart it" % (args.name, own_pid),
                exit_code=2,
            )
        harness.kill_owned_pid(own_pid)

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
    harness.kill_owned_pid(pid)
    if harness.pid_is_movian(pid):
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

def report_reload(args: argparse.Namespace, ok: bool,
                  errors: list[str]) -> int:
    if ok:
        shot = None
        if getattr(args, "shot", False):
            shot = str(harness.take_shot(Instance(args.name)))
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
    ok, errors = harness.do_reload(inst)
    return report_reload(args, ok, errors)


# ---------------------------------------------------------------------------
# watch
# ---------------------------------------------------------------------------

def scan_views(root: Path) -> dict[Path, float]:
    return {p: p.stat().st_mtime for p in root.rglob("*.view")}


def cmd_watch(args: argparse.Namespace) -> int:
    inst = Instance(args.name)
    inst.base_url()  # verify instance is up before entering the loop
    root = Path(args.dir)
    if not root.is_absolute():
        root = harness.REPO_ROOT / root
    if not root.is_dir():
        raise MdevError("watch dir not found: %s" % root)

    seen = scan_views(root)
    print("watching %d .view files under %s (Ctrl-C to stop)"
          % (len(seen), root))
    try:
        while True:
            time.sleep(0.5)
            current = scan_views(root)
            changed = sorted(
                str(p.relative_to(root)) for p in
                (set(current) ^ set(seen))
                | {p for p in current if p in seen
                   and current[p] != seen[p]}
            )
            seen = current
            if not changed:
                continue
            # Tighter settle than plain `mdev reload` so the report lands
            # within 2 s of the file change (0.5 s poll + 1.2 s settle);
            # view errors surface within milliseconds of ReloadUI anyway.
            ok, errors = harness.do_reload(inst, settle=1.2)
            stamp = time.strftime("%H:%M:%S")
            if ok:
                line = "[%s] %s: RELOAD OK" % (stamp, ", ".join(changed))
                if args.shot:
                    line += " shot=%s" % harness.take_shot(inst)
            else:
                line = "[%s] %s: %d error(s): %s" % (
                    stamp, ", ".join(changed), len(errors), errors[0])
            print(line, flush=True)
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

    offset = harness.log_size(inst)
    result = harness.open_and_wait(inst, route, timeout=20.0)

    # Settle window: a prop change (page.metadata.glwview) is dispatched
    # to the GLW thread asynchronously, so a GLW view-parse error (or our
    # own "viewpreview: ERROR:" line, which the JS route handler can also
    # emit slightly after loading/title go ready) can land a beat after
    # open_and_wait already sees the page as ready. Poll briefly rather
    # than reading the log delta once immediately (same idea as
    # do_reload's settle window).
    deadline = time.monotonic() + 1.5
    errors: list[str] = []
    while time.monotonic() < deadline:
        delta = harness.read_log_delta(inst, offset)
        errors = (harness.viewpreview_error_lines(delta)
                 + harness.view_error_lines(delta))
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

    run = sub.add_parser("run", parents=[common],
                         help="launch an isolated Movian instance")
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

    reload_ = sub.add_parser("reload", parents=[common],
                             help="ReloadUI and grep the log for view errors")
    reload_.add_argument("--shot", action="store_true",
                         help="screenshot after a clean reload")
    reload_.set_defaults(func=cmd_reload)

    watch = sub.add_parser("watch", parents=[common],
                           help="auto-reload when .view files change")
    watch.add_argument("--dir", default="glwskins/flat",
                       help="directory to watch (default: glwskins/flat)")
    watch.add_argument("--shot", action="store_true",
                       help="screenshot after each clean reload")
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

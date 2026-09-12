"""Core plumbing for the mdev CLI: instance state, process guard,
launch/stop, HTTP helpers, prop access, log scanning and screenshots.

Python 3 stdlib only.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, NamedTuple

from . import movian_diag_snapshot as diag

STATE_ROOT = Path("/tmp/mdev")

# repo root: this file is <root>/support/devtools/mdevlib/harness.py
REPO_ROOT = Path(__file__).resolve().parents[3]

MOVIAN_BINARY = "./build.debug/movian"
MDEV_PTRACE_ENV = "MOVIAN_MDEV_ALLOW_GDB"

# The viewpreview dev plugin (issue #87): `mdev preview` auto-starts an
# instance with just this plugin loaded if none is running yet.
VIEWPREVIEW_DIR = REPO_ROOT / "support" / "devtools" / "viewpreview"

PORT_RE = re.compile(r"http-server: Listening on port (\d+)")

# GLW view load/parse errors as emitted by glw_view_seterr()
# (src/ui/glw/glw_view_support.c): "Error <file>:<line>: <message>"
VIEW_ERROR_RE = re.compile(r"GLW\s+\[ERROR\]:\s*Error (.+?):(\d+): (.*)$")

# viewpreview.js's fail() logs "viewpreview: ERROR: <msg>" (see
# support/devtools/viewpreview/viewpreview.js); its non-error status line
# ("viewpreview: showing ...") deliberately does not match this.
VIEWPREVIEW_ERROR_RE = re.compile(r"viewpreview:\s*ERROR:")

# `mdev reload --js` (issue #93): action ReloadData -> plugins_reload_dev_plugin()
# (src/plugins.c:1453) logs one of these two lines per `-p` dev plugin.
RELOAD_JS_OK_RE = re.compile(r"Reloaded dev plugin (\S+)")
RELOAD_JS_FAIL_RE = re.compile(r"Unable to reload development plugin: (\S+)")

# Compile-error fallback (issue #93 spike finding): plugin_load()
# (src/plugins.c:611) unconditionally returns 0 for an "ecmascript" plugin
# even when ecmascript_plugin_load() fails to compile the JS -- so
# "Reloaded dev plugin <path>" can appear ALONGSIDE this compile-error
# trace for the very same failed reload. Treat this line as authoritative
# over a same-tick "Reloaded" line for the same plugin.
RELOAD_JS_COMPILE_ERROR_RE = re.compile(r"Unable to compile (\S+) -- (.*)$")

# Error-signal set; the single source of truth for what a generic "error
# line" is (movian_agent.py's SIGNALS["errors"] imports this). GLW view
# errors and viewpreview failures have their own shapes above and are
# matched alongside this in error_lines().
ERROR_SIGNALS = re.compile(
    r"TypeError|ReferenceError|Cannot read property|Unable to load image|"
    r"Unknown format|\|E\||CRASH|assert|Segmentation fault",
    re.IGNORECASE,
)

# nav_open0() (src/navigator.c:763) TRACEs this for every processed open
# event -- the only deterministic signal that a queued /api/open actually
# ran (the prop tree alone can't distinguish "old page still showing" from
# "same URL re-opened").
# To END OF LINE, not `\S+`. A URL is not one non-space token: the search bar
# concatenates the user's query raw (`glwskins/flat/theme.view:227`) and
# `es_route.c:236-240` pushes the capture undecoded, so any multi-word search
# produces `Opening canonproof:search:red lipstick`. `\S+` captured
# `canonproof:search:red`, the equality below failed, and `nav_seen` stayed
# false for a navigation that had already happened -- movian#182's "any URL
# containing a space", which this file's own comments had put down to
# `openerror` alone. Both were real; only one of them had been fixed.
NAV_OPENING_RE = re.compile(r"navigator.*?Opening (.+)")

IMAGE_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
]

PAGE_URL = "global/navigators/current/currentpage/url"
PAGE_LOADING = "global/navigators/current/currentpage/model/loading"
PAGE_TITLE = "global/navigators/current/currentpage/model/metadata/title"
PAGE_TYPE = "global/navigators/current/currentpage/model/type"
PAGE_ERROR = "global/navigators/current/currentpage/model/error"
# How long an absent `loading` must keep looking finished before it is
# believed. Long enough for a backend that is about to fail to say so,
# short enough not to matter to a page that really has no loading prop.
ABSENT_LOADING_SETTLE = 1.0
# `/api/open` accepts and answers 302 before the navigator will consume the
# event. Measured on the stand: an open issued 1s after `mdev run` returns is
# discarded with no trace in the log -- and so is `page:settings`, which needs
# no plugin, so it is neither route registration nor the backend. At ~3s the
# same request works. The GET is idempotent, so the honest answer is to issue
# it again rather than wait longer: waiting cannot recover a dropped event,
# which is why raising `--timeout` never helped (movian#233).
NAV_REISSUE_AFTER = 1.5
NAV_REISSUE_LIMIT = 4
PAGE_NODES = "global/navigators/current/currentpage/model/nodes"
# A popup parks the route that raised it. `native/popup.message` is
# synchronous -- `es_message` switches on `message_popup()`'s return
# (es_misc.c:154-181) and `message_popup` blocks in `popup_display()`
# (notifications.c:223-262) -- so the handler never reaches
# `page.loading = false` and the prop is never created. Reading that absence
# as "this route publishes no loading prop" reported a parked page as ready.
POPUPS_PROP = "global/popups"


class MdevError(Exception):
    """Failure with a one-line reason; carries the process exit code."""

    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


class Instance:
    """One named mdev-managed Movian instance under /tmp/mdev/<name>/."""

    def __init__(self, name: str):
        # Must contain at least one non-dot char: "." / ".." would resolve
        # the state dir outside /tmp/mdev/.
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name) or set(name) == {"."}:
            raise MdevError("invalid instance name: %r" % name)
        self.name = name
        self.dir = STATE_ROOT / name
        self.state_path = self.dir / "state.json"
        self.log_path = self.dir / "movian.log"
        self.persistent = self.dir / "persistent"
        self.cache = self.dir / "cache"
        self.shots = self.dir / "shots"

    def ensure_dirs(self) -> None:
        for d in (self.dir, self.persistent, self.cache, self.shots):
            d.mkdir(parents=True, exist_ok=True)

    def load_state(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write_state(self, state: dict[str, Any]) -> None:
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.replace(self.state_path)

    def save_state(self, state: dict[str, Any]) -> None:
        self.ensure_dirs()
        with (self.dir / "state.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self._write_state(state)

    def record_shot_hash(self, sha256_hex: str, shot_path: Path) -> None:
        """Atomically merge screenshot metadata into current process state."""
        self.ensure_dirs()
        with (self.dir / "state.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = self.load_state() or {}
            state["last_shot_hash"] = sha256_hex
            state["last_shot_path"] = str(shot_path.resolve())
            self._write_state(state)

    def live_pid(self) -> int | None:
        """Pid from state.json if it is alive and still THIS instance's
        movian process (comm + cmdline check, see owns_pid())."""
        state = self.load_state()
        if not state:
            return None
        pid = state.get("pid")
        if isinstance(pid, int) and self.owns_pid(pid):
            return pid
        return None

    def owns_pid(self, pid: int) -> bool:
        """True only when `pid` is a movian process launched against this
        instance's own --persistent dir. The comm check alone is not
        enough: a stale state.json pid recycled by an UNRELATED movian
        would pass it, and stop/--force would then signal a foreign
        process."""
        if not pid_is_movian(pid):
            return False
        try:
            cmdline = Path("/proc/%d/cmdline" % pid).read_bytes()
        except OSError:
            return False
        return str(self.persistent).encode() in cmdline.split(b"\0")

    def base_url(self) -> str:
        state = self.load_state()
        if not state or not state.get("port"):
            raise MdevError(
                "instance %r is not running (no port in state.json); "
                "start it with: mdev run --name %s" % (self.name, self.name)
            )
        pid = self.live_pid()
        if pid is None:
            raise MdevError(
                "instance %r is not running (pid from state.json is dead)"
                % self.name
            )
        return "http://127.0.0.1:%d" % state["port"]


# ---------------------------------------------------------------------------
# Process guard
# ---------------------------------------------------------------------------

def movian_procs() -> list[tuple[int, str]]:
    """All live (pid, cmdline) pairs that run the movian binary.
    `cmdline` is the raw `pgrep -fa` argv string (space-joined).

    Uses `pgrep -fa movian` for broad candidate collection, then accepts
    either a direct movian process via /proc/<pid>/comm or a wrapper whose
    argv names an existing movian executable. Requiring an executable path
    avoids repository-name false positives such as `buksa/movian` (#119).
    """
    try:
        out = subprocess.run(
            ["pgrep", "-fa", "movian"],
            capture_output=True, text=True, check=False,
        ).stdout
    except OSError as error:
        raise MdevError("pgrep failed: %s" % error)
    procs = []
    for line in out.splitlines():
        try:
            pid_str, cmdline = line.split(" ", 1)
        except ValueError:
            continue
        pid = int(pid_str)
        if not pid_is_movian(pid) and not any(
            "/" in token
            and os.path.basename(token) == "movian"
            and os.path.isfile(token)
            for token in cmdline.split()
        ):
            continue  # /proc disappeared or no movian executable
        procs.append((pid, cmdline))
    return [(p, c) for p, c in procs if p != os.getpid()]


def classify_foreign(
    inst: "Instance", own_pid: int | None
) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Split live movian pids (excluding `own_pid`) into (coexistable
    foreign, same-dir collisions) (issue #94).

    A "collision" is a live movian pid whose cmdline references this
    instance's own `--persistent` path -- i.e. something is running against
    our state dir that `state.json` did not confirm as `own_pid` (stale/
    corrupted state.json, or a race). That case still refuses (exit 2):
    coexistence is only safe for genuinely separate instances/profiles.
    Every other live movian pid is a "foreign" instance -- isolated profile,
    dynamic port, no state.json overlap -- safe to warn-and-coexist with.
    """
    persistent_str = str(inst.persistent)
    foreign: list[tuple[int, str]] = []
    collisions: list[tuple[int, str]] = []
    for pid, cmdline in movian_procs():
        if pid == own_pid:
            continue
        if persistent_str in cmdline:
            collisions.append((pid, cmdline))
        else:
            foreign.append((pid, cmdline))
    return foreign, collisions


def coexist_warning(foreign: list[tuple[int, str]]) -> str:
    """One-line coexistence warning naming each foreign pid + cmdline
    (issue #94 contract)."""
    return "coexisting with foreign movian: " + "; ".join(
        "pid %d (%s)" % (pid, cmdline) for pid, cmdline in foreign
    )


def collision_refusal(inst: "Instance",
                      collisions: list[tuple[int, str]]) -> MdevError:
    """The exit-2 refusal for a same-dir collision (issue #94): a live
    movian pid uses this instance's own --persistent path but state.json
    can't confirm it as ours (stale/corrupted state, or a race). Shared
    by `mdev run` and ensure_running() so the message can't drift."""
    return MdevError(
        "refusing to start: live movian pid(s) using %s are not "
        "confirmed as instance %r's own process by state.json: %s -- "
        "this instance's state may be corrupted; investigate before "
        "retrying (don't --force blindly)." % (
            inst.persistent, inst.name,
            ", ".join("%d (%s)" % (p, c) for p, c in collisions),
        ),
        exit_code=2,
    )


def pid_is_movian(pid: int) -> bool:
    try:
        comm = Path("/proc/%d/comm" % pid).read_text().strip()
    except OSError:
        return False
    return comm == "movian"


def kill_owned_pid(inst: "Instance", pid: int, timeout: float = 5.0) -> str:
    """Terminate a pid this instance owns per state.json.  Refuses to
    signal anything whose comm+cmdline do not prove it is this instance's
    own movian (stale-pid safety: a recycled pid -- even one recycled by
    another movian -- is hands-off).

    Returns the stop outcome: ``"stopped-clean"`` (SIGTERM was sufficient
    or the pid was already gone), ``"killed-after-timeout"`` (SIGKILL
    escalation was needed), or ``"still-alive"`` (the owned pid still
    appeared live after SIGKILL)."""
    if not inst.owns_pid(pid):
        return "stopped-clean"  # already gone or pid recycled: hands off
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return "stopped-clean"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not inst.owns_pid(pid):
            return "stopped-clean"
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return "killed-after-timeout"
    time.sleep(0.2)
    if inst.owns_pid(pid):
        return "still-alive"
    return "killed-after-timeout"


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------

# How the core names a plugin loaded with `-p`: `plugin_load(path, "dev")`
# (plugins.c:1435, 1465) and `fqid = "<manifest id>@<origin>"`
# (plugins.c:240). Callers pass the manifest id and this is appended, so the
# suffix stays an implementation detail rather than something to get wrong.
PLUGIN_DEV_ORIGIN = "dev"


class PluginSetting(NamedTuple):
    plugin_id: str
    group: str
    key: str
    value: Any


def parse_plugin_setting(spec: str) -> PluginSetting:
    """Parse `<plugin-id>:<group>:<key>=<value>`.

    Only the first two `:` and the first `=` after them are structure. A
    domain or a cookie is an ordinary setting value and carries both.

    `true`/`false` become 1 and 0, which is what Movian holds: the setting
    prop reads `type = bool, value = 1`, `setvalue` stores what
    `prop.subscribeValue` yields (settings.js:78-83, 302-304), and
    `getvalue` hands it back RAW with no coercion (settings.js:298-300). A
    digit string becomes an int for the same reason.

    Those two rules are a guess about the DECLARED type, which mdev cannot
    see -- `createString` and `createInt` write the same file. So a value in
    double quotes is taken literally, which is the only way to seed the
    string `"2160"` or the string `"true"`.

    The first `=` after the two colons separates key from value, so a VALUE
    may contain `=` (a cookie, a query string) and a KEY may not. A setting
    id containing `=` is legal to the core -- `settings.js` passes the id
    straight to the prop tree and the store -- and cannot be addressed by
    this grammar: `a=b=1` reads as key `a`. Nothing can detect which was
    meant, so the guess is not narrowed here; instead `mdev run` prints the
    key and value it took from every spec, so a wrong split is visible in
    the output rather than only in the plugin's behaviour. The same is true
    of the value coercion above, which that line also makes visible.
    """
    plugin_id, sep, assignment = spec.partition(":")
    group, sep2, rest = assignment.partition(":")
    if not sep or not sep2:
        raise MdevError(
            "--plugin-setting expects <plugin-id>:<group>:<key>=<value>, "
            "got %r" % spec)
    key, sep3, value = rest.partition("=")
    if not sep3:
        raise MdevError("--plugin-setting %r has no <key>=<value>" % spec)
    if not plugin_id or not group or not key:
        raise MdevError(
            "--plugin-setting %r has an empty plugin id, group or key"
            % spec)
    # Both halves that become path components, checked HERE rather than
    # where they are joined. `plugin_setting_path` refused an id like
    # `../P` too, but that runs in the plan -- after `mdev run --force` has
    # already stopped the instance for a request that cannot proceed.
    _one_path_segment("plugin id", plugin_id)
    _one_path_segment("settings group", group)
    return PluginSetting(plugin_id, group, key, _coerce_setting(value))


# What a plugin can actually hold. `store.js` parses the seed with
# `JSON.parse`, and a Duktape Number is a double (DUK_TYPE_NUMBER,
# ext/duktape/duktape.h:267), so an integer past the exactly-representable
# range comes back as a DIFFERENT integer: 9007199254740993 is written
# exactly and read as 9007199254740992. Measured. `--dev-flags` is not
# affected -- those go to htsmsg, read by the core in C -- so the limit
# belongs here and not in `coerce_scalar`.
MAX_EXACT_SETTING_INT = 2 ** 53 - 1


def _coerce_setting(value: str) -> Any:
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    if value == "true":
        return 1
    if value == "false":
        return 0
    coerced = coerce_scalar(value)
    if isinstance(coerced, int) and abs(coerced) > MAX_EXACT_SETTING_INT:
        raise MdevError(
            "--plugin-setting value %s is outside the range a plugin can "
            "read back exactly (+/-%d, Number.MAX_SAFE_INTEGER): store.js "
            "parses the seed with JSON.parse and a Duktape Number is a "
            "double, so the plugin would see %d instead. Quote it to seed "
            "the string."
            % (value, MAX_EXACT_SETTING_INT, int(float(coerced))))
    return coerced


def _one_path_segment(kind: str, value: str) -> str:
    """Refuse anything that is not a single, literal path component.

    `Path("a") / "/tmp/x"` is `/tmp/x` -- pathlib discards everything before
    an absolute part -- so an absolute group name walked straight out of the
    profile and merged into whatever JSON file it landed on. `..` walked out
    the other way. The core concatenates strings (settings.js:297) and never
    escapes anywhere, so this is mdev's hazard, not Movian's, and refusing
    is the whole fix: a settings group is an identifier, not a path.
    """
    if not value or value in (".", "..") or "/" in value or "\\" in value:
        raise MdevError(
            "%s %r must be a single path component -- no separators, no "
            "`..` -- because it names a file inside the plugin's own "
            "profile" % (kind, value))
    return value


def plugin_setting_path(persistent: Path, plugin_id: str,
                        group: str) -> Path:
    """Where `globalSettings` keeps one group for one dev-loaded plugin.

    `Core.storagePath` is `<persistent>/plugins/<fqid>`
    (ecmascript.c:881-882) and the group is a JSON file under `settings/`
    there (settings.js:276,297; store.js:20-21).
    """
    fqid = "%s@%s" % (_one_path_segment("plugin id", plugin_id),
                      PLUGIN_DEV_ORIGIN)
    return (persistent / "plugins" / fqid / "settings"
            / _one_path_segment("settings group", group))


def _refuse_json_constant(token: str):
    raise ValueError(
        "%s is not valid JSON to Movian -- `JSON.parse` rejects it and "
        "store.js:48-51 swallows the failure, leaving an empty store"
        % token)


def _no_duplicate_members(pairs: list[tuple[str, Any]]) -> dict:
    """`json.loads` keeps the LAST of a repeated member; the core keeps the
    first. `htsmsg_json_deserialize2` appends every field
    (htsmsg.c:66, TAILQ_INSERT_TAIL) and `htsmsg_get_str` resolves through
    `htsmsg_field_find`, which walks from the head and returns the first
    match (htsmsg.c:102-105). Neither side rejects the repeat.

    So `{"id":"P","id":"Q"}` is `Q` here and `P` there: mdev would accept a
    spec for `Q`, seed `Q@dev`, report success, and the core would create
    `P@dev` and never read it. Refused rather than mirrored -- a manifest
    that says `id` twice is a bug its author should see, and mirroring would
    make mdev right about an id nobody meant.

    Only the manifest. A settings store is written here and read by Duktape,
    and `JSON.parse` keeps the last member exactly as Python does, so there
    is no disagreement to reconcile there.
    """
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(
                "member %r appears more than once (%r then %r). The core "
                "would take the first and this reads the last, so they "
                "would disagree about the same file"
                % (key, seen[key], value))
        seen[key] = value
    return seen


def _load_manifest(plugin_dir: str) -> dict:
    """A `-p` directory's manifest, or an MdevError saying which part failed.

    Raises rather than returning None. A `-p` directory whose manifest
    cannot be read is a fact worth saying: swallowing it dropped the plugin
    from the known set, and the refusal downstream then reported "not among
    the -p plugins: (none given)" -- pointing at the id the caller typed
    instead of at the manifest that could not be parsed.
    """
    manifest_path = Path(plugin_dir) / "plugin.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"),
                              object_pairs_hook=_no_duplicate_members)
    except OSError as error:
        raise MdevError("cannot read %s: %s" % (manifest_path, error))
    except json.JSONDecodeError as error:
        raise MdevError("%s is not valid JSON: %s" % (manifest_path, error))
    except ValueError as error:
        # Not a decode failure: valid JSON the core would read differently.
        raise MdevError("%s cannot be used as a manifest: %s"
                        % (manifest_path, error))
    # Parsing is not the same as being a manifest. `[]` gets through
    # json.loads and then `.get` raises AttributeError -- a traceback where
    # this promises an MdevError.
    if not isinstance(manifest, dict):
        raise MdevError(
            "%s is valid JSON but not an object (%s), so it declares no id"
            % (manifest_path, type(manifest).__name__))
    return manifest


def plugin_manifest(plugin_dir: str) -> tuple[str, str]:
    """A plugin's declared (id, type).

    The type matters: `plugins.c:674` sends `"views"` down a branch that
    never calls `ecmascript_plugin_load`, so no ES context is created, no
    `Core.storagePath` exists and `globalSettings` is never reached. A seed
    for such a plugin is a file nothing will ever read, reported as success.
    """
    manifest = _load_manifest(plugin_dir)
    return _manifest_id(plugin_dir, manifest), str(manifest.get("type") or "")


def plugin_manifest_id(plugin_dir: str) -> str:
    """The `id` a plugin declares, which is what the core builds fqid from."""
    return _manifest_id(plugin_dir, _load_manifest(plugin_dir))


# `\uXXXX` with an uppercase hex digit, which the core decodes wrongly.
# json.c:71 computes `*s - 'F' + 10`, so A-F yield 5-10 instead of 10-15 and
# `"P\u004A"` becomes `PE` in the core and `PJ` here -- mdev would seed
# `PJ@dev` while the core created `PE@dev`. Filed as movian#250; until it is
# fixed, an id whose raw text carries such an escape is refused rather than
# seeded into a profile no plugin will read. Scoped to the id: the same
# escape in a synopsis is the core's problem and not this seed's.
_RAW_ID = re.compile(r'"id"\s*:\s*"((?:[^"\\]|\\.)*)"')
_UPPER_ESCAPE = re.compile(r'\\u[0-9a-fA-F]*[A-F]')


def _manifest_id(plugin_dir: str, manifest: dict) -> str:
    value = manifest.get("id")
    manifest_path = Path(plugin_dir) / "plugin.json"
    if not isinstance(value, str) or not value:
        raise MdevError("%s declares no \"id\"" % manifest_path)
    raw = _RAW_ID.search(manifest_path.read_text(encoding="utf-8"))
    if raw is not None and _UPPER_ESCAPE.search(raw.group(1)):
        raise MdevError(
            "%s spells its id with an escape the core decodes differently: "
            "%s. json.c:71 computes uppercase hex as `*s - 'F' + 10`, so "
            "A-F yield 5-10 instead of 10-15 -- the core would build a "
            "different fqid than this reads, and the seed would land where "
            "nothing looks (movian#250). Spell the id literally, or in "
            "lowercase hex." % (manifest_path, raw.group(1)))
    return value


def resolve_plugin_settings(plugins: list[str],
                            specs: list[str]) -> list[PluginSetting]:
    """Everything that can be judged without touching instance state.

    Specs parsed, manifests read, ids and types checked -- no store is
    opened, no directory made, nothing killed. `mdev run --force` stops the
    running instance BEFORE it would otherwise have got here, so a malformed
    setting or an unknown id used to terminate a working instance for a
    request that was never going to run.
    """
    if not specs:
        return []
    parsed = [parse_plugin_setting(spec) for spec in specs]
    known = {}
    kinds = {}
    for plugin in plugins:
        plugin_id, kind = plugin_manifest(plugin)
        known[plugin_id] = plugin
        kinds[plugin_id] = kind
    for spec, setting in zip(specs, parsed):
        if setting.plugin_id not in known:
            unaddressable = sorted(i for i in known if ":" in i)
            raise MdevError(
                "--plugin-setting %r names plugin %r, which is not among "
                "the -p plugins: %s. Pass the id from its plugin.json; the "
                "@%s the core appends is added here.%s"
                % (spec, setting.plugin_id,
                   ", ".join(sorted(known)) or "(none given)",
                   PLUGIN_DEV_ORIGIN,
                   ("  Note that %s cannot be addressed by this flag at all: "
                    "a ':' in the id collides with the spec's own separators."
                    % ", ".join(repr(i) for i in unaddressable))
                   if unaddressable else ""))
        if kinds[setting.plugin_id] != "ecmascript":
            raise MdevError(
                "--plugin-setting %r targets plugin %r, whose manifest "
                "declares type %r. Only an ecmascript plugin gets an ES "
                "context, and only that context has the storagePath these "
                "settings live under (plugins.c:674, 702-727) -- the file "
                "would be written and never read."
                % (spec, setting.plugin_id, kinds[setting.plugin_id]))
    return parsed


def _check_destination(persistent: Path, path: Path) -> None:
    """Refuse a destination that is not a plain file inside the profile.

    The path-component guard promises that a seed stays in the plugin's own
    profile, and a symlink breaks that promise from the other side: both
    `is_file()` and the write follow one, so a group symlinked at an
    unrelated JSON file merged into it. Measured.

    A leaf that is a DIRECTORY is the other half. `is_file()` reads it as
    "no store yet", the parent preflight passes because it only looks at the
    parent, and the commit loop then raised an uncaught IsADirectoryError --
    after earlier targets were already written, which is the partial seed
    the two-phase design exists to prevent.
    """
    root = os.path.realpath(persistent)

    # Walk from the leaf up to the profile root and no further. An earlier
    # version walked `path.parents` and skipped non-existent candidates with
    # `continue`, which skipped the stop condition with them and then
    # reported the profile's own parent as "outside".
    candidates = []
    current = path
    while True:
        candidates.append(current)
        if current == persistent or current.parent == current:
            break
        current = current.parent

    for candidate in candidates:
        if candidate.is_symlink():
            raise MdevError(
                "refusing to seed through the symlink %s: a seed must stay "
                "inside the plugin's own profile" % candidate)
        if not candidate.exists():
            continue
        if candidate == persistent:
            continue
        if os.path.commonpath([root, os.path.realpath(candidate)]) != root:
            raise MdevError(
                "refusing to seed %s: it resolves outside %s"
                % (candidate, persistent))
        if candidate == path and not candidate.is_file():
            raise MdevError(
                "refusing to seed %s: it exists and is not a regular file"
                % path)
        # A hard link is the same escape as a symlink with nothing to
        # inspect: no target path, `realpath` inside the profile, a regular
        # file -- and one inode with another name somewhere else. Measured:
        # a leaf linked at `outside.json` turned `{"mine": true}` into
        # `{"mine": true, "pwned": 1}`. The commit below moves a new file
        # into place rather than writing through this one, so the alias
        # would survive either way; refusing says so instead, which is what
        # the symlink case next door does. (Only the leaf: a directory
        # always has nlink > 1.)
        if candidate == path and candidate.stat().st_nlink > 1:
            raise MdevError(
                "refusing to seed %s: it has %d names, so it is also a file "
                "outside the plugin's profile"
                % (path, candidate.stat().st_nlink))


def plan_plugin_settings(
        persistent: Path, plugins: list[str],
        specs: list[str]) -> list[tuple[Path, dict[str, Any]]]:
    """Resolve and validate every seed, writing nothing.

    Split from the commit so a caller can find out the whole request is
    sound BEFORE it writes anything of its own -- `mdev run` also seeds the
    core's dev flags, and those used to land first, staying active for the
    next run while the command reported failure and launched nothing.

    Merges into whatever is already there. A persistent instance carries
    settings somebody set by hand, and one seeded key must not wipe the
    rest.
    """
    if not specs:
        return []
    resolve_plugin_settings(plugins, specs)
    known = {}
    kinds = {}
    for plugin in plugins:
        plugin_id, kind = plugin_manifest(plugin)
        known[plugin_id] = plugin
        kinds[plugin_id] = kind

    grouped: dict[Path, dict[str, Any]] = {}
    for spec in specs:
        plugin_id, group, key, value = parse_plugin_setting(spec)
        if plugin_id not in known:
            # A manifest id may contain ':' -- plugins.c:632-647 takes the
            # string as given -- and this flag's grammar cannot address one,
            # because the first two colons are structure. Detectable only
            # here, so it is said here rather than left as a puzzle.
            unaddressable = sorted(i for i in known if ":" in i)
            raise MdevError(
                "--plugin-setting %r names plugin %r, which is not among "
                "the -p plugins: %s. Pass the id from its plugin.json; the "
                "@%s the core appends is added here.%s"
                % (spec, plugin_id,
                   ", ".join(sorted(known)) or "(none given)",
                   PLUGIN_DEV_ORIGIN,
                   ("  Note that %s cannot be addressed by this flag at all: "
                    "a ':' in the id collides with the spec's own separators."
                    % ", ".join(repr(i) for i in unaddressable))
                   if unaddressable else ""))
        if kinds[plugin_id] != "ecmascript":
            raise MdevError(
                "--plugin-setting %r targets plugin %r, whose manifest "
                "declares type %r. Only an ecmascript plugin gets an ES "
                "context, and only that context has the storagePath these "
                "settings live under (plugins.c:674, 702-727) -- the file "
                "would be written and never read."
                % (spec, plugin_id, kinds[plugin_id]))
        grouped.setdefault(
            plugin_setting_path(persistent, plugin_id, group), {})[key] = value

    # Two phases. Writing as it went meant a later malformed target left
    # the earlier files already seeded while the command reported failure
    # and launched nothing -- a profile carrying settings from an operation
    # that said it had not happened.
    planned: list[tuple[Path, dict[str, Any]]] = []
    for path, values in grouped.items():
        _check_destination(persistent, path)
        existing: dict[str, Any] = {}
        if path.is_file():
            try:
                loaded = json.loads(
                    path.read_text(encoding="utf-8"),
                    # Python accepts NaN/Infinity and would write them back;
                    # `JSON.parse` rejects them and store.js swallows that
                    # silently (`catch (e) {}`, store.js:48-51), leaving the
                    # plugin an EMPTY store. The seed would report success
                    # and the prompt it was meant to bypass would appear.
                    parse_constant=_refuse_json_constant)
            except (OSError, ValueError) as error:
                raise MdevError(
                    "cannot merge into %s: it exists and cannot be read as "
                    "JSON (%s), so seeding would discard it" % (path, error))
            # Same distinction the manifest reader needs: parsing is not
            # being the right shape. A `[]` here used to fall through to an
            # empty dict and then be written over -- discarded silently, by
            # the very code whose refusal above promises not to.
            if not isinstance(loaded, dict):
                raise MdevError(
                    "cannot merge into %s: it holds a JSON %s, not an "
                    "object, so seeding would discard it"
                    % (path, type(loaded).__name__))
            existing = loaded
        existing.update(values)
        planned.append((path, existing))

    # Every destination, before any content. A profile directory that is
    # actually a regular file only fails at mkdir, and doing that inside the
    # write loop committed the earlier plugin before the later one blew up.
    # A failure here leaves empty directories and no seeds, which is the
    # bound this can offer without a staging area.
    for path, _ in planned:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise MdevError(
                "cannot create %s for the seed: %s" % (path.parent, error))

    # And that it can take a file. The commit moves a staged sibling into
    # place, so the LEAF's own mode is irrelevant -- a read-only store used
    # to raise an uncaught PermissionError from the middle of the write
    # loop, after earlier targets were already seeded -- but the directory's
    # is not. Checked here because the mkdir above is what makes it exist.
    for path, _ in planned:
        if not os.access(path.parent, os.W_OK | os.X_OK):
            raise MdevError(
                "cannot seed %s: %s is not writable" % (path, path.parent))
    return planned


def commit_plugin_settings(
        planned: list[tuple[Path, dict[str, Any]]]) -> list[Path]:
    """Write what `plan_plugin_settings` resolved; return the files touched.

    Every file is staged as a sibling and moved into place, and every
    staging happens before any move. Writing directly meant a target that
    could not be written -- a read-only store -- raised from the middle of
    the loop with earlier targets already seeded, which is the partial state
    the plan exists to prevent, reached after the plan had approved
    everything. Moving also means each file appears whole or not at all, and
    that the leaf's own mode and link count do not matter.

    The remaining bound: a failure in the move loop can leave earlier files
    replaced. Nothing short of a transaction closes that, and by then the
    directory has been proven writable and the content proven writable to
    it, so what is left is the disk filling up between the two loops.
    """
    staged: list[tuple[Path, Path]] = []
    try:
        for path, contents in planned:
            staged.append((_stage(path, contents), path))
    except MdevError:
        for tmp, _ in staged:
            tmp.unlink(missing_ok=True)
        raise

    written = []
    for index, (tmp, path) in enumerate(staged):
        try:
            os.replace(tmp, path)
        except OSError as error:
            # Everything not yet moved, including this one. Leaving them
            # meant a repeatedly failing run accumulated complete settings
            # snapshots in the profile under names nothing reads -- litter
            # that looks like state, from an operation that reported
            # failure. What is already moved stays moved; that is the bound
            # the docstring names.
            for leftover, _ in staged[index:]:
                leftover.unlink(missing_ok=True)
            raise MdevError(
                "cannot move the staged seed into %s: %s" % (path, error))
        written.append(path)
    return written


def _stage(path: Path, contents: dict[str, Any]) -> Path:
    """Write one seed to a sibling of `path`, ready to be moved onto it.

    `mkstemp` rather than a name built from `path.name`, and all three
    reasons were defects:

    A predictable sibling can be pre-created. `<group>.mdev-new.<pid>` is
    guessable by anything that can create a file in the profile -- which
    lives under /tmp, whose ancestors mdev creates world-traversable -- and
    a wrapper can fix the pid by pre-creating the link and then exec'ing
    mdev. `write_text` follows a symlink, so it truncated the link's target
    and `os.replace` then installed the LINK as the settings leaf, pointing
    every later seed outside the profile too. Measured: `{"mine": true}`
    became `{"pwned": 1}`. `mkstemp` opens with O_CREAT|O_EXCL and a name
    nothing can predict, so there is nothing to pre-create and nothing to
    follow.

    A name built by appending to `path.name` is longer than `path.name`, so
    a group within the filesystem's limit could have a destination that is
    legal and a staging name that is not -- a stricter, undocumented limit
    than Movian's own, reached after `--force` had already stopped the
    instance. A short fixed prefix is independent of the group.

    And `mkstemp` creates at 0600, which is what a NEW store should be:
    a plugin's settings can hold a session cookie, and the profile's
    ancestors are readable by other local users. That is narrower than the
    0644 Movian itself would write, deliberately. An EXISTING store keeps
    its own mode instead -- replacing the inode used to reset a 0600 store
    to 0644, widening permissions as a side effect of seeding.
    """
    try:
        fd, name = tempfile.mkstemp(dir=str(path.parent), prefix=".mdev-seed.")
    except OSError as error:
        raise MdevError(
            "cannot stage a seed in %s: %s" % (path.parent, error))
    tmp = Path(name)
    try:
        mode = None
        if path.exists():
            mode = stat.S_IMODE(path.stat().st_mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(contents))
        if mode is not None:
            os.chmod(tmp, mode)
    except OSError as error:
        tmp.unlink(missing_ok=True)
        raise MdevError("cannot stage the seed for %s: %s" % (path, error))
    return tmp


def seed_plugin_settings(persistent: Path, plugins: list[str],
                         specs: list[str]) -> list[Path]:
    """Plan and commit in one step, for callers with nothing else to seed."""
    return commit_plugin_settings(
        plan_plugin_settings(persistent, plugins, specs))


def coerce_scalar(value: str) -> Any:
    """An integer if it reads as one, otherwise the string it already is."""
    return int(value) if re.fullmatch(r"-?\d+", value) else value


def parse_dev_flags(spec: str) -> dict[str, Any]:
    """Parse "smbdebug=1,ecmascriptdebug=1" into an htsmsg-JSON dict."""
    flags: dict[str, Any] = {}
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise MdevError("--dev-flags expects k=v pairs, got %r" % item)
        key, value = item.split("=", 1)
        if not key:
            raise MdevError("--dev-flags: empty key in %r" % item)
        flags[key] = coerce_scalar(value)
    if not flags:
        raise MdevError("--dev-flags: no flags parsed from %r" % spec)
    return flags


def build_argv(inst: Instance, plugins: list[str], skin: str | None,
               libav_log: bool, start_url: str | None,
               extra_flags: list[str] | None = None) -> list[str]:
    argv = [
        "stdbuf", "-oL", "-eL",
        MOVIAN_BINARY, "-d",
        "--disable-upgrades",
        "--persistent", str(inst.persistent),
        "--cache", str(inst.cache),
    ]
    for plugin in plugins:
        argv += ["-p", os.path.abspath(plugin)]
    if skin:
        argv += ["--skin", os.path.abspath(skin)]
    if libav_log:
        argv.append("--libav-log")
    if extra_flags:
        argv += extra_flags
    if start_url:
        argv.append(start_url)
    return argv


def launch(inst: Instance, argv: list[str], timeout: float = 30.0) -> dict[str, Any]:
    """Start Movian from the repo root and wait for the HTTP port line."""
    binary = REPO_ROOT / MOVIAN_BINARY
    if not binary.is_file():
        raise MdevError("movian binary not found: %s" % binary)

    inst.ensure_dirs()
    log_fd = open(inst.log_path, "wb", buffering=0)
    log_fd.truncate(0)
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(REPO_ROOT),           # dataroot:// resolves against CWD
            env={**os.environ, MDEV_PTRACE_ENV: "1"},
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,       # survive mdev exiting
        )
    finally:
        log_fd.close()

    port = None
    deadline = time.monotonic() + timeout
    scanned = ""
    offset = 0
    while time.monotonic() < deadline:
        # Incremental scan: only read what movian appended since the last
        # tick (stdbuf -oL keeps the log line-buffered, so the port line
        # never lands split across reads of a growing file).
        size = log_size(inst)
        if size > offset:
            scanned += read_log_delta(inst, offset)
            offset = size
        if proc.poll() is not None:
            tail = "\n".join(scanned.splitlines()[-10:])
            raise MdevError(
                "movian exited with code %s before the HTTP server came up;"
                " log tail:\n%s" % (proc.returncode, tail)
            )
        match = PORT_RE.search(scanned)
        if match:
            port = int(match.group(1))
            break
        time.sleep(0.2)

    if port is None:
        # The child is ours by construction (we hold the Popen handle);
        # kill_owned_pid()'s comm check could miss it if stdbuf has not
        # exec'd into movian yet, so signal the handle directly.
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise MdevError(
            "timed out (%.0fs) waiting for 'Listening on port' in %s"
            % (timeout, inst.log_path)
        )

    state = {
        "name": inst.name,
        "pid": proc.pid,
        "port": port,
        "log": str(inst.log_path),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "argv": argv,
    }
    inst.save_state(state)
    return state


def ensure_running(name: str, plugins: list[str]) -> Instance:
    """Return a live Instance named `name`, launching one with `plugins`
    if it is not already up. Reuses an already-running instance as-is
    (its existing plugin/skin selection wins -- this does not restart
    it even if `plugins` differs). Same coexistence guard as `mdev run`
    (issue #94): warns and proceeds next to a foreign movian instance;
    never touches a movian process this state dir doesn't own, and still
    refuses (exit 2) on a same-dir collision (see `classify_foreign()`).

    Used by `mdev preview` (issue #87) to auto-start the viewpreview
    instance on first use. Passes the existing core CLI flag
    `--bypass-ecmascript-acl` (src/main.c, gconf.bypass_ecmascript_acl):
    the ecmascript file ACL (src/ecmascript/es_fs.c:filename_is_allowed)
    otherwise restricts a plugin's own `fs`/`native/fs` reads to its own
    directory, but viewpreview.js needs to read fixture JSON and check
    view-file paths anywhere in the repo (or a sibling checkout) -- this
    is a pre-existing core flag, not a new C change.
    """
    inst = Instance(name)
    if inst.live_pid() is not None:
        return inst

    foreign, collisions = classify_foreign(inst, None)
    if collisions:
        raise collision_refusal(inst, collisions)
    if foreign:
        print(coexist_warning(foreign), file=sys.stderr)

    inst.ensure_dirs()
    argv = build_argv(inst, plugins, None, False, None,
                      extra_flags=["--bypass-ecmascript-acl"])
    launch(inst, argv)
    return inst


# ---------------------------------------------------------------------------
# HTTP / prop helpers
# ---------------------------------------------------------------------------

def http_request(base_url: str, path: str, timeout: float = 5.0,
                 method: str = "GET",
                 form: dict[str, str] | None = None) -> dict[str, Any]:
    data = None
    headers = {}
    if form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(
        base_url.rstrip("/") + path, data=data, headers=headers, method=method
    )
    try:
        response = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    except (urllib.error.URLError, OSError) as error:
        return {"ok": False, "error": str(error), "path": path}
    with response:
        try:
            body = response.read()
        except OSError as error:
            return {"ok": False, "error": str(error), "path": path}
        return {
            "ok": 200 <= response.status < 400,
            "status": response.status,
            "content_type": response.headers.get("Content-Type"),
            "body": body,
            "path": path,
        }


def get_prop(base_url: str, path: str, timeout: float = 5.0) -> dict[str, Any] | None:
    """Fetch and parse one /api/prop node; None if the prop does not exist."""
    # `:` and `@` are legal in a path segment (RFC 3986 pchar) and Movian's
    # /api/prop does not decode percent-escapes, so encoding them makes a
    # perfectly addressable prop unreachable. A service registered by a plugin
    # is named `plugin:<id>@dev`, and this reported it absent: the raw path
    # answers, the escaped one 404s.
    encoded = urllib.parse.quote(path, safe="/*:@")
    result = http_request(base_url, "/api/prop/" + encoded, timeout)
    if not result.get("ok"):
        return None
    return diag.parse_prop(result["body"].decode("utf-8", "replace"))


def prop_value(base_url: str, path: str, timeout: float = 5.0) -> str | None:
    parsed = get_prop(base_url, path, timeout)
    if parsed is None:
        return None
    return parsed.get("value")


def prop_has_value(value: str | None) -> bool:
    return value not in (None, "", "(void)", "(zombie)")


def pending_popups(base_url: str) -> int | None:
    """How many popups are up, or None when the queue could not be read.

    A COUNT, deliberately, after trying identity and finding there is none
    to have. The children are unnamed -- that is why `*N` exists -- and the
    fields differ per kind: a message popup publishes `message`
    (notifications.c:245), an auth prompt publishes `id`/`source`/`reason`
    and no message at all (keyring.c:128-133), a file picker publishes
    `title` (fa_filepicker.c:274-280), a resume dialog `title`/`position`
    (playinfo.c:88-94). Fingerprinting by message therefore called every
    non-message popup unreadable, and an auth prompt pending would have
    failed EVERY open closed -- worse than the defect this guard is for.

    Known and accepted limit of a count: it cannot tell replacement from
    persistence. A bystander popup answered in the same window as this
    route raises its own leaves the total unchanged, and the wait then
    settles on a parked handler. Detecting that needs a stable popup
    identity, which the prop tree does not expose; giving popups one is a
    core change and its own decision.

    None is unreadable and every caller must fail closed on it. Collapsing
    it to zero is how an instrument failure certifies a parked page ready.
    AGENTS.md: a silent instrument is not evidence until the instrument is
    known to be working.
    """
    parsed = get_prop(base_url, POPUPS_PROP)
    if parsed is None:
        return None
    if parsed.get("value") != "directory":
        return 0
    return len(parsed.get("children", []))


def node_count(base_url: str, path: str = PAGE_NODES) -> int:
    parsed = get_prop(base_url, path)
    if parsed is None or parsed.get("value") != "directory":
        return 0
    return len(parsed.get("children", []))


def open_and_wait(inst: Instance, url: str, timeout: float = 20.0) -> dict[str, Any]:
    """GET /api/open for `url` and wait for page-ready; return
    {"url", "title", "type", "nodes"}. Shared by `mdev open` and
    `mdev preview`.
    """
    base = inst.base_url()
    before_url = prop_value(base, PAGE_URL)
    offset = log_size(inst)
    # What was already up before we navigated. A ConnMan credential request
    # (networking/connman.c:341) or a file picker (fa_filepicker.c:296) can
    # be pending for reasons that have nothing to do with this route, and
    # blocking on one would hang every static page:* open until the deadline
    # and blame the route for it.
    popups_before = pending_popups(base)

    def issue_open() -> None:
        result = http_request(
            base, "/api/open?" + urllib.parse.urlencode({"url": url}),
            timeout=5.0)
        if not result.get("ok"):
            raise MdevError("GET /api/open failed: %s"
                            % (result.get("error") or result.get("status")))

    issue_open()
    issued = 1
    issued_at = time.monotonic()

    deadline = time.monotonic() + timeout
    cur_url = title = None
    ready = nav_seen = False
    settled_since: float | None = None
    popups = 0
    while time.monotonic() < deadline:
        # /api/open only QUEUES a nav event. Before trusting the prop
        # tree, require nav_open0()'s per-open "Opening <url>" trace in
        # the log delta -- when `url` is already the open page, the props
        # (same url, loading=0, title set) look "ready" immediately and
        # would otherwise report the OLD page's state as the result.
        if not nav_seen:
            delta = read_log_delta(inst, offset)
            nav_seen = any(m.group(1).rstrip() == url
                           for m in NAV_OPENING_RE.finditer(delta))
            if nav_seen:
                # Grace tick: the trace fires just before the currentpage
                # prop swap becomes visible over HTTP.
                time.sleep(0.3)
            else:
                # No "Opening <url>" yet. Either the navigator has not got to
                # it, or it never will -- the two are indistinguishable from
                # here, and one of them is recoverable, so re-issue.
                if (issued < NAV_REISSUE_LIMIT
                        and time.monotonic() - issued_at
                        >= NAV_REISSUE_AFTER):
                    issue_open()
                    issued += 1
                    issued_at = time.monotonic()
                time.sleep(0.2)
            continue
        # Sampled only after the navigation landed: the popup is created BY
        # the route, so before that there is nothing to see.
        popups = pending_popups(base)

        cur_url = prop_value(base, PAGE_URL)
        loading = prop_value(base, PAGE_LOADING)
        title = prop_value(base, PAGE_TITLE)
        # An error page IS ready -- it is the answer, not a slow arrival.
        # It carries no title, so waiting for one turned a definite refusal
        # into a 20-second timeout reporting `title='(void)'`, which is what
        # movian#182 had been reading as "URLs containing a space": any URL
        # that lands on openerror shows the same symptom, space or not.
        if prop_value(base, PAGE_TYPE) == "openerror" and \
                (cur_url == url or cur_url != before_url):
            raise MdevError(
                "page opened as an error: url=%r %s"
                % (cur_url, prop_value(base, PAGE_ERROR) or "(no detail)")
            )
        # Ready when loading is 0 -- or void/absent: static page:* routes
        # never create the loading prop at all.
        #
        # A title is NOT required. Nothing obliges a route to set one, and
        # demanding it declared a fully rendered page unready: measured on
        # `asyncPageLoad:test:smoke`, whose model carries 40 nodes and a type
        # while metadata/title stays void, and on `devplug:webtest`. The
        # nav_seen gate above is what stops the previous page being read as
        # this one, so the title was never doing that job -- it was only
        # excluding pages that do not have one (movian#182).
        if loading in ("0", "(void)", None):
            # Verify navigation actually targeted our URL: either the page
            # url now equals the requested one, or it at least changed away
            # from what was open before (redirecting backends may rewrite
            # the page url).
            if cur_url == url or cur_url != before_url:
                if loading == "0":
                    # The backend published a finished state; nothing more is
                    # coming.
                    ready = True
                    break
                # An ABSENT `loading` is a weaker statement. A static page:*
                # route never creates the prop -- and neither does a backend
                # that has not started publishing yet, because nav_open0()
                # publishes the URL and runs nav_open_thread() separately, so
                # a slow handler can still end in openerror after this point.
                # Require the state to hold, which gives the openerror check
                # above a chance to fire, rather than trusting one sample.
                #
                # ...but not while a popup is up. A handler parked in
                # popup_display() has not reached `page.loading = false`, so
                # its `loading` is absent for the same reason a static
                # route's is, and settling here reported a parked page as
                # ready with exit 0 -- measured, movian#242.
                #
                # A DEFINITE `loading == "0"` above is still trusted. Taken
                # literally "not ready while a popup is pending" would refuse
                # a page that had already published a finished state and then
                # asked something, and no attribution ties a popup to the
                # route that raised it, so the literal rule would fail pages
                # nothing implicated. The absent-loading case is the one that
                # was measured false-green.
                if settled_since is None:
                    settled_since = time.monotonic()
                elif time.monotonic() - settled_since >= ABSENT_LOADING_SETTLE:
                    # One check, at the commit point, and resampled here
                    # rather than trusted from the top of the tick: an
                    # asynchronous route can raise its popup in between, and
                    # a stale zero would put the false green straight back
                    # for that interleaving.
                    #
                    # An earlier version also reset the timer on every tick
                    # a popup was up. Same outcome, and the redundancy hid
                    # mutations: deleting one of the two left the other
                    # doing the job, so a battery that removed only one came
                    # back green and read as though the guard did not
                    # matter.
                    #
                    # None means the probe could not be read, and that fails
                    # CLOSED -- an unreadable instrument must not be able to
                    # certify a page ready.
                    popups = pending_popups(base)
                    if popups is None or popups_before is None \
                            or popups > popups_before:
                        settled_since = None
                    else:
                        ready = True
                        break
            else:
                settled_since = None
        else:
            settled_since = None
        time.sleep(0.2)

    if not ready:
        raise MdevError(
            "page not ready after %.0fs: nav_event_seen=%r url=%r "
            "loading=%r title=%r (open issued %d time%s)%s"
            % (timeout, nav_seen, cur_url,
               prop_value(base, PAGE_LOADING), title,
               issued, "" if issued == 1 else "s",
               " -- the popup queue could not be read"
               if popups is None or popups_before is None else
               ((" -- %d popup(s) pending, %d of them already up before this "
                 "open; the route is parked until one is answered"
                 % (popups, popups_before))
                if popups > popups_before else ""))
        )

    ptype = prop_value(base, PAGE_TYPE)
    nodes = node_count(base)
    return {"url": cur_url, "title": title, "type": ptype, "nodes": nodes}


# ---------------------------------------------------------------------------
# Log access
# ---------------------------------------------------------------------------

def read_log(inst: Instance) -> str:
    try:
        return inst.log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def log_size(inst: Instance) -> int:
    try:
        return inst.log_path.stat().st_size
    except OSError:
        return 0


def read_log_delta(inst: Instance, offset: int) -> str:
    try:
        with open(inst.log_path, "rb") as f:
            f.seek(offset)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def error_lines(text: str) -> list[str]:
    return [
        line for line in text.splitlines()
        if ERROR_SIGNALS.search(line) or VIEW_ERROR_RE.search(line)
        or VIEWPREVIEW_ERROR_RE.search(line)
    ]


def view_error_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if VIEW_ERROR_RE.search(line)]


def viewpreview_error_lines(text: str) -> list[str]:
    return [line for line in text.splitlines()
            if VIEWPREVIEW_ERROR_RE.search(line)]


def reload_js_ok_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if RELOAD_JS_OK_RE.search(line)]


def reload_js_fail_lines(text: str) -> list[str]:
    """"Unable to reload development plugin" (the errbuf-reported failure
    path) plus the duktape compile-error fallback (see
    RELOAD_JS_COMPILE_ERROR_RE's docstring) -- either is a failure."""
    return [
        line for line in text.splitlines()
        if RELOAD_JS_FAIL_RE.search(line) or RELOAD_JS_COMPILE_ERROR_RE.search(line)
    ]


def reload_line_matches_dir(line: str, plugin_dir: str) -> bool:
    """True when a reload-result log line refers to `plugin_dir`: the path
    token it names is the dir itself ("Reloaded dev plugin <dir>") or a
    file under it (the compile-error line names a .js file). Core logs the
    successful directory as a file:// URL, while state.json records a plain
    absolute path, so normalize that URL before comparing. A plain substring
    test would also credit a sibling dir like <dir>-extra's lines to <dir>.
    """
    for pattern in (RELOAD_JS_OK_RE, RELOAD_JS_FAIL_RE,
                    RELOAD_JS_COMPILE_ERROR_RE):
        match = pattern.search(line)
        if match:
            path = match.group(1)
            if path.startswith("file://"):
                path = urllib.parse.unquote(urllib.parse.urlparse(path).path)
            return (path == plugin_dir
                    or path.startswith(plugin_dir.rstrip("/") + "/"))
    return False


# ---------------------------------------------------------------------------
# Reload / screenshot flows
# ---------------------------------------------------------------------------

def do_reload(inst: Instance, settle: float = 2.0) -> tuple[bool, list[str]]:
    """POST ReloadUI and grep the log delta for GLW view errors.

    Returns (ok, error_lines).
    """
    base = inst.base_url()
    offset = log_size(inst)
    result = http_request(base, "/api/input/action/ReloadUI",
                          timeout=5.0, method="POST")
    if not result.get("ok"):
        raise MdevError(
            "POST /api/input/action/ReloadUI failed: %s"
            % (result.get("error") or result.get("status"))
        )
    deadline = time.monotonic() + settle
    errors: list[str] = []
    while time.monotonic() < deadline:
        errors = view_error_lines(read_log_delta(inst, offset))
        if errors:
            break
        time.sleep(0.2)
    return (not errors, errors)


def plugin_dirs_from_argv(argv: list[str]) -> list[str]:
    """Extract every `-p`/`--plugin` value from a recorded launch argv
    (see `state.json`'s "argv"; `build_argv()` always stores an
    `os.path.abspath()`'d value there)."""
    dirs = []
    i = 0
    while i < len(argv):
        if argv[i] in ("-p", "--plugin") and i + 1 < len(argv):
            dirs.append(argv[i + 1])
            i += 2
        else:
            i += 1
    return dirs


def do_reload_js(inst: Instance, settle: float = 2.0) -> tuple[bool, list[dict]]:
    """POST ReloadData (issue #93) and grep the log delta for the
    per-dev-plugin reload result reported by `plugins_reload_dev_plugin()`
    (src/plugins.c:1453).

    Exit criteria: ok only when every `-p` dev plugin recorded for this
    instance reports "Reloaded dev plugin ..." AND no
    "Unable to reload development plugin"/duktape compile-error line
    matches that plugin's path -- see RELOAD_JS_COMPILE_ERROR_RE's
    docstring for why the compile-error line must win over a same-tick
    "Reloaded" line for the same plugin.

    Returns (ok, per_plugin) where per_plugin is a list of
    {"plugin": <dir or None>, "ok": bool, "detail": <matched log line>}
    (one entry per `-p` dir, plus a trailing entry for any failure line
    that couldn't be attributed to a specific plugin dir).
    """
    state = inst.load_state() or {}
    plugin_dirs = plugin_dirs_from_argv(state.get("argv") or [])
    if not plugin_dirs:
        raise MdevError(
            "instance %r has no dev plugins (-p) to reload with --js"
            % inst.name
        )

    base = inst.base_url()
    offset = log_size(inst)
    result = http_request(base, "/api/input/action/ReloadData",
                          timeout=5.0, method="POST")
    if not result.get("ok"):
        raise MdevError(
            "POST /api/input/action/ReloadData failed: %s"
            % (result.get("error") or result.get("status"))
        )

    deadline = time.monotonic() + settle
    ok_lines: list[str] = []
    fail_lines: list[str] = []
    while time.monotonic() < deadline:
        delta = read_log_delta(inst, offset)
        ok_lines = reload_js_ok_lines(delta)
        fail_lines = reload_js_fail_lines(delta)
        accounted = sum(
            1 for d in plugin_dirs
            if any(reload_line_matches_dir(line, d)
                   for line in ok_lines + fail_lines)
        )
        if accounted >= len(plugin_dirs):
            break
        time.sleep(0.15)

    per_plugin: list[dict] = []
    for plugin_dir in plugin_dirs:
        matched_fail = [line for line in fail_lines
                        if reload_line_matches_dir(line, plugin_dir)]
        matched_ok = [line for line in ok_lines
                      if reload_line_matches_dir(line, plugin_dir)]
        ok = bool(matched_ok) and not matched_fail
        per_plugin.append({
            "plugin": plugin_dir,
            "ok": ok,
            "detail": matched_fail[0] if matched_fail else (
                matched_ok[0] if matched_ok else "no reload result seen"
            ),
        })

    # A failure line that names no known plugin dir still fails the
    # overall reload (belt-and-suspenders; observed to always be
    # attributable in practice -- see RELOAD_JS_COMPILE_ERROR_RE).
    unattributed = [
        line for line in fail_lines
        if not any(reload_line_matches_dir(line, d) for d in plugin_dirs)
    ]
    if unattributed:
        per_plugin.append({"plugin": None, "ok": False,
                           "detail": unattributed[0]})

    overall_ok = all(p["ok"] for p in per_plugin)
    return overall_ok, per_plugin


def sniff_image(body: bytes) -> str | None:
    """Return a file extension for known image magic bytes, else None."""
    for magic, ext in IMAGE_MAGIC:
        if body.startswith(magic):
            return ext
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "webp"
    return None


def take_shot(
    inst: Instance,
    out: str | None = None,
    timeout: float = 15.0,
    if_changed_hash: str | None = None,
) -> tuple[Path | None, str]:
    """Capture a screenshot and return (path, sha256_hex).

    The SHA-256 is computed from the raw response bytes before writing.
    When ``if_changed_hash`` matches, return ``None`` as the path without
    creating, overwriting, or deleting a file.
    """
    base = inst.base_url()
    result = http_request(base, "/api/screenshot/raw", timeout=timeout)
    if not result.get("ok"):
        raise MdevError(
            "GET /api/screenshot/raw failed: %s"
            % (result.get("error") or result.get("status"))
        )
    body = result["body"]
    if not body:
        raise MdevError("screenshot is empty")
    sha256_hex = hashlib.sha256(body).hexdigest()
    ext = sniff_image(body)
    if ext is None:
        raise MdevError(
            "screenshot has unknown magic bytes: %s" % body[:8].hex()
        )
    if if_changed_hash == sha256_hex:
        return None, sha256_hex
    if out:
        path = Path(out)
    else:
        inst.ensure_dirs()
        path = inst.shots / (time.strftime("%Y%m%d-%H%M%S") + "." + ext)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path, sha256_hex

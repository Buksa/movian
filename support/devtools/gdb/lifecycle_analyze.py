#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lifecycle_analyze.py -- JSONL post-processor for the movian GDB lifecycle
collector (Buksa/movian #145).

Reads one or more collector event logs (the JSONL emitted by
``movian_lifecycle.py``) plus the static symbol inventory, and derives four
artifact types:

  * ``init-order.json``       -- INITME init-callback execution order, with
                                 per-callback timing and group attribution.
  * ``fini-order.json``       -- fini-callback order plus a *dynamic* check of
                                 whether fini runs in reverse of init.
  * ``thread-lifecycle.json`` -- thread create/join events with names and
                                 best-effort observed activity spans.
  * ``lifecycle-graph.mmd``   -- a Mermaid order/pairing graph whose edges are
                                 tagged ``confirmed-static`` /
                                 ``confirmed-dynamic`` / ``inferred`` /
                                 ``unknown``.  No inferred edge is ever
                                 rendered as confirmed.

When a plugin-reload window is present (a ``plugins_reload_dev_plugin`` event)
it additionally emits ``resource-balance.json``: a per-kind create/destroy
balance proof for the dev plugin, correlated by (plugin id, es-context
pointer) -- never by raw pointer arithmetic.

All four (five) artifacts are produced from *real* collector JSONL.  The tool
is pure post-processing: it touches no production C/runtime code.

Usage::

    python3 support/devtools/gdb/lifecycle_analyze.py events.jsonl \\
        --inventory support/devtools/gdb/inventory.json \\
        --out-dir /tmp/movian-lifecycle/<RUN_ID> \\
        --run-id <RUN_ID> --scenario <name> [--plugin-id <path>]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import OrderedDict, defaultdict


# INIT_GROUP enum (src/main.h:351): NET=0 API=1 IPC=2 ASYNCIO=3 GRAPHICS=4.
INIT_GROUP_NAMES = {
    0: "INIT_GROUP_NET",
    1: "INIT_GROUP_API",
    2: "INIT_GROUP_IPC",
    3: "INIT_GROUP_ASYNCIO",
    4: "INIT_GROUP_GRAPHICS",
}

# Resource create/destroy symbol pairs, keyed by a short "kind" label used in
# the reload balance proof.  es-resource uses link/unlink rather than
# create/destroy: every resource is linked to its owning context, including
# routes allocated via es_resource_alloc(), and unlink is the matching
# ownership transition.
RESOURCE_PAIRS = OrderedDict([
    ("es-context",   ({"es_context_create"},
                      {"es_context_end"})),
    ("es-resource",  ({"es_resource_link"},
                      {"es_resource_unlink"})),
    ("service",      ({"service_create", "service_createp",
                       "service_create_managed"},
                      {"service_destroy"})),
    ("es-plugin",    ({"ecmascript_plugin_load"},
                      {"ecmascript_plugin_unload"})),
    ("plugin",       ({"plugin_load"},
                      {"plugin_unload"})),
])

RESOURCE_CATEGORIES = {
    "es-context": "es-context",
    "es-resource": "es-resource",
    "service": "service",
    "es-plugin": "es-plugin",
    "plugin": "plugin",
}

# The issue #145 plugin always exercises these kinds.  A zero/zero result for
# one of them is missing evidence, not a balance proof.  Plugin-owned routes,
# subscriptions, services, and timers all cross es_resource_link/unlink;
# counting their lower-level prop/callout side effects would duplicate
# resources and mix in unrelated global runtime activity.
RELOAD_REQUIRED_KINDS = {
    "es-context", "es-resource", "service", "es-plugin", "plugin",
}

CREATE_SYMS = set()
DESTROY_SYMS = set()
for _c, _d in RESOURCE_PAIRS.values():
    CREATE_SYMS |= _c
    DESTROY_SYMS |= _d


# ===========================================================================
# Loading
# ===========================================================================

def load_events(paths):
    """Read exactly one collector JSONL run, ordered by (monotonicNs, seq)."""
    if len(paths) != 1:
        raise SystemExit(
            "exactly one collector event file is required; separate runs "
            "must be analyzed independently")
    events = []
    path = paths[0]
    with open(path) as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(
                    "%s:%d: malformed json: %s" % (path, ln, exc))
            obj.setdefault("__src", os.path.basename(path))
            events.append(obj)
    events.sort(key=lambda e: (e.get("monotonicNs", 0), e.get("seq", 0)))
    return events


def load_inventory(path):
    with open(path) as f:
        inv = json.load(f)
    by_symbol = {}
    init_helpers = []
    init_fns = {}        # symbol -> entry  (entries whose initFn is set)
    fini_fns = {}        # symbol -> entry  (entries whose finiFn is set)
    for e in inv.get("entries", []):
        sym = e.get("symbol")
        by_symbol[sym] = e
        if e.get("category") == "init-helper":
            init_helpers.append(e)
            # An init-helper entry represents either an init callback
            # (initFn set) or a fini callback (finiFn set); some have both.
            if e.get("initFn"):
                init_fns[e["initFn"]] = e
            if e.get("finiFn"):
                fini_fns[e["finiFn"]] = e
    return {
        "raw": inv,
        "by_symbol": by_symbol,
        "init_helpers": init_helpers,
        "init_fns": init_fns,
        "fini_fns": fini_fns,
        "initme_use_count": inv.get("initmeUseCount"),
    }


def _ms(ns):
    if ns is None:
        return None
    return round(ns / 1e6, 3)


def _enter_events(events):
    return [e for e in events if e.get("event") == "enter"]


# ===========================================================================
# init-order.json
# ===========================================================================

def derive_init_order(events, inv):
    enters = _enter_events(events)
    init_fn_set = set(inv["init_fns"])          # symbols that are init callbacks
    init_group_events = [e for e in enters
                         if e.get("symbol") == "init_group"]

    # Observed init-callback executions, in execution order.
    observed = [e for e in enters if e.get("symbol") in init_fn_set]

    t0 = observed[0]["monotonicNs"] if observed else None
    prev = None
    order = []
    for e in observed:
        sym = e["symbol"]
        ent = inv["init_fns"].get(sym, {})
        rec = {
            "seq": e.get("seq"),
            "symbol": sym,
            "group": ent.get("group"),
            "prio": ent.get("prio"),
            "monotonicNs": e.get("monotonicNs"),
            "msFromStart": _ms(e["monotonicNs"] - t0) if t0 is not None else None,
            "msFromPrev": (_ms(e["monotonicNs"] - prev) if prev is not None
                           else 0.0),
            "hasDeclaredFini": ent.get("finiFn") is not None,
            "finiFn": ent.get("finiFn"),
            "thread": e.get("thread"),
        }
        order.append(rec)
        prev = e["monotonicNs"]

    # Registered (per inventory) but never observed executing.
    observed_syms = {e["symbol"] for e in observed}
    not_executed = []
    for sym, ent in inv["init_fns"].items():
        if sym in observed_syms:
            continue
        platforms = ent.get("platforms") or []
        reason = ("platform-gated:%s" % ",".join(platforms)
                  if platforms else "not-hit-or-unbound")
        not_executed.append({
            "symbol": sym, "group": ent.get("group"),
            "finiFn": ent.get("finiFn"), "reason": reason,
        })

    # Executed more than once during the run.
    counts = defaultdict(int)
    for e in observed:
        counts[e["symbol"]] += 1
    executed_twice = [{"symbol": s, "count": c}
                      for s, c in sorted(counts.items()) if c > 1]

    # inithelper_register events (registration phase).
    reg_events = [e for e in enters
                  if e.get("symbol") == "inithelper_register"]

    return {
        "initGroupEvents": [
            {"seq": e.get("seq"),
             "group": (e.get("arguments") or {}).get("group"),
             "groupName": INIT_GROUP_NAMES.get(
                 (e.get("arguments") or {}).get("group")),
             "monotonicNs": e.get("monotonicNs")}
            for e in init_group_events],
        "order": order,
        "registeredButNotExecuted": not_executed,
        "executedMoreThanOnce": executed_twice,
        "initHelperRegistrationEvents": len(reg_events),
        "inventoryInitHelperCount": inv.get("initme_use_count"),
        "distinctInitCallbacksObserved": len(observed_syms),
        "summary": {
            "initCallbacksObserved": len(observed),
            "distinctInitCallbacksObserved": len(observed_syms),
            "initGroupDispatches": len(init_group_events),
            "registeredButNotExecuted": len(not_executed),
            "executedMoreThanOnce": len(executed_twice),
        },
    }


# ===========================================================================
# fini-order.json
# ===========================================================================

def derive_fini_order(events, inv, init_order):
    enters = _enter_events(events)
    fini_fn_set = set(inv["fini_fns"])          # symbols that are fini callbacks
    fini_group_events = [e for e in enters
                         if e.get("symbol") == "fini_group"]
    observed = [e for e in enters if e.get("symbol") in fini_fn_set]

    t0 = observed[0]["monotonicNs"] if observed else None
    prev = None
    order = []
    for e in observed:
        sym = e["symbol"]
        ent = inv["fini_fns"].get(sym, {})
        rec = {
            "seq": e.get("seq"),
            "symbol": sym,
            "group": ent.get("group"),
            "prio": ent.get("prio"),
            "monotonicNs": e.get("monotonicNs"),
            "msFromShutdownStart": (_ms(e["monotonicNs"] - t0)
                                    if t0 is not None else None),
            "msFromPrev": (_ms(e["monotonicNs"] - prev) if prev is not None
                           else 0.0),
            "pairedInitFn": ent.get("pairedWith"),
            "thread": e.get("thread"),
        }
        order.append(rec)
        prev = e["monotonicNs"]

    observed_fini = {e["symbol"] for e in observed}

    # For every init callback observed, classify its fini outcome.
    init_without_fini = []
    for rec in init_order["order"]:
        sym = rec["symbol"]
        declared_fini = rec["finiFn"]
        if declared_fini is None:
            classification = "process-lifetime-ownership (no fini declared)"
        elif declared_fini in observed_fini:
            classification = "fini-observed"
        else:
            classification = "declared-fini-not-observed"
        init_without_fini.append({
            "initFn": sym,
            "group": rec["group"],
            "declaredFini": declared_fini,
            "finiFired": declared_fini in observed_fini,
            "classification": classification,
        })

    # Reverse-of-init check, per group.  movian's init_group/fini_group both
    # iterate the same prio-sorted inithelpers list FORWARD, so fini is NOT
    # expected to be the reverse of init -- this quantifies it dynamically.
    per_group = {}
    init_by_group = defaultdict(list)
    fini_by_group = defaultdict(list)
    for r in init_order["order"]:
        init_by_group[r["group"]].append(r["symbol"])
    for r in order:
        fini_by_group[r["group"]].append(r["symbol"])
    for g in sorted(set(list(init_by_group) + list(fini_by_group)),
                    key=lambda x: (x is None, x)):
        io = init_by_group.get(g, [])
        fo = fini_by_group.get(g, [])
        # Only the subset with a declared fini participates in the LIFO check.
        io_finiable = [s for s in io
                       if inv["init_fns"].get(s, {}).get("finiFn")]
        rev = list(reversed(io_finiable))
        if fo == rev and fo:
            verdict = "fini-runs-in-reverse-of-init (LIFO)"
        elif fo == io_finiable and fo:
            verdict = "fini-runs-in-SAME-order-as-init (FIFO, not LIFO)"
        elif not fo:
            verdict = "no-fini-observed-for-group"
        else:
            verdict = "fini-order-differs-from-both-init-and-reverse"
        per_group[g] = {
            "initOrder": io,
            "finiOrder": fo,
            "initOrderFiniable": io_finiable,
            "reversedInitFiniable": rev,
            "finiEqualsReversedInit": fo == rev,
            "finiEqualsInit": fo == io_finiable,
            "verdict": verdict,
        }

    return {
        "finiGroupEvents": [
            {"seq": e.get("seq"),
             "group": (e.get("arguments") or {}).get("group"),
             "groupName": INIT_GROUP_NAMES.get(
                 (e.get("arguments") or {}).get("group")),
             "monotonicNs": e.get("monotonicNs")}
            for e in fini_group_events],
        "order": order,
        "initToFiniOutcome": init_without_fini,
        "reverseOfInitCheck": {"perGroup": per_group},
        "summary": {
            "finiCallbacksObserved": len(observed),
            "distinctFiniCallbacksObserved": len(observed_fini),
            "finiGroupDispatches": len(fini_group_events),
            "initCallbacksFiniable": sum(
                1 for r in init_order["order"] if r["hasDeclaredFini"]),
            "declaredFiniNotObserved": sum(
                1 for r in init_without_fini
                if r["classification"] == "declared-fini-not-observed"),
            "processLifetimeNoFini": sum(
                1 for r in init_without_fini
                if r["classification"].startswith("process-lifetime")),
        },
    }


# ===========================================================================
# thread-lifecycle.json
# ===========================================================================

def _cstr(s):
    """Clean a captured C-string.  gdb's val.string(length=256) can read past
    the NUL terminator and drag adjacent rodata into the value (e.g. a thread
    title surfaces as "callout\\x00BIG5\\x00..."), so truncate at the first
    NUL and strip."""
    if not isinstance(s, str):
        return s
    nul = s.find("\x00")
    if nul >= 0:
        s = s[:nul]
    return s.strip()
def _thread_name(th):
    if not isinstance(th, dict):
        return None
    return th.get("name") or th.get("osTid") or th.get("gdbId")


def derive_thread_lifecycle(events):
    enters = _enter_events(events)
    creates = [e for e in enters if e.get("category") == "thread-create"]

    # Map thread.name -> span of event seqs, for best-effort activity spans.
    by_name_seqs = defaultdict(list)
    for e in events:
        nm = _thread_name(e.get("thread"))
        if nm is not None:
            by_name_seqs[nm].append(e.get("monotonicNs"))

    create_recs = []
    for e in creates:
        args = e.get("arguments") or {}
        title = _cstr(args.get("title"))
        func = args.get("func") or args.get("start") or args.get("routine")
        # Fall back to the first pointer/string arg if names differ.
        if title is None:
            for v in args.values():
                if isinstance(v, str) and not v.startswith("0x"):
                    title = _cstr(v)
                    break
        create_recs.append({
            "seq": e.get("seq"),
            "symbol": e.get("symbol"),
            "kind": ("detached" if e.get("symbol")
                     == "hts_thread_create_detached" else "joinable"),
            "name": title,
            "startRoutine": func,
            "creator": e.get("thread"),
            "monotonicNs": e.get("monotonicNs"),
            "msFromStart": None,
        })

    t0 = creates[0]["monotonicNs"] if creates else None
    for r in create_recs:
        r["msFromStart"] = _ms(r["monotonicNs"] - t0) if t0 is not None else None

    by_name = defaultdict(lambda: {"creates": 0, "kinds": set()})
    for r in create_recs:
        nm = r["name"] or "<unknown>"
        by_name[nm]["creates"] += 1
        by_name[nm]["kinds"].add(r["kind"])
    by_name_out = OrderedDict()
    for nm in sorted(by_name):
        by_name_out[nm] = {"creates": by_name[nm]["creates"],
                           "kinds": sorted(by_name[nm]["kinds"])}

    spans = []
    for nm, seqs in by_name_seqs.items():
        if len(seqs) < 2:
            continue
        spans.append({
            "name": nm, "eventCount": len(seqs),
            "msSpan": _ms(max(seqs) - min(seqs)),
            "note": ("best-effort activity span inferred from events whose "
                     "thread.name matches; not a measured join"),
        })

    return {
        "createEvents": create_recs,
        "byName": by_name_out,
        "joinProbeAvailable": False,
        "note": ("The inventory arms hts_thread_create_detached/"
                 "hts_thread_create_joinable only; there is no "
                 "hts_thread_join probe, so thread join/duration is not "
                 "directly observable.  Detached threads are never joined by "
                 "design; joinable threads' joins are uninstrumented."),
        "observedActivitySpans": spans,
        "summary": {
            "createEvents": len(create_recs),
            "distinctThreadNames": len(by_name_out),
        },
    }


# ===========================================================================
# lifecycle-graph.mmd
# ===========================================================================

def _node_id(sym):
    return "n_" + "".join(ch if ch.isalnum() else "_" for ch in str(sym))


def _observed_symbols(events):
    return {e.get("symbol") for e in _enter_events(events)}


def derive_graph(events, inv, init_order, fini_order, balance=None):
    observed = _observed_symbols(events)
    lines = []
    lines.append("%% Mermaid lifecycle graph (Buksa/movian #145)")
    lines.append("%% Edge tags:")
    lines.append("%%   confirmed-static  : pairing declared in the inventory")
    lines.append("%%   confirmed-dynamic : declared pairing whose BOTH endpoints")
    lines.append("%%                       were observed as collector events")
    lines.append("%%   inferred          : relationship inferred from event")
    lines.append("%%                       adjacency/containment (never confirmed)")
    lines.append("%%   unknown           : observed, no declared/inferable relation")
    lines.append("graph TD")

    edges = OrderedDict()        # (src, dst) -> tag (strongest wins)
    edge_labels = {}

    def add_edge(src, dst, tag, label=None):
        if src is None or dst is None or src == dst:
            return
        rank = {"unknown": 0, "inferred": 1,
                "confirmed-static": 2, "confirmed-dynamic": 3}
        prev = edges.get((src, dst))
        if prev is None or rank.get(tag, 0) > rank.get(prev, 0):
            edges[(src, dst)] = tag
        if label:
            edge_labels[(src, dst)] = label

    # 1. Init dispatch order (inferred): init_group(G) --> each init callback.
    for rec in init_order["order"]:
        g = rec["group"] or "?"
        src = _node_id("init_group_" + g)
        dst = _node_id(rec["symbol"])
        add_edge(src, dst, "inferred", "init #%d" % (
            [r["symbol"] for r in init_order["order"]].index(rec["symbol"]) + 1))

    # 2. init -> fini pairings (static; dynamic if both observed).
    for sym, ent in inv["init_fns"].items():
        fini = ent.get("finiFn")
        if not fini:
            continue
        if sym in observed:
            tag = ("confirmed-dynamic" if fini in observed
                   else "confirmed-static")
            add_edge(_node_id(sym), _node_id(fini), tag, "init/fini pair")

    # 3. Resource create -> destroy pairings.
    for kind, (csyms, dsyms) in RESOURCE_PAIRS.items():
        for c in csyms:
            for d in dsyms:
                if c in observed:
                    tag = ("confirmed-dynamic" if d in observed
                           else "confirmed-static")
                    add_edge(_node_id(c), _node_id(d), tag, kind)

    # 4. Plugin reload chain (inferred) when a reload window exists.
    if balance and balance.get("reloadWindows"):
        add_edge(_node_id("plugins_reload_dev_plugin"),
                 _node_id("plugin_unload"), "inferred", "reload: unload")
        add_edge(_node_id("plugins_reload_dev_plugin"),
                 _node_id("plugin_load"), "inferred", "reload: reload")
        add_edge(_node_id("plugin_unload"),
                 _node_id("ecmascript_plugin_unload"), "inferred",
                 "unload es")
        add_edge(_node_id("ecmascript_plugin_unload"),
                 _node_id("es_resource_destroy"), "inferred", "teardown")
        add_edge(_node_id("plugin_load"),
                 _node_id("ecmascript_plugin_load"), "inferred", "load es")
        add_edge(_node_id("ecmascript_plugin_load"),
                 _node_id("es_context_create"), "inferred", "new ctx")

    # 5. Unknown: observed symbols that have no edge yet.
    edged_nodes = set()
    for (s, d) in edges:
        edged_nodes.add(s)
        edged_nodes.add(d)
    unknown = []
    for sym in sorted(observed):
        if sym is None:
            continue
        nid = _node_id(sym)
        if nid not in edged_nodes:
            ent = inv["by_symbol"].get(sym, {})
            if ent and ent.get("pairedWith") in observed:
                continue  # will be drawn as part of a pair endpoint
            unknown.append(sym)

    # Emit nodes with readable labels grouped into subgraphs by group.
    label_of = {}
    for sym in observed:
        label_of[_node_id(sym)] = sym
    for sym, ent in inv["init_fns"].items():
        label_of.setdefault(_node_id(sym), sym)
    for g_rec in init_order["initGroupEvents"]:
        gname = g_rec["groupName"]
        if gname is None and g_rec["group"] is not None:
            gname = "group%d" % g_rec["group"]
        if gname is None:
            continue
        label_of[_node_id("init_group_" + gname)] = "init_group[" + gname + "]"

    # Group init callbacks under subgraphs.
    groups_drawn = set()
    for rec in init_order["order"]:
        g = rec["group"] or "?"
        if g in groups_drawn:
            continue
        groups_drawn.add(g)
        lines.append("  subgraph %s" % _node_id("cluster_" + str(g)))
        lines.append("    note[\"%s\"]" % g)
        gid = _node_id("init_group_" + g)
        lines.append("    %s[\"%s\"]" % (gid, label_of.get(gid, g)))
        lines.append("  end")

    # All nodes (ensure every referenced node is declared).
    declared = set()
    def declare(nid):
        if nid in declared:
            return
        declared.add(nid)
        lbl = label_of.get(nid)
        if lbl is None:
            return
        lines.append("  %s[\"%s\"]" % (nid, lbl))

    for (s, d) in edges:
        declare(s)
        declare(d)
    for sym in unknown:
        declare(_node_id(sym))

    # Edges with tag annotations.
    tag_style = {
        "confirmed-dynamic": "==",
        "confirmed-static": "--",
        "inferred": "..",
        "unknown": "~~",
    }
    for (s, d), tag in edges.items():
        lbl = edge_labels.get((s, d), "")
        ann = "[%s]" % tag
        text = ("%s %s" % (lbl, ann)).strip()
        lines.append("  %s -->|%s| %s" % (s, text, d))

    lines.append("")
    lines.append("  %% observed-but-unpaired (unknown relation):")
    for sym in unknown:
        lines.append("  %% %s" % sym)
    if not unknown:
        lines.append("  %% (none)")

    # Re-declare subgraph-cluster nodes that mermaid wants inside subgraphs:
    # (already declared above inside subgraphs; nothing more needed.)
    return "\n".join(lines) + "\n"


# ===========================================================================
# resource-balance.json (plugin reload)
# ===========================================================================

def _pointer_argument(event, keys):
    args = event.get("arguments") or {}
    objs = event.get("objects") or {}
    for key in keys:
        value = objs.get(key, args.get(key))
        if (isinstance(value, str) and value.startswith("0x")
                and value != "0x0"):
            return value
    return None


def _ctx_pointer(event):
    """Return an explicit es_context_t pointer; never guess from any pointer."""
    return _pointer_argument(
        event, ("ec", "context", "es_context", "er->er_ctx"))


def _plugin_id_arg(event):
    """Best-effort plugin id/path from captured plugin arguments."""
    args = event.get("arguments") or {}
    manifest = _cstr(args.get("manifest"))
    if isinstance(manifest, str):
        match = re.search(r'"id"\s*:\s*"([^"]+)"', manifest)
        if match:
            return match.group(1)
    for key in ("id", "path", "url", "plugin", "name", "file"):
        value = _cstr(args.get(key))
        if (isinstance(value, str) and value
                and not value.startswith(("0x", "<"))):
            return value
    return None


def _plugin_aliases(plugin_id_hint):
    aliases = set()

    def add(value):
        value = _cstr(value)
        if not isinstance(value, str) or not value:
            return
        aliases.add(value)
        path = value[7:] if value.startswith("file://") else value
        aliases.add(os.path.normpath(path))
        aliases.add(os.path.basename(path.rstrip("/")))

    add(plugin_id_hint)
    if plugin_id_hint and os.path.isdir(plugin_id_hint):
        manifest_path = os.path.join(plugin_id_hint, "plugin.json")
        try:
            with open(manifest_path) as f:
                add(json.load(f).get("id"))
        except (OSError, ValueError, AttributeError):
            pass
    return aliases


def _plugin_matches(plugin_id, aliases):
    if not aliases:
        return True
    candidates = _plugin_aliases(plugin_id)
    return bool(candidates & aliases)


def _thread_key(event):
    thread = event.get("thread") or {}
    return (thread.get("osTid"), thread.get("gdbId"), thread.get("name"))


def _stack_functions(event):
    return {frame.get("function") for frame in (event.get("stack") or [])
            if isinstance(frame, dict)}


def _context_plugin_map(enters):
    """Associate plugin contexts with ids from ecmascript_plugin_load."""
    pending = {}
    contexts = {}
    for event in enters:
        key = _thread_key(event)
        symbol = event.get("symbol")
        if symbol == "ecmascript_plugin_load":
            pending[key] = _plugin_id_arg(event)
        elif symbol == "es_context_begin" and key in pending:
            ctx = _ctx_pointer(event)
            if ctx:
                contexts[ctx] = pending.pop(key)
    return contexts


def _first_context(events):
    for event in events:
        if event.get("symbol") == "es_context_begin":
            ctx = _ctx_pointer(event)
            if ctx:
                return ctx
    return None


def _reload_cycles(enters, reload_event):
    """Split one reload loop into one synchronous cycle per dev plugin."""
    start_ns = reload_event.get("monotonicNs", 0)
    end_ns = start_ns + 6_000_000_000
    scope = [event for event in enters
             if start_ns <= event.get("monotonicNs", 0) <= end_ns]
    load_indexes = [
        index for index, event in enumerate(scope)
        if event.get("symbol") == "ecmascript_plugin_load"
    ]
    outer_starts = []
    previous_load = -1
    for load_index in load_indexes:
        outer = load_index
        for index in range(load_index, previous_load, -1):
            if scope[index].get("symbol") == "plugin_load":
                outer = index
                break
        outer_starts.append(outer)
        previous_load = load_index

    cycles = []
    for number, load_index in enumerate(load_indexes):
        start_index = outer_starts[number]
        end_index = ((outer_starts[number + 1] - 1)
                     if number + 1 < len(outer_starts)
                     else len(scope) - 1)
        unload_index = None
        for index in range(start_index, load_index):
            if scope[index].get("symbol") == "ecmascript_plugin_unload":
                unload_index = index
        old_events = scope[(unload_index + 1 if unload_index is not None
                            else start_index):load_index]
        new_ctx = _first_context(scope[load_index:end_index + 1])
        if new_ctx:
            for index in range(load_index, end_index + 1):
                event = scope[index]
                if (event.get("symbol") == "es_context_end"
                        and _ctx_pointer(event) == new_ctx
                        and "ecmascript_plugin_load"
                        in _stack_functions(event)):
                    end_index = index
                    break
        cycles.append({
            "pluginId": _plugin_id_arg(scope[load_index]),
            "oldContextPtr": _first_context(old_events),
            "newContextPtr": new_ctx,
            "threadKey": _thread_key(scope[load_index]),
            "startSeq": scope[start_index].get("seq", 0),
            "endSeq": scope[end_index].get("seq", 0),
            "events": scope[start_index:end_index + 1],
        })
    return cycles


def _resource_context_map(enters):
    contexts = {}
    for event in enters:
        if event.get("symbol") != "es_resource_link":
            continue
        resource = _pointer_argument(event, ("er", "resource"))
        ctx = _ctx_pointer(event)
        if resource and ctx:
            contexts[resource] = ctx
    return contexts


def _event_context(event, resource_contexts):
    ctx = _ctx_pointer(event)
    if ctx:
        return ctx
    resource = _pointer_argument(event, ("er", "resource"))
    return resource_contexts.get(resource)


def _belongs_to_cycle(event, contexts, resource_contexts, thread_key):
    ctx = _event_context(event, resource_contexts)
    if ctx is not None:
        return ctx in contexts
    if thread_key != (None, None, None) and _thread_key(event) == thread_key:
        return True
    return bool(
        {"ecmascript_plugin_load", "ecmascript_plugin_unload"}
        & _stack_functions(event)
    )


def _probe_coverage(events, kind, end_seq):
    reasons = []
    installed = next(
        (event for event in events
         if event.get("event") == "collector-installed"), None)
    armed = set(installed.get("armed") or []) if installed else set()
    creates, destroys = RESOURCE_PAIRS[kind]
    if installed is None:
        reasons.append("collector-installation-not-observed")
    else:
        if not (creates & armed):
            reasons.append("create-probe-not-armed")
        if not (destroys & armed):
            reasons.append("destroy-probe-not-armed")
        if kind == "es-context" and "es_context_begin" not in armed:
            reasons.append("context-pointer-probe-not-armed")
    category = RESOURCE_CATEGORIES[kind]
    if any(event.get("event") == "rate-limited"
           and event.get("category") == category
           and event.get("seq", 0) <= end_seq for event in events):
        reasons.append("category-rate-limited")
    if any(event.get("event") == "probe-error"
           and event.get("seq", 0) <= end_seq for event in events):
        reasons.append("collector-probe-error")
    return reasons


def _indeterminate_window(reload_event, reason, plugin_id_hint):
    return {
        "reloadSeq": reload_event.get("seq"),
        "reloadMonotonicNs": reload_event.get("monotonicNs"),
        "windowEvents": 0,
        "pluginId": plugin_id_hint,
        "oldContextPtr": None,
        "newContextPtr": None,
        "correlationKey": {
            "pluginId": plugin_id_hint,
            "oldContextPtr": None,
            "newContextPtr": None,
        },
        "balance": {},
        "status": "indeterminate",
        "balanced": None,
        "imbalances": [],
        "indeterminateReasons": [reason],
    }


def derive_resource_balance(events, plugin_id_hint=None):
    enters = _enter_events(events)
    reloads = [event for event in enters
               if event.get("symbol") == "plugins_reload_dev_plugin"]
    if not reloads:
        return {"reloadWindows": [], "perWindow": [],
                "note": "no plugins_reload_dev_plugin event; not a reload run"}

    sym_kind = {}
    for kind, (create_symbols, destroy_symbols) in RESOURCE_PAIRS.items():
        for symbol in create_symbols:
            sym_kind[symbol] = (kind, "create")
        for symbol in destroy_symbols:
            sym_kind[symbol] = (kind, "destroy")

    aliases = _plugin_aliases(plugin_id_hint)
    context_plugins = _context_plugin_map(enters)
    resource_contexts = _resource_context_map(enters)
    per_window = []
    for reload_event in reloads:
        cycles = _reload_cycles(enters, reload_event)
        if aliases:
            matches = [cycle for cycle in cycles
                       if _plugin_matches(cycle["pluginId"], aliases)]
        else:
            matches = cycles
        if len(matches) != 1:
            reason = ("target-plugin-cycle-not-observed" if not matches
                      else "target-plugin-cycle-ambiguous")
            per_window.append(
                _indeterminate_window(reload_event, reason, plugin_id_hint))
            continue

        cycle = matches[0]
        old_ctx = cycle["oldContextPtr"]
        new_ctx = cycle["newContextPtr"]
        detected_plugin = (
            cycle["pluginId"]
            or context_plugins.get(old_ctx)
            or context_plugins.get(new_ctx)
            or plugin_id_hint
        )
        contexts = {ctx for ctx in (old_ctx, new_ctx) if ctx}
        balance = OrderedDict(
            (kind, {"created": 0, "destroyed": 0,
                    "createCtx": [], "destroyCtx": [], "ids": []})
            for kind in RESOURCE_PAIRS)

        # Context create/end probes are called for many refcounted begin/end
        # operations.  Count the one old/new context transition identified by
        # the plugin unload/load cycle rather than every es_context_end entry.
        if new_ctx:
            balance["es-context"]["created"] = 1
            balance["es-context"]["createCtx"].append(new_ctx)
        if old_ctx:
            balance["es-context"]["destroyed"] = 1
            balance["es-context"]["destroyCtx"].append(old_ctx)

        attributed = []
        seen_resource_operations = set()
        for event in cycle["events"]:
            symbol = event.get("symbol")
            if symbol not in sym_kind:
                continue
            kind, operation = sym_kind[symbol]
            if kind == "es-context":
                continue
            if (kind not in ("plugin", "es-plugin")
                    and not _belongs_to_cycle(
                        event, contexts, resource_contexts,
                        cycle["threadKey"])):
                continue
            if kind == "es-resource":
                resource = _pointer_argument(event, ("er", "resource"))
                operation_key = (operation, resource)
                if resource and operation_key in seen_resource_operations:
                    continue
                if resource:
                    seen_resource_operations.add(operation_key)
            attributed.append(event)
            ctx = _event_context(event, resource_contexts)
            count_key = "created" if operation == "create" else "destroyed"
            balance[kind][count_key] += 1
            if ctx:
                balance[kind][operation + "Ctx"].append(ctx)
            plugin_id = _plugin_id_arg(event)
            if plugin_id:
                balance[kind]["ids"].append(plugin_id)

        imbalances = []
        indeterminate = []
        output_balance = OrderedDict()
        for kind, values in balance.items():
            created = values["created"]
            destroyed = values["destroyed"]
            delta = created - destroyed
            coverage_reasons = _probe_coverage(
                events, kind, cycle["endSeq"])
            if (kind in RELOAD_REQUIRED_KINDS
                    and created == 0 and destroyed == 0):
                coverage_reasons.append("required-kind-not-observed")
            if coverage_reasons:
                kind_status = "indeterminate"
                if kind in RELOAD_REQUIRED_KINDS or created or destroyed:
                    indeterminate.append({
                        "kind": kind, "reasons": coverage_reasons,
                    })
            elif delta:
                kind_status = "imbalanced"
                imbalances.append({
                    "kind": kind, "created": created,
                    "destroyed": destroyed, "delta": delta,
                })
            elif created == 0:
                kind_status = "not-observed"
            else:
                kind_status = "balanced"
            output_balance[kind] = {
                "created": created,
                "destroyed": destroyed,
                "delta": delta,
                "status": kind_status,
                "coverageIssues": coverage_reasons,
            }

        if indeterminate:
            status = "indeterminate"
        elif imbalances:
            status = "imbalanced"
        else:
            status = "balanced"
        per_window.append({
            "reloadSeq": reload_event.get("seq"),
            "reloadMonotonicNs": reload_event.get("monotonicNs"),
            "windowEvents": len(attributed),
            "pluginId": detected_plugin,
            "oldContextPtr": old_ctx,
            "newContextPtr": new_ctx,
            "correlationKey": {
                "pluginId": detected_plugin,
                "oldContextPtr": old_ctx,
                "newContextPtr": new_ctx,
            },
            "balance": output_balance,
            "status": status,
            "balanced": status == "balanced",
            "imbalances": imbalances,
            "indeterminateReasons": indeterminate,
        })

    return {
        "reloadWindows": [
            {"seq": event.get("seq"),
             "monotonicNs": event.get("monotonicNs")}
            for event in reloads
        ],
        "perWindow": per_window,
        "note": (
            "Each reload is split into a synchronous cycle per dev plugin. "
            "Counts include only events attributed by plugin id, old/new "
            "es-context pointer, or the ecmascript load/unload stack. Missing "
            "or rate-limited probes produce an indeterminate result."
        ),
    }


# ===========================================================================
# Driver
# ===========================================================================

def write_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, sort_keys=False)
        f.write("\n")


def analyze(events, inv, run_id, scenario, plugin_id):
    init_order = derive_init_order(events, inv)
    fini_order = derive_fini_order(events, inv, init_order)
    threads = derive_thread_lifecycle(events)
    balance = derive_resource_balance(events, plugin_id_hint=plugin_id)
    graph = derive_graph(events, inv, init_order, fini_order, balance)
    return {
        "init-order.json": init_order,
        "fini-order.json": fini_order,
        "thread-lifecycle.json": threads,
        "lifecycle-graph.mmd": graph,
        "resource-balance.json": balance,
    }


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="lifecycle_analyze.py",
        description="movian lifecycle JSONL post-processor (issue #145)")
    p.add_argument("events", nargs="+", help="collector JSONL event file(s)")
    p.add_argument("--inventory", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "inventory.json"))
    p.add_argument("--out-dir", required=True,
                   help="directory to write derived artifacts")
    p.add_argument("--run-id", default="run")
    p.add_argument("--scenario", default="unnamed")
    p.add_argument("--plugin-id", default=None,
                   help="dev plugin path/id for reload correlation")
    args = p.parse_args(argv)

    events = load_events(args.events)
    if not events:
        sys.stderr.write("no events loaded from %s\n" % args.events)
        return 1
    inv = load_inventory(args.inventory)

    os.makedirs(args.out_dir, exist_ok=True)
    arts = analyze(events, inv, args.run_id, args.scenario, args.plugin_id)

    for fname, obj in arts.items():
        path = os.path.join(args.out_dir, fname)
        if fname.endswith(".json"):
            payload = dict(obj)
            payload["runId"] = args.run_id
            payload["scenario"] = args.scenario
            payload["eventsSource"] = [os.path.basename(e)
                                       for e in args.events]
            write_json(path, payload)
        else:
            with open(path, "w") as f:
                f.write(obj)

    summary = {
        "runId": args.run_id,
        "scenario": args.scenario,
        "eventCount": len(events),
        "init": arts["init-order.json"]["summary"],
        "fini": arts["fini-order.json"]["summary"],
        "threads": arts["thread-lifecycle.json"]["summary"],
        "reloadWindows": len(arts["resource-balance.json"]["reloadWindows"]),
    }
    write_json(os.path.join(args.out_dir, "summary.json"), summary)

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

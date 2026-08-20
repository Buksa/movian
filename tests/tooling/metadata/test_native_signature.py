#!/usr/bin/env python3
"""Behavior tests for `gen.py`'s native signature derivation (movian#207).

Why a unit test and not a fixture. Most of what this rule does IS visible in
the artifact -- `native/prop.destroy(p?: PropHandle): void` is right there --
and the declaration fixtures pin that half. What they cannot see is everything
the rule declines to say. A rule that over-reads the C emits a confident wrong
type; a rule that under-reads emits `any`, which is what was there before and
looks like nothing happening. Both are invisible to a diff of the artifact
unless someone already knows what the C says.

So this file pins the refusals, and the fixtures pin the answers. Neither is a
gate on its own: a derivation that answers `any` to everything passes every
refusal here, and one that answers `string` to everything passes a fixture
that only ever checks strings.

The sharpest case is REFUSED_NON_INDEX. `es_get_native_obj(ctx, 0, &cls)`,
`es_escape(ctx, 1)` and `set_timer(duk, 1)` are the same shape to a regex --
a name, a context, a small integer -- and only two of the three are talking
about an argument index. `es_escape`'s int is a URL-escaping mode and
`set_timer`'s is a repeat multiplier. Reading either as an index invents a
parameter type out of a flag. What separates them is proof: the helper's own
body has to be seen applying a Duktape reader to that parameter *by name*.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
GEN_PY = REPO_ROOT / "support" / "devtools" / "metadata" / "gen.py"

_spec = importlib.util.spec_from_file_location("movian_metadata_gen", GEN_PY)
assert _spec is not None and _spec.loader is not None
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


def c_function(name: str, params: list[str], body: str) -> dict[str, object]:
    """A `scan_c_functions()` record, built by hand."""
    return {
        "file": "src/ecmascript/es_test.c",
        "line": 1,
        "ctx": gen._c_param_name(params[0]),
        "params": [gen._c_param_name(part) for part in params],
        "body": "{\n%s\n}" % body,
    }


def facts(corpus: dict[str, dict[str, object]], name: str) -> dict[int, object]:
    memo: dict[tuple[str, int], dict[str, object]] = {}
    return gen._function_facts(name, corpus, memo,
                               gen.NATIVE_HELPER_DEPTH)["indices"]


class DirectReaders(unittest.TestCase):
    def test_require_and_coerce_are_both_typed_and_kept_apart(self) -> None:
        corpus = {"f": c_function("f", ["duk_context *ctx"], """
  const char *a = duk_require_string(ctx, 0);
  double b = duk_to_number(ctx, 1);
  int c = duk_get_boolean(ctx, 2);
""")}
        self.assertEqual(facts(corpus, "f"), {
            0: {"type": "string", "reader": "require"},
            1: {"type": "number", "reader": "coerce"},
            2: {"type": "boolean", "reader": "get"},
        })

    def test_a_test_call_suppresses_the_type_it_sits_beside(self) -> None:
        """`duk_is_X` is the body admitting it accepts more than one shape."""
        corpus = {"f": c_function("f", ["duk_context *ctx"], """
  if(duk_is_string(ctx, 0))
    return handle_string(duk_to_string(ctx, 0));
  return handle_other(ctx);
""")}
        record = corpus["f"]
        derived = gen.native_parameters(
            record, gen._function_facts("f", corpus, {},
                                        gen.NATIVE_HELPER_DEPTH), 1)
        self.assertNotIn("type", derived[0])

    def test_two_readers_disagreeing_poison_the_slot(self) -> None:
        corpus = {"f": c_function("f", ["duk_context *ctx"], """
  const char *a = duk_to_string(ctx, 0);
  double n = duk_to_number(ctx, 0);
""")}
        self.assertIsNone(facts(corpus, "f")[0])

    def test_a_negative_index_is_not_an_argument(self) -> None:
        """`-1` is the top of the stack, not argument minus one."""
        corpus = {"f": c_function("f", ["duk_context *ctx"], """
  const char *a = duk_to_string(ctx, -1);
""")}
        self.assertEqual(facts(corpus, "f"), {})


class ParameterNames(unittest.TestCase):
    """Names are documentation, so a wrong one is never a compile error."""

    def test_a_local_names_the_argument_it_reads(self) -> None:
        record = c_function("f", ["duk_context *ctx"],
                            "  const char *path = duk_to_string(ctx, 0);")
        derived = gen.native_parameters(
            record, gen._function_facts("f", {"f": record}, {},
                                        gen.NATIVE_HELPER_DEPTH), 1)
        self.assertEqual(derived[0].get("name"), "path")

    def test_a_struct_member_is_not_a_parameter_name(self) -> None:
        """es_io.c's `ehr->ehr_headreq = es_prop_is_true(ctx, 1, ...)`.

        The target of that assignment ends in an identifier like any local
        does, and taking it advertised `native/io.httpReq(url, ehr_headreq)`
        -- the callee's own bookkeeping field, offered to a plugin author as
        the name of their options object.
        """
        record = c_function("f", ["duk_context *ctx"], """
  ehr->ehr_headreq = es_prop_is_true(ctx, 1, "headRequest");
""")
        derived = gen.native_parameters(
            record, gen._function_facts("f", {"f": record}, {},
                                        gen.NATIVE_HELPER_DEPTH), 2)
        self.assertNotIn("name", derived[1])

    def test_the_real_tree_keeps_that_name_out(self) -> None:
        modules = {module["name"]: module
                   for module in gen.build_native_modules()}
        params = next(function for function
                      in modules["native/io"]["functions"]
                      if function["name"] == "httpReq")["params"]
        self.assertNotIn("name", params[1], params)

    def test_a_typescript_keyword_is_refused(self) -> None:
        record = c_function("f", ["duk_context *ctx"],
                            "  const char *new = duk_to_string(ctx, 0);")
        derived = gen.native_parameters(
            record, gen._function_facts("f", {"f": record}, {},
                                        gen.NATIVE_HELPER_DEPTH), 1)
        self.assertNotIn("name", derived[0])

    def test_one_name_is_not_reused_across_two_slots(self) -> None:
        """Duplicate parameter names do not compile."""
        record = c_function("f", ["duk_context *ctx"], """
  str = duk_to_string(ctx, 0);
  str = duk_to_string(ctx, 1);
""")
        derived = gen.native_parameters(
            record, gen._function_facts("f", {"f": record}, {},
                                        gen.NATIVE_HELPER_DEPTH), 2)
        self.assertEqual(derived[0].get("name"), "str")
        self.assertNotIn("name", derived[1])


class HandleClasses(unittest.TestCase):
    def test_the_class_at_the_call_site_becomes_the_type(self) -> None:
        corpus = {"f": c_function("f", ["duk_context *ctx"], """
  prop_t *p = es_get_native_obj(ctx, 0, &es_native_prop);
  es_sqlite_t *s = es_resource_get(ctx, 1, &es_resource_sqlite);
""")}
        self.assertEqual(facts(corpus, "f"), {
            0: {"handle": "PropHandle", "nativeClass": "es_native_prop"},
            1: {"handle": "SqliteHandle", "nativeClass": "es_resource_sqlite"},
        })

    def test_two_classes_at_one_slot_poison_it(self) -> None:
        corpus = {"f": c_function("f", ["duk_context *ctx"], """
  void *a = es_get_native_obj_nothrow(ctx, 0, &es_native_prop);
  void *b = es_get_native_obj_nothrow(ctx, 0, &es_native_htsmsg);
""")}
        self.assertIsNone(facts(corpus, "f")[0])


class HelperForwarding(unittest.TestCase):
    def test_a_forwarded_index_carries_its_type_back(self) -> None:
        """es_file_basename -> get_filename -> duk_to_string, in miniature."""
        corpus = {
            "get_filename": c_function(
                "get_filename",
                ["duk_context *ctx", "int index", "int for_write"], """
  const char *filename = duk_to_string(ctx, index);
  return filename;
"""),
            "f": c_function("f", ["duk_context *ctx"], """
  const char *name = get_filename(ctx, 0, 0);
"""),
        }
        self.assertEqual(facts(corpus, "f"),
                         {0: {"type": "string", "reader": "coerce"}})

    def test_forwarding_chains_two_deep(self) -> None:
        """es_prop_setValue -> es_stprop_get -> es_get_native_obj."""
        corpus = {
            "es_get_native_obj": c_function(
                "es_get_native_obj",
                ["duk_context *ctx", "int obj_idx", "void *c"], "  return 0;"),
            "es_stprop_get": c_function(
                "es_stprop_get", ["duk_context *ctx", "int val_index"], """
  return es_get_native_obj(ctx, val_index, &es_native_prop);
"""),
            "f": c_function("f", ["duk_context *ctx"], """
  prop_t *p = es_stprop_get(ctx, 0);
"""),
        }
        self.assertEqual(
            facts(corpus, "f"),
            {0: {"handle": "PropHandle", "nativeClass": "es_native_prop"}})

    def test_a_helper_reading_a_literal_index_reaches_its_caller(self) -> None:
        """Positive stack indices are absolute in the frame, so they carry."""
        corpus = {
            "set_timer": c_function(
                "set_timer", ["duk_context *duk", "int repeat"], """
  int val = duk_require_int(duk, 1);
"""),
            "f": c_function("f", ["duk_context *ctx"], "  return set_timer(ctx, 1);"),
        }
        self.assertEqual(facts(corpus, "f"),
                         {1: {"type": "number", "reader": "require"}})

    def test_recursion_terminates(self) -> None:
        corpus = {
            "a": c_function("a", ["duk_context *ctx"], "  return b(ctx);"),
            "b": c_function("b", ["duk_context *ctx"], "  return a(ctx);"),
        }
        self.assertEqual(facts(corpus, "a"), {})


class RefusedNonIndex(unittest.TestCase):
    """An `int` parameter is not an argument index until the body proves it."""

    def test_a_mode_flag_is_not_an_index(self) -> None:
        """es_string.c's `es_escape(ctx, how)` -- `how` is a URL-escape mode."""
        corpus = {
            "es_escape": c_function(
                "es_escape", ["duk_context *ctx", "int how"], """
  const char *str = duk_safe_to_string(ctx, 0);
  size_t len = url_escape(NULL, 0, str, how);
  duk_push_lstring(ctx, r, len - 1);
  return 1;
"""),
            "f": c_function("f", ["duk_context *ctx"], "  return es_escape(ctx, 1);"),
        }
        # Argument 0 is a string because es_escape reads slot 0 outright. The
        # `1` handed to `how` must contribute nothing at all: if it were read
        # as an index, argument 1 of a one-argument native would acquire a
        # type out of a flag value.
        self.assertEqual(facts(corpus, "f"),
                         {0: {"type": "string", "reader": "coerce"}})

    def test_the_real_tree_still_refuses_both_known_flags(self) -> None:
        """The corpus assertion behind the synthetic ones above."""
        functions = gen.scan_c_functions()
        for name, flag in (("es_escape", "how"), ("set_timer", "repeat")):
            with self.subTest(name):
                record = functions.get(name)
                self.assertIsNotNone(record, "%s vanished from src/ecmascript"
                                     % name)
                self.assertIn(flag, record["params"],
                              "%s no longer takes %s" % (name, flag))
                position = record["params"].index(flag)
                derived = gen._function_facts(name, functions, {},
                                              gen.NATIVE_HELPER_DEPTH)
                self.assertNotIn(position, derived["params"],
                                 "%s(%s) is being read as an argument index"
                                 % (name, flag))


class ReturnTypes(unittest.TestCase):
    def test_return_zero_and_no_push_is_void(self) -> None:
        record = c_function("f", ["duk_context *ctx"], "  return 0;")
        self.assertEqual(gen.native_return_type(record, {}), ("void", None))

    def test_a_single_push_kind_under_return_one(self) -> None:
        record = c_function("f", ["duk_context *ctx"], """
  duk_push_string(ctx, tmp);
  return 1;
""")
        self.assertEqual(gen.native_return_type(record, {}),
                         ("string", None))

    def test_mixed_arities_refuse(self) -> None:
        record = c_function("f", ["duk_context *ctx"], """
  if(bad)
    return 0;
  duk_push_string(ctx, tmp);
  return 1;
""")
        self.assertIsNone(gen.native_return_type(record, {}))

    def test_two_push_kinds_refuse(self) -> None:
        record = c_function("f", ["duk_context *ctx"], """
  if(bad)
    duk_push_null(ctx);
  else
    duk_push_string(ctx, tmp);
  return 1;
""")
        self.assertIsNone(gen.native_return_type(record, {}))

    def test_a_pushed_object_is_not_a_named_type(self) -> None:
        """`object` is a shape this scan cannot describe, so it says nothing."""
        record = c_function("f", ["duk_context *ctx"], """
  duk_push_object(ctx);
  return 1;
""")
        self.assertIsNone(gen.native_return_type(record, {}))

    def test_a_push_through_a_helper_is_named_when_the_helper_is_known(
            self) -> None:
        """`es_stprop_push` is one hop from a class named at a call site.

        Refusing it would leave every handle-returning native at `any` while
        the same class is accepted on the reading side, and `native/prop` has
        no other way to hand one out.
        """
        record = c_function("f", ["duk_context *ctx"], """
  es_stprop_push(ctx, p);
  return 1;
""")
        helper = c_function("es_stprop_push", ["duk_context *ctx",
                                               "prop_t *p"], """
  es_push_native_obj(ctx, &es_native_prop, p);
""")
        self.assertIsNone(gen.native_return_type(record, {}))
        self.assertEqual(
            gen.native_return_type(record, {"es_stprop_push": helper}),
            ("PropHandle", "es_native_prop"))

    def test_a_resource_push_resolves_the_class_it_created(self) -> None:
        """Every resource is pushed as one class; the specific one is created.

        `es_resource_push` goes through `es_push_native_obj(ctx,
        &es_native_resource, er)`, so reading it literally would declare
        `fs.open()` and `sqlite.create()` the same type -- and then reject
        `fs.read(fs.open(...))`, which the runtime accepts.
        """
        record = c_function("f", ["duk_context *ctx"], """
  es_fd_t *efd = es_resource_create(ec, &es_resource_fd, 0);
  es_resource_push(ctx, &efd->super);
  return 1;
""")
        self.assertEqual(gen.native_return_type(record, {}),
                         ("FdHandle", "es_resource_fd"))

    def test_two_resource_classes_in_one_body_refuse(self) -> None:
        record = c_function("f", ["duk_context *ctx"], """
  es_resource_create(ec, &es_resource_fd, 0);
  es_resource_create(ec, &es_resource_sqlite, 0);
  es_resource_push(ctx, er);
  return 1;
""")
        self.assertIsNone(gen.native_return_type(record, {}))

    def test_a_filled_container_returns_the_container(self) -> None:
        """The idiom half this surface is written in.

        Reading the nearest push gives the ELEMENT, which would declare
        `readdir()` as `string` when it returns `string[]`.
        """
        record = c_function("f", ["duk_context *ctx"], """
  duk_push_array(ctx);
  RB_FOREACH(fde, &fd->fd_entries, fde_link) {
    duk_push_string(ctx, name);
    duk_put_prop_index(ctx, -2, idx++);
  }
  return 1;
""")
        self.assertEqual(gen.native_return_type(record, {}),
                         ("string[]", None))

    def test_a_container_whose_element_is_not_stored_refuses(self) -> None:
        """Without the `duk_put_prop_*` the second push is the result, not an
        element, and nothing here can tell which."""
        record = c_function("f", ["duk_context *ctx"], """
  duk_push_array(ctx);
  duk_push_string(ctx, name);
  return 1;
""")
        self.assertIsNone(gen.native_return_type(record, {}))

    def test_the_real_tree_still_typed_readdir_and_open(self) -> None:
        """The corpus assertion behind the synthetic ones above."""
        functions = gen.scan_c_functions()
        self.assertEqual(
            gen.native_return_type(functions["es_file_readdir"], functions),
            ("string[]", None))
        self.assertEqual(
            gen.native_return_type(functions["es_file_open"], functions),
            ("FdHandle", "es_resource_fd"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

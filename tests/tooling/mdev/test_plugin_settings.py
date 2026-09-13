#!/usr/bin/env python3
"""Seeding a plugin's own setting before launch (movian#247).

A plugin that gates its content behind a setting cannot be driven from a
fresh isolated instance, because nothing can write that setting before the
process starts. `--dev-flags` writes `<persistent>/settings/dev`, which is
the CORE's namespace and not a plugin's.

The path a plugin setting actually lands at, traced rather than guessed:

    plugins.c:240        fqid = "<manifest id>@<origin>", origin "dev" for
                         a `-p` plugin (plugins.c:1435, 1465)
    ecmascript.c:881    Core.storagePath = <persistent>/plugins/<fqid>
    settings.js:276,297 globalSettings -> <storagePath>/settings/<group>
    store.js:20-21       written as JSON.stringify(keys) -- a flat object

So: `<persistent>/plugins/<id>@dev/settings/<group>`, and the file really
looks like this, read off the test stand:

    $ cat ~/.hts/showtime/plugins/tmdb/settings/tmdb
    {"similar_movies":1,"lists":1,"collection":1,"trailers":1}

Integers for what `createBool` declared. That is not decoration: `getvalue`
returns the stored value RAW, with no coercion at all
(settings.js:298-300), so what the seed writes is exactly what the plugin
sees, and a plugin comparing `=== 1` behaves differently from one seeing
`true`. The seed therefore writes what Movian writes.

The plugin id here is the MANIFEST id -- `HDRezka`, not `HDRezka@dev`. The
suffix is an implementation detail of how the core names a dev load, and
making a person type it is a trap rather than a contract; mdev knows the
`-p` directories, so it reads their manifests and appends the suffix
itself. An id that matches none of them is refused with the ones that do.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from support.devtools.mdevlib import cli  # noqa: E402
from support.devtools.mdevlib import harness  # noqa: E402
from support.devtools.mdevlib.harness import MdevError  # noqa: E402


def plugin_dir(root: Path, plugin_id: str) -> str:
    """A `-p` directory with a manifest, the way a dev plugin ships."""
    path = root / plugin_id
    path.mkdir(parents=True, exist_ok=True)
    (path / "plugin.json").write_text(json.dumps({
        "type": "ecmascript", "id": plugin_id, "version": "1.0.0",
        "file": "main.js", "apiversion": 2,
    }), encoding="utf-8")
    return str(path)


class ParsingTheSpec(unittest.TestCase):
    def test_the_three_parts_and_the_value(self) -> None:
        self.assertEqual(
            harness.parse_plugin_setting("HDRezka:hdrezka:tosEnabled=false"),
            ("HDRezka", "hdrezka", "tosEnabled", 0))

    def test_a_bool_is_written_as_an_int(self) -> None:
        """What Movian writes, measured off the stand -- `createBool`
        settings appear in the store as 1 and 0. `getvalue` hands the raw
        value to the plugin, so writing `true` would hand it something
        Movian never would."""
        for spec, expected in (("p:g:k=true", 1), ("p:g:k=false", 0)):
            with self.subTest(spec):
                self.assertEqual(harness.parse_plugin_setting(spec).value,
                                 expected)

    def test_an_integer_stays_an_integer(self) -> None:
        self.assertEqual(harness.parse_plugin_setting("p:g:k=42").value, 42)
        self.assertEqual(harness.parse_plugin_setting("p:g:k=-1").value, -1)

    def test_anything_else_is_a_string(self) -> None:
        self.assertEqual(
            harness.parse_plugin_setting("p:g:k=hd.example").value,
            "hd.example")

    def test_quoting_forces_the_literal_string(self) -> None:
        """The escape hatch, and the reason it has to exist.

        `true` and `2160` are guesses about the DECLARED type, which mdev
        cannot see -- `createString` and `createInt` write the same file, so
        a string setting whose value happens to be digits would be handed an
        int the application never wrote. Quoting is the only way to seed the
        string `2160`, or the string `true`.
        """
        for spec, expected in (('p:g:k="2160"', "2160"),
                               ('p:g:k="true"', "true"),
                               ('p:g:k=""', "")):
            with self.subTest(spec):
                self.assertEqual(
                    harness.parse_plugin_setting(spec).value, expected)

    def test_a_value_may_contain_the_separators(self) -> None:
        """A domain or a cookie is a perfectly ordinary setting value, and
        both carry `:` and `=`. Only the first two `:` and the first `=`
        after them are structure."""
        self.assertEqual(
            harness.parse_plugin_setting("p:g:domain=https://x.example/a=b"),
            ("p", "g", "domain", "https://x.example/a=b"))

    def test_a_malformed_spec_does_not_echo_its_value(self) -> None:
        """The three parse refusals had no key to name, so they echoed the
        whole spec -- value included. Mistyping is the likeliest way to
        reach them, and it is the same trigger as the unknown-plugin case
        that was redacted first, so these leaked for exactly the reason that
        one did.

        Each case below is a DIFFERENT refusal: one colon short, empty
        group, empty key.
        """
        secret = "session=secret"
        for spec in ("P:cookie=" + secret,
                     "P::k=" + secret,
                     "P:g:=" + secret):
            with self.subTest(spec=spec.split("=")[0]):
                with self.assertRaises(MdevError) as caught:
                    harness.parse_plugin_setting(spec)
                message = str(caught.exception)
                self.assertNotIn(secret, message)
                self.assertNotIn("secret", message)
                self.assertIn("redacted", message)

    def test_a_spec_with_no_value_is_shown_whole(self) -> None:
        """Nothing to hide, and the reader needs all of it: this refusal
        fires precisely because there is no `=`, so redacting would remove
        the only thing it can report."""
        with self.assertRaises(MdevError) as caught:
            harness.parse_plugin_setting("P:g:justakey")
        self.assertIn("P:g:justakey", str(caught.exception))
        self.assertNotIn("redacted", str(caught.exception))

    def test_a_malformed_spec_is_refused(self) -> None:
        for spec in ("nocolons", "p:g", "p:g:k", "p:g:=v", "p::k=v",
                     ":g:k=v"):
            with self.subTest(spec):
                with self.assertRaises(MdevError):
                    harness.parse_plugin_setting(spec)


class WhereItLands(unittest.TestCase):
    def test_the_path_matches_the_core(self) -> None:
        """`<persistent>/plugins/<id>@dev/settings/<group>` -- fqid from
        plugins.c:240, the `plugins/` segment from ecmascript.c:881."""
        self.assertEqual(
            harness.plugin_setting_path(Path("/p"), "HDRezka", "hdrezka"),
            Path("/p/plugins/HDRezka@dev/settings/hdrezka"))


class ItStaysInsideTheProfile(unittest.TestCase):
    """A settings group names a file in the plugin's own profile.

    `Path("a") / "/tmp/x"` is `/tmp/x` -- pathlib discards everything before
    an absolute part -- so an absolute group walked straight out and MERGED
    into whatever JSON file it landed on. Reproduced before the fix: a file
    holding `{"important": true}` came back `{"important": true, "pwned":
    1}`. `..` walked out the other way. The core concatenates strings
    (settings.js:297) and cannot escape at all, so this was mdev's hazard
    alone.
    """

    def test_an_escaping_group_is_refused(self) -> None:
        for group in ("/tmp/victim", "..", ".", "a/b", "a\\b", ""):
            with self.subTest(group):
                with self.assertRaises(MdevError):
                    harness.plugin_setting_path(Path("/p"), "x", group)

    def test_an_escaping_plugin_id_is_refused(self) -> None:
        """The id comes from a manifest, which is a file this harness did
        not write."""
        with self.assertRaises(MdevError):
            harness.plugin_setting_path(Path("/p"), "../../x", "g")

    def test_the_spec_parser_refuses_it_too(self) -> None:
        """Early, so the message names the spec rather than a path built
        from it."""
        with self.assertRaises(MdevError):
            harness.parse_plugin_setting("p:/tmp/victim:k=1")

    def test_nothing_is_written_outside(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        victim = root / "victim.json"
        victim.write_text('{"important": true}', encoding="utf-8")
        with self.assertRaises(MdevError):
            harness.seed_plugin_settings(
                root / "persistent",
                [plugin_dir(root / "src", "P")],
                ["P:%s:pwned=1" % victim])
        self.assertEqual(json.loads(victim.read_text()),
                         {"important": True})


class NothingIsWrittenUntilEverythingCanBe(unittest.TestCase):
    """A refused seed must leave no seed.

    Writing as the loop went meant a later malformed target left the earlier
    files already written while the command reported failure and launched
    nothing -- a profile carrying settings from an operation that said it
    had not happened, which a later run would use without knowing.
    """

    def test_an_earlier_file_is_not_written_when_a_later_one_refuses(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        persistent = root / "persistent"
        plugins = [plugin_dir(root / "src", "P")]
        bad = harness.plugin_setting_path(persistent, "P", "bad")
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("[1,2,3]", encoding="utf-8")
        with self.assertRaises(MdevError):
            harness.seed_plugin_settings(
                persistent, plugins, ["P:good:k=1", "P:bad:k=2"])
        self.assertFalse(
            harness.plugin_setting_path(persistent, "P", "good").exists(),
            "an earlier target was seeded by a request that failed")
        self.assertEqual(bad.read_text(), "[1,2,3]")


class WhatMovianCanActuallyRead(unittest.TestCase):
    """Python's JSON is more permissive than Movian's, and the gap is silent.

    `json.loads` accepts `NaN`/`Infinity` and `json.dumps` writes them back.
    `JSON.parse` rejects them, and `store.js:48-51` swallows that failure
    whole -- `try { ... } catch (e) {}` -- leaving the plugin an EMPTY
    store. The seed would report success and the prompt it was meant to
    bypass would appear anyway.
    """

    def test_a_store_movian_cannot_parse_is_refused(self) -> None:
        for token in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(token):
                tmp = tempfile.TemporaryDirectory()
                self.addCleanup(tmp.cleanup)
                root = Path(tmp.name)
                path = harness.plugin_setting_path(
                    root / "persistent", "P", "g")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{"old": %s}' % token, encoding="utf-8")
                with self.assertRaises(MdevError) as caught:
                    harness.seed_plugin_settings(
                        root / "persistent",
                        [plugin_dir(root / "src", "P")], ["P:g:new=1"])
                self.assertIn(token, str(caught.exception))
                self.assertIn("old", path.read_text())

    def test_an_exponent_overflow_is_refused_in_the_plan(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        path = harness.plugin_setting_path(
            root / "persistent", "P", "g")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"old": 1e400}', encoding="utf-8")
        with self.assertRaises(MdevError) as caught:
            harness.plan_plugin_settings(
                root / "persistent",
                [plugin_dir(root / "src", "P")], ["P:g:new=1"])
        self.assertIn(str(path), str(caught.exception))
        self.assertEqual(path.read_text(), '{"old": 1e400}')

    def test_serialization_overflow_is_an_mdev_error(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "persistent" / "g"
        path.parent.mkdir(parents=True)
        with self.assertRaises(MdevError) as caught:
            harness._stage(path, {"old": float("inf")})
        self.assertIn(str(path), str(caught.exception))
        self.assertFalse(path.exists())


class TheDestinationIsCheckedBeforeAnythingIsWritten(unittest.TestCase):
    def test_a_profile_that_is_a_file_stops_the_whole_request(self) -> None:
        """Planning validated content but not destinations, so a profile
        directory that is actually a regular file only blew up at mkdir --
        inside the write loop, after an earlier plugin was committed."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        persistent = root / "persistent"
        plugins = [plugin_dir(root / "src", "P"),
                   plugin_dir(root / "src", "Q")]
        blocked = persistent / "plugins" / "Q@dev"
        blocked.parent.mkdir(parents=True, exist_ok=True)
        blocked.write_text("not a directory", encoding="utf-8")
        with self.assertRaises(MdevError):
            harness.seed_plugin_settings(
                persistent, plugins, ["P:g:k=1", "Q:g:k=2"])
        self.assertFalse(
            harness.plugin_setting_path(persistent, "P", "g").exists(),
            "P was seeded by a request that could never have completed")


class TheDestinationMustBeWhatItClaims(unittest.TestCase):
    """The path-component guard promises a seed stays in the profile.

    A symlink breaks that promise from the other side: `is_file()` and the
    write both follow one, so a group symlinked at an unrelated JSON file
    merged into it. Measured: `{"mine": true}` came back `{"mine": true,
    "pwned": 1}`.

    A leaf that is a DIRECTORY is the other half. `is_file()` reads it as
    "no store yet" and the parent preflight passes, so the commit loop
    raised an uncaught IsADirectoryError -- after earlier targets were
    written, which is the partial seed the two-phase design prevents.
    """

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.plugins = [plugin_dir(self.root / "src", "P")]

    def test_a_symlinked_group_is_refused(self) -> None:
        persistent = self.root / "persistent"
        leaf = harness.plugin_setting_path(persistent, "P", "g")
        leaf.parent.mkdir(parents=True, exist_ok=True)
        outside = self.root / "outside.json"
        outside.write_text('{"mine": true}', encoding="utf-8")
        os.symlink(outside, leaf)
        with self.assertRaises(MdevError) as caught:
            harness.seed_plugin_settings(
                persistent, self.plugins, ["P:g:pwned=1"])
        self.assertIn("symlink", str(caught.exception))
        self.assertEqual(json.loads(outside.read_text()), {"mine": True})

    def test_a_directory_leaf_stops_the_whole_request(self) -> None:
        persistent = self.root / "persistent"
        harness.plugin_setting_path(persistent, "P", "bad").mkdir(parents=True)
        with self.assertRaises(MdevError):
            harness.seed_plugin_settings(
                persistent, self.plugins, ["P:good:k=1", "P:bad:k=2"])
        self.assertFalse(
            harness.plugin_setting_path(persistent, "P", "good").exists())

    def test_an_ordinary_first_seed_still_works(self) -> None:
        """The control: the guard walks up to the profile root and stops.
        An earlier version skipped non-existent candidates with `continue`
        and skipped the stop condition with them, so a brand-new profile --
        the commonest case there is -- was refused as 'outside'."""
        persistent = self.root / "persistent"
        harness.seed_plugin_settings(persistent, self.plugins, ["P:g:k=1"])
        self.assertEqual(
            json.loads(
                harness.plugin_setting_path(persistent, "P", "g").read_text()),
            {"k": 1})


class OnlyAPluginThatCouldReadIt(unittest.TestCase):
    def test_a_views_plugin_is_refused(self) -> None:
        """`plugins.c:674` sends type "views" down a branch that never
        calls `ecmascript_plugin_load`, so no ES context exists, no
        `Core.storagePath` exists, and the seed would be a file nothing
        reads -- reported as success."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        views = root / "views"
        views.mkdir()
        (views / "plugin.json").write_text(
            json.dumps({"id": "V", "type": "views"}), encoding="utf-8")
        with self.assertRaises(MdevError) as caught:
            harness.seed_plugin_settings(
                root / "persistent", [str(views)], ["V:g:k=1"])
        self.assertIn("views", str(caught.exception))
        self.assertIn("never read", str(caught.exception))


class Seeding(unittest.TestCase):
    def seed(self, specs, ids=("HDRezka",)):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        persistent = root / "persistent"
        plugins = [plugin_dir(root / "src", i) for i in ids]
        written = harness.seed_plugin_settings(persistent, plugins, specs)
        return persistent, written

    def test_it_writes_what_movian_would_read(self) -> None:
        persistent, written = self.seed(["HDRezka:hdrezka:tosEnabled=false"])
        path = persistent / "plugins/HDRezka@dev/settings/hdrezka"
        self.assertEqual(written, [path])
        self.assertEqual(json.loads(path.read_text()), {"tosEnabled": 0})

    def test_several_keys_share_one_file(self) -> None:
        """One group is one JSON object; two keys must not each truncate
        the other's file."""
        persistent, _ = self.seed(["HDRezka:hdrezka:tosEnabled=false",
                                   "HDRezka:hdrezka:debug=true"])
        path = persistent / "plugins/HDRezka@dev/settings/hdrezka"
        self.assertEqual(json.loads(path.read_text()),
                         {"tosEnabled": 0, "debug": 1})

    def test_an_existing_file_is_merged_not_replaced(self) -> None:
        """A persistent instance already carries settings a person set by
        hand. Seeding one must not wipe the rest."""
        persistent, _ = self.seed([])
        path = persistent / "plugins/HDRezka@dev/settings/hdrezka"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"domain": "x.example", "debug": 1}))
        harness.seed_plugin_settings(
            persistent, [plugin_dir(path.parents[3] / "src2", "HDRezka")],
            ["HDRezka:hdrezka:tosEnabled=false"])
        self.assertEqual(json.loads(path.read_text()),
                         {"domain": "x.example", "debug": 1, "tosEnabled": 0})

    def test_an_unknown_plugin_id_is_refused_with_the_known_ones(self) -> None:
        """The trap this shape exists to remove: a seed written to an id
        nothing loads is a file nobody reads, and silence would look
        exactly like the setting not working."""
        with self.assertRaises(MdevError) as caught:
            self.seed(["Wrong:g:k=1"], ids=("HDRezka", "tmdb"))
        message = str(caught.exception)
        self.assertIn("Wrong", message)
        self.assertIn("HDRezka", message)
        self.assertIn("tmdb", message)

    def test_unknown_plugin_refusal_redacts_value(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        secret = "session=secret"
        spec = "Wrong:g:cookie=" + secret
        with self.assertRaises(MdevError) as caught:
            harness.resolve_plugin_settings(
                [plugin_dir(root / "src", "P")], [spec])
        message = str(caught.exception)
        self.assertIn("Wrong", message)
        self.assertIn("g", message)
        self.assertIn("cookie", message)
        self.assertNotIn(secret, message)

    def test_plan_unknown_plugin_refusal_redacts_value(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        secret = "session=secret"
        spec = "Wrong:g:cookie=" + secret
        plugins = [plugin_dir(root / "src", "P")]
        with mock.patch.object(harness, "resolve_plugin_settings"):
            with self.assertRaises(MdevError) as caught:
                harness.plan_plugin_settings(
                    root / "persistent", plugins, [spec])
        message = str(caught.exception)
        self.assertIn("Wrong", message)
        self.assertIn("g", message)
        self.assertIn("cookie", message)
        self.assertNotIn(secret, message)

    def test_plugin_type_refusal_redacts_value(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        views = root / "views"
        views.mkdir()
        (views / "plugin.json").write_text(
            json.dumps({"id": "V", "type": "views"}), encoding="utf-8")
        secret = "session=secret"
        spec = "V:g:cookie=" + secret
        with self.assertRaises(MdevError) as caught:
            harness.resolve_plugin_settings([str(views)], [spec])
        message = str(caught.exception)
        self.assertIn("V", message)
        self.assertIn("g", message)
        self.assertIn("cookie", message)
        self.assertNotIn(secret, message)

    def test_plan_plugin_type_refusal_redacts_value(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        views = root / "views"
        views.mkdir()
        (views / "plugin.json").write_text(
            json.dumps({"id": "V", "type": "views"}), encoding="utf-8")
        secret = "session=secret"
        spec = "V:g:cookie=" + secret
        with mock.patch.object(harness, "resolve_plugin_settings"):
            with self.assertRaises(MdevError) as caught:
                harness.plan_plugin_settings(
                    root / "persistent", [str(views)], [spec])
        message = str(caught.exception)
        self.assertIn("V", message)
        self.assertIn("g", message)
        self.assertIn("cookie", message)
        self.assertNotIn(secret, message)

    def test_an_unreadable_manifest_says_so(self) -> None:
        """A `-p` directory whose manifest cannot be parsed used to vanish
        from the known set, and the refusal then pointed at the id the
        caller typed -- "not among the -p plugins: (none given)" -- instead
        of at the manifest. That is a misdirecting diagnosis, which is the
        movian#239 class."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        broken = root / "broken"
        broken.mkdir()
        (broken / "plugin.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(MdevError) as caught:
            harness.seed_plugin_settings(
                root / "persistent", [str(broken)], ["x:g:k=1"])
        self.assertIn("not valid JSON", str(caught.exception))
        self.assertIn("broken", str(caught.exception))

    def test_a_manifest_that_parses_but_is_not_an_object(self) -> None:
        """Parsing is not being a manifest. `[]` gets through json.loads
        and then `.get` raised AttributeError -- a traceback where the
        reader documents an MdevError."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        weird = root / "weird"
        weird.mkdir()
        (weird / "plugin.json").write_text("[]", encoding="utf-8")
        with self.assertRaises(MdevError) as caught:
            harness.seed_plugin_settings(
                root / "persistent", [str(weird)], ["x:g:k=1"])
        self.assertIn("not an object", str(caught.exception))

    def test_a_store_that_parses_but_is_not_an_object_is_refused(self) -> None:
        """The same distinction one layer down, and it was worse here: a
        `[]` fell through to an empty dict and was then written over --
        discarded silently, by the code whose refusal promises not to."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        plugins = [plugin_dir(root / "src", "HDRezka")]
        path = harness.plugin_setting_path(
            root / "persistent", "HDRezka", "hdrezka")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[1,2,3]", encoding="utf-8")
        with self.assertRaises(MdevError) as caught:
            harness.seed_plugin_settings(
                root / "persistent", plugins, ["HDRezka:hdrezka:k=1"])
        self.assertIn("not an object", str(caught.exception))
        self.assertEqual(path.read_text(), "[1,2,3]",
                         "the refusal must not have written anything")

    def test_the_suffix_is_not_typed_by_hand(self) -> None:
        """`HDRezka@dev` is how the core names a dev load, not something a
        caller should have to know. Passing it is refused like any other
        id that is not a manifest id."""
        with self.assertRaises(MdevError):
            self.seed(["HDRezka@dev:g:k=1"])



class TheStagingFileIsNotAnAttackSurface(unittest.TestCase):
    """The temporary the commit moves into place is itself a destination.

    Making the commit safe against the leaf introduced a second path that
    nothing checked, and it was worse than the one it fixed: a predictable
    sibling can be pre-created as a symlink, `write_text` followed it, and
    `os.replace` then installed the LINK as the settings leaf -- so the
    outside file was truncated (`{"mine": true}` became `{"pwned": 1}`, not
    merged) and every later seed would write outside the profile too.
    """

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.persistent = self.root / "persistent"
        self.plugins = [plugin_dir(self.root / "src", "P")]

    def test_the_old_predictable_name_is_no_longer_used(self) -> None:
        """A link at the name the previous version chose is now irrelevant:
        `mkstemp` picks a name nothing can predict, with O_EXCL, so there is
        nothing to pre-create."""
        leaf = harness.plugin_setting_path(self.persistent, "P", "g")
        leaf.parent.mkdir(parents=True, exist_ok=True)
        outside = self.root / "outside.json"
        outside.write_text('{"mine": true}', encoding="utf-8")
        os.symlink(outside, leaf.with_name(
            "%s.mdev-new.%d" % (leaf.name, os.getpid())))
        harness.seed_plugin_settings(
            self.persistent, self.plugins, ["P:g:pwned=1"])
        self.assertEqual(json.loads(outside.read_text()), {"mine": True})
        self.assertFalse(leaf.is_symlink())
        self.assertEqual(json.loads(leaf.read_text()), {"pwned": 1})

    def test_a_staging_name_cannot_be_pre_created(self) -> None:
        """The property behind that, checked directly rather than through
        one guessed name: whatever `_stage` opens, it opens exclusively, so
        a file already there is an error and never a target."""
        leaf = harness.plugin_setting_path(self.persistent, "P", "g")
        leaf.parent.mkdir(parents=True, exist_ok=True)
        opened = []
        real = os.open

        def spy(path, flags, *rest):
            if ".mdev-seed." in str(path):
                opened.append(flags)
            return real(path, flags, *rest)

        with mock.patch.object(os, "open", spy):
            harness.seed_plugin_settings(
                self.persistent, self.plugins, ["P:g:k=1"])
        self.assertTrue(opened, "nothing was staged")
        for flags in opened:
            self.assertTrue(flags & os.O_EXCL, "staged without O_EXCL")
            self.assertTrue(flags & os.O_CREAT)

    def test_a_long_group_is_not_made_illegal_by_the_suffix(self) -> None:
        """A group the filesystem accepts must not fail because the staging
        name is longer than the destination. 250 bytes is legal at the
        destination and was ENAMETOOLONG once suffixed -- a stricter limit
        than Movian's, invented here, and reached after `--force` had
        already stopped the instance."""
        group = "g" * 250
        harness.seed_plugin_settings(
            self.persistent, self.plugins, ["P:%s:k=1" % group])
        self.assertEqual(
            json.loads(harness.plugin_setting_path(
                self.persistent, "P", group).read_text()), {"k": 1})

    def test_an_existing_store_keeps_its_own_mode(self) -> None:
        """Replacing the inode reset a 0600 store to 0644 -- widening
        permissions as a side effect of seeding, on a profile under /tmp
        whose ancestors mdev creates world-traversable, holding whatever a
        plugin keeps in its settings.

        Every mode here differs from the 0600 `mkstemp` creates, in both
        directions. A first version of this test used 0600 itself and passed
        with the preservation deleted -- the staging default happened to
        agree with it, so the test measured nothing.
        """
        for mode in (0o640, 0o444, 0o664):
            with self.subTest("%04o" % mode):
                leaf = harness.plugin_setting_path(
                    self.persistent, "P", "g%04o" % mode)
                leaf.parent.mkdir(parents=True, exist_ok=True)
                leaf.write_text('{"old": 1}', encoding="utf-8")
                os.chmod(leaf, mode)
                harness.seed_plugin_settings(
                    self.persistent, self.plugins,
                    ["P:g%04o:k=1" % mode])
                self.assertEqual(stat.S_IMODE(leaf.stat().st_mode), mode)
                self.assertEqual(json.loads(leaf.read_text()),
                                 {"old": 1, "k": 1})

    def test_a_new_store_is_not_world_readable(self) -> None:
        leaf = harness.plugin_setting_path(self.persistent, "P", "g")
        harness.seed_plugin_settings(
            self.persistent, self.plugins, ["P:g:k=1"])
        self.assertEqual(stat.S_IMODE(leaf.stat().st_mode) & 0o077, 0)


class OnlyWhatAPluginCanReadBack(unittest.TestCase):
    """The seed is read by `JSON.parse` in Duktape, whose Number is a double.

    `DUK_TYPE_NUMBER` is documented as a double in
    `ext/duktape/duktape.h:267`, so an integer past the exactly-representable
    range is written exactly and read as a DIFFERENT integer. Measured:
    9007199254740993 comes back 9007199254740992, and mdev reported a
    successful seed of a value the plugin never sees.
    """

    def test_an_inexact_integer_is_refused(self) -> None:
        for value in ("9007199254740993", "-9007199254740993",
                      "123456789012345678901234567890"):
            with self.subTest(value):
                with self.assertRaises(MdevError) as caught:
                    harness.parse_plugin_setting("P:g:k=%s" % value)
                self.assertIn("MAX_SAFE_INTEGER", str(caught.exception))

    def test_the_boundary_itself_is_accepted(self) -> None:
        """2**53 - 1 is exactly representable, so refusing it would be the
        guard overreaching into values that work."""
        self.assertEqual(
            harness.parse_plugin_setting("P:g:k=9007199254740991").value,
            9007199254740991)

    def test_a_four_hundred_digit_integer_refusal_stays_clean(self) -> None:
        for value in ("9" * 400, "-" + "9" * 400):
            with self.subTest(value=value[:8]):
                with self.assertRaises(MdevError) as caught:
                    harness.parse_plugin_setting("P:g:k=%s" % value)
                expected = "Infinity" if value[0] != "-" else "-Infinity"
                self.assertIn(expected, str(caught.exception))

    def test_a_quoted_one_is_still_a_string(self) -> None:
        """The escape the refusal names has to exist: a plugin holding a big
        number as a string is unaffected, and the message says so."""
        self.assertEqual(
            harness.parse_plugin_setting('P:g:k="9007199254740993"').value,
            "9007199254740993")

    def test_dev_flags_are_not_narrowed_by_it(self) -> None:
        """`--dev-flags` goes to htsmsg and is read by the core in C, so the
        Duktape limit does not apply there and must not leak into it."""
        self.assertEqual(
            harness.parse_dev_flags("big=9007199254740993"),
            {"big": 9007199254740993})


class TheManifestIsReadTheWayTheCoreReadsIt(unittest.TestCase):
    """`json.loads` keeps the last repeated member; the core keeps the first.

    `htsmsg_json_deserialize2` appends every field (htsmsg.c:66) and
    `htsmsg_field_find` walks from the head (htsmsg.c:102-105), so
    `htsmsg_get_str(ctrl, "id")` at plugins.c:633 resolves to the FIRST.
    Neither side rejects the repeat, so the two disagree silently about the
    same file: mdev would seed `Q@dev` and the core would create `P@dev`.

    The source half is asserted rather than trusted, because it is the fact
    that makes refusing correct.
    """

    def test_the_core_takes_the_first_of_a_repeated_member(self) -> None:
        htsmsg = (REPO_ROOT / "src" / "htsmsg" / "htsmsg.c").read_text(
            encoding="utf-8")
        self.assertIn("TAILQ_INSERT_TAIL(&msg->hm_fields, f, hmf_link);",
                      htsmsg)
        find = htsmsg.split("htsmsg_field_find(htsmsg_t *msg", 1)[1]
        body = find.split("\n}", 1)[0]
        self.assertIn("TAILQ_FOREACH(f, &msg->hm_fields, hmf_link)", body)
        self.assertNotIn("TAILQ_FOREACH_REVERSE", body)

    def test_a_repeated_member_is_refused(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        weird = root / "src" / "dup"
        weird.mkdir(parents=True)
        (weird / "plugin.json").write_text(
            '{"id":"P","id":"Q","type":"ecmascript","version":"1.0.0"}',
            encoding="utf-8")
        with self.assertRaises(MdevError) as caught:
            harness.seed_plugin_settings(
                root / "persistent", [str(weird)], ["Q:g:k=1"])
        self.assertIn("more than once", str(caught.exception))
        self.assertFalse((root / "persistent").exists())

    def test_an_uppercase_unicode_escape_in_the_id_is_refused(self) -> None:
        """The core's own decoder is wrong about uppercase hex.
        `src/misc/json.c:71` computes `*s - 'F' + 10`, so A-F yield 5-10
        instead of 10-15: `"P\\u004A"` is `PJ` here and `PE` there, and the
        seed would land in a profile the core never creates. Refused until
        movian#250 is fixed, because mirroring a core bug is worse.
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        weird = root / "src" / "esc"
        weird.mkdir(parents=True)
        (weird / "plugin.json").write_text(
            '{"id":"P\\u004A","type":"ecmascript","version":"1.0.0"}',
            encoding="utf-8")
        with self.assertRaises(MdevError) as caught:
            harness.seed_plugin_settings(
                root / "persistent", [str(weird)], ["PJ:g:k=1"])
        self.assertIn("json.c:71", str(caught.exception))

    def test_a_lowercase_surrogate_escape_in_the_id_is_refused(self) -> None:
        """The core drops surrogate code points in `utf8_put`.
        `src/misc/str.c:687-688` therefore turns this id into `P`, while
        Python resolves the surrogate pair to `P😀`; their fqids differ.
        """
        source = (REPO_ROOT / "src" / "misc" / "str.c").read_text(
            encoding="utf-8")
        self.assertIn(
            "if(c == 0xfffe || c == 0xffff || "
            "(c >= 0xD800 && c < 0xE000))\n"
            "    return 0;",
            source)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        weird = root / "src" / "esc"
        weird.mkdir(parents=True)
        (weird / "plugin.json").write_text(
            '{"id":"P\\ud83d\\ude00","type":"ecmascript",'
            '"version":"1.0.0"}',
            encoding="utf-8")
        with self.assertRaises(MdevError) as caught:
            harness.seed_plugin_settings(
                root / "persistent", [str(weird)], ["P😀:g:k=1"])
        self.assertIn("str.c:687", str(caught.exception))
        self.assertNotIn("json.c:71", str(caught.exception))

    def test_the_core_decoder_is_still_the_one_described(self) -> None:
        """Asserted, not trusted: this refusal exists only because of that
        line, and `htsmsg_json_deserialize2` is what reads plugin.json
        (htsmsg_json.c:228-230 delegates to json_deserialize)."""
        decoder = (REPO_ROOT / "src" / "misc" / "json.c").read_text(
            encoding="utf-8")
        self.assertIn("v |= *s - 'F' + 10;", decoder)
        delegate = (REPO_ROOT / "src" / "htsmsg" / "htsmsg_json.c").read_text(
            encoding="utf-8")
        self.assertIn("json_deserialize(src, &json_to_htsmsg", delegate)

    def test_a_lowercase_escape_is_accepted(self) -> None:
        """Only the affected digits. Lowercase hex decodes correctly in the
        core, so refusing it would be the guard overreaching."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        ok = root / "src" / "esc"
        ok.mkdir(parents=True)
        (ok / "plugin.json").write_text(
            '{"id":"P\\u004a","type":"ecmascript","version":"1.0.0"}',
            encoding="utf-8")
        self.assertEqual(harness.plugin_manifest_id(str(ok)), "PJ")

    def test_an_ordinary_manifest_still_reads(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.assertEqual(
            harness.plugin_manifest(plugin_dir(root / "src", "P")),
            ("P", "ecmascript"))


class RunJudgesBeforeItTouchesAnything(unittest.TestCase):
    """Two orderings inside `cmd_run`, pinned where they live.

    Everything above validated the seeding functions. These two findings
    were about WHEN `cmd_run` calls them, and that is not visible from
    `harness` at all: a correct `plan_plugin_settings` called after the dev
    flags are written still leaves dev flags behind, and a correct
    `resolve_plugin_settings` called after `--force` still kills a working
    instance for a request that was never going to run.

    Both were verified by hand on a live instance and then left unpinned,
    which is the shape of an unchecked claim. Moving either call back down
    `cmd_run` fails these.
    """

    def _args(self, **overrides) -> argparse.Namespace:
        base = dict(
            name="ordering", plugin=[], plugin_setting=[], force=False,
            dev_flags=None, skin=None, libav_log=None, start_url=None,
            bypass_ecmascript_acl=False, json=False)
        base.update(overrides)
        return argparse.Namespace(**base)

    def _instance(self, persistent: Path, pid):
        class Stub:
            def __init__(self, name):
                self.name = name
                self.persistent = persistent

            def live_pid(self):
                return pid

            def ensure_dirs(self):
                pass

        return Stub

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.persistent = self.root / "persistent"

    def test_force_does_not_kill_for_a_request_that_cannot_run(self) -> None:
        killed = []
        with mock.patch.object(cli, "Instance",
                               self._instance(self.persistent, 4242)), \
             mock.patch.object(harness, "classify_foreign",
                               return_value=([], [])), \
             mock.patch.object(harness, "kill_owned_pid",
                               side_effect=lambda *a: killed.append(a)):
            with self.assertRaises(MdevError):
                cli.cmd_run(self._args(
                    plugin=[plugin_dir(self.root / "src", "P")],
                    plugin_setting=["Bogus:g:k=1"], force=True))
        self.assertEqual(killed, [], "a working instance was stopped for a "
                                     "request that could never have run")

    def test_force_does_not_kill_for_an_id_that_is_not_a_path_component(
            self) -> None:
        """The same ordering, one layer deeper. A manifest may declare any
        string (`plugins.c:632-647`), so an id like `../P` matched a spec,
        passed resolution and was refused only by `plugin_setting_path` --
        which runs in the plan, after the kill. The check now happens at
        parse time, where both path components are decided."""
        killed = []
        weird = self.root / "src" / "weird"
        weird.mkdir(parents=True, exist_ok=True)
        (weird / "plugin.json").write_text(json.dumps({
            "type": "ecmascript", "id": "../P", "version": "1.0.0",
            "file": "main.js", "apiversion": 2}), encoding="utf-8")
        with mock.patch.object(cli, "Instance",
                               self._instance(self.persistent, 4242)), \
             mock.patch.object(harness, "classify_foreign",
                               return_value=([], [])), \
             mock.patch.object(harness, "kill_owned_pid",
                               side_effect=lambda *a: killed.append(a)):
            with self.assertRaises(MdevError) as caught:
                cli.cmd_run(self._args(
                    plugin=[str(weird)], plugin_setting=["../P:g:k=1"],
                    force=True))
        self.assertIn("single path component", str(caught.exception))
        self.assertEqual(killed, [])

    def test_the_report_does_not_print_the_value(self) -> None:
        """The documented use for this flag is a setting a plugin gates on,
        and those are cookies and tokens. A value passed through the
        environment to keep it out of shell history must not then be printed
        into a CI log -- and the report exists to settle two guesses that the
        key and the TYPE settle on their own."""
        secret = "sid=deadbeefcafe; Domain=example.invalid"
        captured = io.StringIO()
        with mock.patch.object(cli, "Instance",
                               self._instance(self.persistent, None)), \
             mock.patch.object(harness, "classify_foreign",
                               return_value=([], [])), \
             mock.patch.object(harness, "build_argv",
                               return_value=["movian"]), \
             mock.patch.object(harness, "launch",
                               return_value={"pid": 1, "port": 2,
                                             "log": "/dev/null"}), \
             contextlib.redirect_stderr(captured), \
             contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_run(self._args(
                plugin=[plugin_dir(self.root / "src", "P")],
                plugin_setting=['P:g:cookie="%s"' % secret]))
        report = captured.getvalue()
        self.assertNotIn(secret, report)
        self.assertNotIn("deadbeef", report)
        self.assertIn("cookie", report)
        self.assertIn("str", report)
        self.assertEqual(
            json.loads(harness.plugin_setting_path(
                self.persistent, "P", "g").read_text()),
            {"cookie": secret},
            "the value must still be seeded, only not printed")

    def test_dev_flags_are_not_left_behind_by_a_refused_seed(self) -> None:
        """The spec passes id and type checks and fails only when the
        existing store is read, which is what separates the two phases."""
        store = harness.plugin_setting_path(self.persistent, "P", "g")
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text("[1,2,3]", encoding="utf-8")
        with mock.patch.object(cli, "Instance",
                               self._instance(self.persistent, None)), \
             mock.patch.object(harness, "classify_foreign",
                               return_value=([], [])):
            with self.assertRaises(MdevError):
                cli.cmd_run(self._args(
                    plugin=[plugin_dir(self.root / "src", "P")],
                    plugin_setting=["P:g:k=1"], dev_flags="smbdebug=1"))
        self.assertFalse(
            (self.persistent / "settings" / "dev").exists(),
            "dev flags stayed active after the command reported failure")


class TheLeafIsOnlyTheProfilesFile(unittest.TestCase):
    """A hard link is the symlink escape with nothing to inspect.

    No target path, `realpath` inside the profile, a regular file -- and
    one inode carrying another name somewhere else. Measured: a leaf linked
    at `outside.json` turned `{"mine": true}` into `{"mine": true,
    "pwned": 1}`, through the guard that had just been written to stop
    exactly that.
    """

    def test_a_hard_linked_leaf_is_refused(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        persistent = root / "persistent"
        leaf = harness.plugin_setting_path(persistent, "P", "g")
        leaf.parent.mkdir(parents=True, exist_ok=True)
        outside = root / "outside.json"
        outside.write_text('{"mine": true}', encoding="utf-8")
        os.link(outside, leaf)
        with self.assertRaises(MdevError) as caught:
            harness.seed_plugin_settings(
                persistent, [plugin_dir(root / "src", "P")], ["P:g:pwned=1"])
        self.assertIn("names", str(caught.exception))
        self.assertEqual(json.loads(outside.read_text()), {"mine": True})


class ACommittableTargetIsProvenBeforeAnyIsWritten(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.persistent = self.root / "persistent"
        self.plugins = [plugin_dir(self.root / "src", "P")]

    def test_a_read_only_store_is_replaced_not_written_through(self) -> None:
        """The commit moves a staged sibling into place, so the leaf's own
        mode never comes into it. Writing through it raised an uncaught
        PermissionError from the middle of the loop, with the earlier target
        already seeded -- the plan had approved everything and the failure
        happened anyway, one layer further down."""
        readonly = harness.plugin_setting_path(self.persistent, "P", "ro")
        readonly.parent.mkdir(parents=True, exist_ok=True)
        readonly.write_text('{"keep": 1}', encoding="utf-8")
        os.chmod(readonly, 0o444)
        self.addCleanup(os.chmod, readonly.parent, 0o755)
        harness.seed_plugin_settings(
            self.persistent, self.plugins, ["P:good:k=1", "P:ro:k=2"])
        self.assertEqual(json.loads(readonly.read_text()),
                         {"keep": 1, "k": 2})
        self.assertEqual(
            json.loads(harness.plugin_setting_path(
                self.persistent, "P", "good").read_text()),
            {"k": 1})

    @unittest.skipIf(os.geteuid() == 0,
                     "root bypasses the mode bits this case is about: "
                     "chmod(0555) leaves os.access(W_OK) true under "
                     "CAP_DAC_OVERRIDE, so as UID 0 this would measure the "
                     "runner rather than the refusal")
    def test_an_unwritable_directory_stops_the_whole_request(self) -> None:
        """What the leaf's mode does not decide, the directory's does. The
        request is refused before any target changes, rather than partway
        through -- and this is the case `os.replace` cannot paper over."""
        settings = harness.plugin_setting_path(
            self.persistent, "P", "x").parent
        settings.mkdir(parents=True, exist_ok=True)
        os.chmod(settings, 0o555)
        self.addCleanup(os.chmod, settings, 0o755)
        # Asserted against the PLAN, not the seed. Staging would refuse
        # this anyway -- the temp file cannot be created either -- but the
        # commit runs after `mdev run` has written the core's dev flags, so
        # the guarantee that a refused request leaves nothing behind needs
        # the refusal to happen here.
        with self.assertRaises(MdevError) as caught:
            harness.plan_plugin_settings(
                self.persistent, self.plugins, ["P:a:k=1", "P:b:k=2"])
        self.assertIn("not writable", str(caught.exception))
        self.assertEqual(sorted(p.name for p in settings.iterdir()), [])

    def test_a_failed_move_takes_its_staging_files_with_it(self) -> None:
        """A move can fail for reasons no preflight covers -- EBUSY on a
        bind-mounted target -- and raising there left every not-yet-moved
        staging file in the profile. A repeatedly failing run accumulated
        complete settings snapshots under names nothing reads: litter that
        looks like state, from an operation that reported failure."""
        real = os.replace

        def refuse_the_second(src, dst, *rest):
            if str(dst).endswith("g2"):
                raise OSError(16, "Device or resource busy")
            return real(src, dst, *rest)

        with mock.patch.object(os, "replace", refuse_the_second):
            with self.assertRaises(MdevError):
                harness.seed_plugin_settings(
                    self.persistent, self.plugins,
                    ["P:g1:k=1", "P:g2:k=2", "P:g3:k=3"])
        settings = harness.plugin_setting_path(
            self.persistent, "P", "x").parent
        # g1 was already moved -- that is the stated bound -- and nothing
        # else may remain.
        self.assertEqual(sorted(p.name for p in settings.iterdir()), ["g1"])

    def test_nothing_is_staged_where_it_could_be_mistaken_for_a_group(
            self) -> None:
        """`globalSettings` reads `<storagePath>/settings/<group>` by exact
        name, so a leftover staging file is inert -- but litter in a profile
        is what a later run reads and cannot explain. The staging files are
        gone whether the request succeeded or not."""
        settings = harness.plugin_setting_path(
            self.persistent, "P", "x").parent
        harness.seed_plugin_settings(self.persistent, self.plugins,
                                     ["P:g:k=1"])
        self.assertEqual([p.name for p in settings.iterdir()], ["g"])


# Last, so that running this file directly runs every class above it. It sat
# in the middle once, which meant `python3 tests/tooling/mdev/
# test_plugin_settings.py` exited 0 after 27 of 29 tests -- silently
# skipping the two that pin the cmd_run orderings, in the very file whose
# job is to notice things like that. Discovery-based CI imports the module
# and ran them; the local command people actually type did not.
if __name__ == "__main__":
    unittest.main(verbosity=2)

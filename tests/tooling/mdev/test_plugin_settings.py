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

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)

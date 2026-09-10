#!/usr/bin/env python3
"""Seeding a plugin's own setting before launch (movian#247).

A plugin that gates its content behind a setting cannot be driven from a
fresh isolated instance, because nothing can write that setting before the
process starts. `--dev-flags` writes `<persistent>/settings/dev`, which is
the CORE's namespace and not a plugin's.

The path a plugin setting actually lands at, traced rather than guessed:

    plugins.c:240        fqid = "<manifest id>@<origin>", origin "dev" for
                         a `-p` plugin (plugins.c:1435, 1465)
    ecmascript.c:881-882 Core.storagePath = <persistent>/plugins/<fqid>
    settings.js:276,297  globalSettings stores at <storagePath>/settings/<group>
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

import importlib.util
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
                self.assertEqual(harness.parse_plugin_setting(spec)[3],
                                 expected)

    def test_an_integer_stays_an_integer(self) -> None:
        self.assertEqual(harness.parse_plugin_setting("p:g:k=42")[3], 42)
        self.assertEqual(harness.parse_plugin_setting("p:g:k=-1")[3], -1)

    def test_anything_else_is_a_string(self) -> None:
        self.assertEqual(harness.parse_plugin_setting("p:g:k=hd.example")[3],
                         "hd.example")

    def test_a_value_may_contain_the_separators(self) -> None:
        """A domain or a cookie is a perfectly ordinary setting value, and
        both carry `:` and `=`. Only the first three `:` and the first `=`
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

    def test_the_suffix_is_not_typed_by_hand(self) -> None:
        """`HDRezka@dev` is how the core names a dev load, not something a
        caller should have to know. Passing it is refused like any other
        id that is not a manifest id."""
        with self.assertRaises(MdevError):
            self.seed(["HDRezka@dev:g:k=1"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

/*
 * Two parsers, one file, one question (movian#250).
 *
 * The core reads this plugin's `plugin.json` with the C decoder in
 * `src/misc/json.c` and builds the fqid from what it finds. Duktape reads the
 * SAME bytes again through `JSON.parse(Plugin.manifest)` -- the raw manifest is
 * handed across at `plugins.c:726-727` and published as `Plugin.manifest` at
 * `ecmascript.c:900`.
 *
 * So the manifest spells its id and title with UPPERCASE hex escapes, and this
 * route publishes what each parser made of them. The two disagreeing is the
 * defect; the two agreeing is the fix. Nothing here asserts which is right --
 * it reports both and lets the reader compare, which is the point of having a
 * second producer.
 *
 * `Plugin.id` is the FQID (`<manifest id>@<origin>`, plugins.c:247), not the
 * bare id, so compare its prefix rather than the whole string.
 *
 * Not demonstrable from inside a plugin: the KEY half of the defect. A member
 * name is decoded by the same path (`json.c:162`), but the names this loader
 * needs -- `type`, `id`, `version`, `file` -- are looked up by literal, so a
 * manifest that escaped one of them would not load at all and this file would
 * never run. That absence IS the demonstration, and it belongs in a test that
 * can observe a load failure, not here.
 */

var page = require('movian/page');

new page.Route('jsonunicode:test', function(pg) {
  var duktape = JSON.parse(Plugin.manifest);

  // Everything a reader needs, in props mdev can read without a screenshot.
  pg.type = 'directory';
  pg.metadata.title = 'json_unicode fixture';

  pg.appendItem('jsonunicode:test', 'video', {
    title: 'c_fqid=' + Plugin.id,
    description: 'duktape_id=' + duktape.id +
                 ' duktape_title=' + duktape.title
  });

  // The comparison itself, so a check does not have to re-derive it.
  var expected = duktape.id + '@dev';
  pg.metadata.subtitle = Plugin.id === expected ? 'AGREE' : 'DISAGREE';

  pg.loading = false;
});

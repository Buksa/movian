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

  /*
   * The second item is not a second demonstration -- it is the other
   * direction, and mislabelling it would be worse than leaving it out.
   *
   * TMDB is the heaviest consumer of the C decoder in the tree: four entry
   * points, all `htsmsg_json_deserialize2` (tmdb.c:257, 315, 430, 575), and
   * every response goes through it. `bindVideoMetadata` reaches them --
   * page.js:25 -> es_metadata.c:92 -> metadata_bind_video_info
   * (mlp.c:1849) -> the lazy query pipeline -> TMDB over HTTP with the
   * built-in key.
   *
   * It cannot trigger the defect, and that was measured rather than assumed:
   * the live API returns `"title":"Amélie"` as raw UTF-8, with ZERO \uXXXX
   * escapes in either the search response or /configuration. Reaching the
   * defective branch through TMDB needs a mocked response that escapes
   * uppercase, which is listed separately in movian#250's exercise order.
   *
   * What it does prove is the blast radius: the single busiest consumer of
   * the changed decoder still binds metadata end to end after the fix. A
   * title with a non-ASCII character is chosen on purpose, so a fix that
   * damaged ordinary UTF-8 handling would show here immediately.
   */
  var tmdb = pg.appendItem('tmdbmock:amelie', 'video', {
    title: 'TMDB metadata: Amélie',
    description: 'Blast-radius item: exercises the decoder\'s busiest ' +
                 'consumer. Live TMDB sends raw UTF-8, so this does not ' +
                 'trigger the defect -- it shows the fix did not break it.'
  });

  tmdb.bindVideoMetadata({
    title: 'Amélie',
    year: 2001
  });

  // The comparison itself, so a check does not have to re-derive it.
  var expected = duktape.id + '@dev';
  pg.metadata.subtitle = Plugin.id === expected ? 'AGREE' : 'DISAGREE';

  pg.loading = false;
});

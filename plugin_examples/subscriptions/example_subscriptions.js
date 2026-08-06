/**
 * Property subscriptions.
 *
 * An apiversion-1 plugin (no `apiversion` in plugin.json, which
 * src/plugins.c:712 defaults to 1), so the legacy `plugin` object and the
 * `showtime` global are both available.
 *
 * Subscribing is NOT one of the things that object offers, though: the v1
 * surface in res/ecmascript/legacy/api-v1.js:102-165 has createService,
 * addURI, addSearcher, createSettings, cachePut/cacheGet and friends, and no
 * `subscribe`. Neither does the Page that `addURI` hands the callback -- it
 * is a modern `movian/page` Page (api-v1.js:112-115).
 *
 * The real API is `movian/prop`, which any plugin can require regardless of
 * apiversion.
 */

(function(plugin) {

  var prop = require('movian/prop');

  var U = "example:subscriptions:";

  // `prop.global` is a proxied prop object, so a path is written as property
  // access rather than as a dotted string.
  prop.subscribeValue(prop.global.clock.unixtime, function(v) {
    showtime.print("Current unix time is " + v);
  }, null);

  prop.subscribeValue(prop.global.navigators.current.currentpage.url,
                      function(v) {
    showtime.print("Current URL is " + v);
  }, null);

  // Register a service (will appear on home page)
  plugin.createService("Subscriptions example", U, "other", true);

  plugin.addURI(U, function(page) {
    page.type = "directory";
    page.metadata.title = "Hello";
    page.loading = false;

    // Dump the page's property tree on the console
    page.dump();

    // A page's own properties are reached through its prop root, and are
    // subscribed the same way as the globals above.
    prop.subscribeValue(page.root.url, function(v) {
      showtime.print("Page url is " + v);
    }, null);
  });

// `this` at program top level IS the v1 plugin object -- measured on a
// running instance: `this === plugin` is true. Passing the global by name
// instead says the same thing to a reader and to a type-checker, which cannot
// know what the loader binds `this` to.
})(plugin);

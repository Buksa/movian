/**
 * popup_test.js -- dev-only plugin for issue #242.
 *
 * `native/popup.message` is synchronous: es_message calls message_popup()
 * and switches on its return value (src/ecmascript/es_misc.c:154-181), and
 * message_popup builds the popup prop then blocks in popup_display()
 * waiting for an answer (src/notifications.c:223-262). A route that calls
 * it therefore never finishes, and `currentpage.loading` never clears.
 *
 * Two routes, and the pair is the point:
 *
 *   popuptest:blocking -- raises the popup from inside the handler, the way
 *       a plugin asking a first-launch question does. This is the case under
 *       test.
 *   popuptest:clean -- the same page with no popup. The control. A red on
 *       `blocking` proves nothing unless `clean` is green in the same run,
 *       because "the plugin failed to load" looks identical from outside.
 */

var page = require('movian/page');
var popup = require('native/popup');

var TAG = 'popuptest242';

function log(msg) {
  console.log(TAG + ': ' + msg);
}

function finish(pageobj, title) {
  pageobj.type = 'directory';
  pageobj.metadata.title = title;
  pageobj.loading = false;
}

new page.Route('popuptest:blocking', function(pageobj) {
  log('blocking route entered; raising popup');
  // Blocks here until something answers. Everything below this line runs
  // only after the answer -- which is what makes the page unreachable to a
  // waiter that cannot dismiss.
  var answer = popup.message('issue #242 probe: dismiss me', true, false);
  log('blocking route resumed, answer=' + answer);
  finish(pageobj, 'popup dismissed');
});

// The shape every destructive core popup has: OK and CANCEL both offered,
// and the caller acting only on OK (fileaccess.c:978 deletes files,
// metadb.c:61 clears the metadata cache). A harness that answered OK here
// would authorise whatever such a popup proposed, so the answer must come
// back false.
new page.Route('popuptest:cancelable', function(pageobj) {
  log('cancelable route entered; raising ok+cancel popup');
  var answer = popup.message('issue #242 probe: decline me', true, true);
  log('cancelable route resumed, answer=' + answer);
  finish(pageobj, answer ? 'CONFIRMED (unsafe)' : 'declined');
});

new page.Route('popuptest:clean', function(pageobj) {
  log('clean route entered; no popup');
  finish(pageobj, 'no popup here');
});

log('plugin loaded, routes: popuptest:blocking, popuptest:cancelable, popuptest:clean');

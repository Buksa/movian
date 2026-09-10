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
var settings = require('movian/settings');

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

// The shape a plugin gated behind a terms prompt has (movian#247):
// a persisted setting decides whether the question is asked at all, and
// the route parks on it when it is. HDRezka does exactly this --
// `service.tosEnabled` guards `popup.message(TOS_TEXT, true, true)` in
// pages/home.js -- and that setting is what `mdev run --plugin-setting`
// exists to seed, because answering the prompt is not something a harness
// may do (movian#245).
var askFirst = true;

settings.globalSettings('popuptest', 'popup test plugin', null,
                        'dev-only fixture for movian#242 and #247');
settings.createBool('askFirst', 'Ask before opening the gated route',
                    askFirst, function(v) {
  askFirst = !!v;
  log('askFirst = ' + askFirst);
});

new page.Route('popuptest:gated', function(pageobj) {
  log('gated route entered; askFirst=' + askFirst);
  if(askFirst) {
    var answer = popup.message('issue #247 probe: seeded away?', true, true);
    log('gated route resumed, answer=' + answer);
    finish(pageobj, 'was asked');
    return;
  }
  log('gated route not asking');
  finish(pageobj, 'not asked');
});

new page.Route('popuptest:clean', function(pageobj) {
  log('clean route entered; no popup');
  finish(pageobj, 'no popup here');
});

log('plugin loaded, routes: popuptest:blocking, popuptest:gated, popuptest:clean');

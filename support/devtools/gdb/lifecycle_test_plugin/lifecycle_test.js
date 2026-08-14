/**
 * lifecycle_test.js -- dev-only plugin for issue #145 lifecycle scenarios.
 *
 * On load it creates five known destroyable resources so the reload
 * balance proof has concrete collector signal:
 *
 *   - one route
 *   - one service
 *   - two prop subscriptions
 *   - one interval timer
 *
 * These are ECMAScript resources linked to the plugin's es_context. Service
 * creation also owns a core service and an internal delete subscription.
 * Reload unloads the old context and unlinks its resources before loading a
 * new context with the same five resources.
 *
 * Each step is wrapped so a single API mismatch cannot fail the whole load
 * (a failed-to-compile/execute plugin would make `mdev reload --js` a false
 * green).  It always logs a "plugin loaded, resources created: N" line.
 */

var page = require('movian/page');
var prop = require('movian/prop');
var service = require('movian/service');

var TAG = 'lscen145';
var created = 0;

function log(msg) {
  console.log(TAG + ': ' + msg);
}

// 1. Route registration.
try {
  new page.Route('lscen145:test', function (pg) {
    pg.type = 'raw';
    pg.metadata.title = 'lifecycle test plugin';
    pg.loading = false;
  });
  created += 1;
  log('route registered');
} catch (e) {
  log('route error: ' + e);
}

// 2. Service creation -- also creates a core service and delete subscription.
try {
  service.create('LSCEN145 Test', 'lscen145:test', 'other', true, null);
  created += 1;
  log('service created');
} catch (e) {
  log('service error: ' + e);
}

// 3. Two prop subscriptions -> prop_subscribe (x2).
try {
  var g = prop.global;
  prop.subscribe(g, function () {});
  prop.subscribe(g, function () {});
  created += 2;
  log('subscriptions created');
} catch (e) {
  log('subscribe error: ' + e);
}


// 4. Periodic timer -> a linked ECMAScript timer resource.
try {
  setInterval(function () {}, 60000);
  created += 1;
  log('timer armed');
} catch (e) {
  log('timer error: ' + e);
}

log('plugin loaded, resources created: ' + created);

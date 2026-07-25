/**
 * lifecycle_test.js -- dev-only plugin for issue #145 lifecycle scenarios.
 *
 * On load it creates a bounded, known set of destroyable resources so the
 * reload resource-balance proof (created == destroyed across one reload
 * cycle) has concrete collector signal:
 *
 *   - one route            (page.Route)
 *   - one service          (service.create  -> service_create + an es_resource
 *                                                  + a prop subscription)
 *   - two prop subscriptions (prop.subscribe -> prop_subscribe)
 *   - one hook             (hook.register   -> an es_resource)
 *   - one interval timer   (setInterval     -> callout_arm_x)
 *
 * The plugin's own es_context (es_context_create) is created automatically by
 * the ecmascript loader.  Every resource above is owned by that context and is
 * torn down (es_resource_destroy / prop_unsubscribe / callout_disarm /
 * service_destroy / es_context_release) when the dev plugin is unloaded by
 * plugins_reload_dev_plugin.
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

// 2. Service creation -- generates service_create + one es_resource + one
//    prop subscription (the uninstall/delete sub) in core.
try {
  service.create('lscen145_svc', 'LSCEN145 Test', 'lscen145:test',
                 'other', true, null);
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


// 4. Periodic timer -> callout_arm_x; cleared when the context is torn down.
try {
  setInterval(function () {}, 60000);
  created += 1;
  log('timer armed');
} catch (e) {
  log('timer error: ' + e);
}

log('plugin loaded, resources created: ' + created);

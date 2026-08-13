/**
 * viewpreview -- dev-only plugin that renders one arbitrary .view file
 * in isolation with JSON fixture data (issue #87).
 *
 * Route:   viewpreview:show:<base64(JSON config)>
 * Config:  { "view": "<abs path to .view>",
 *            "fixture": "<abs path to fixture .json>" (optional),
 *            "type": "raw" (optional, default "raw") }
 *
 * Fixture JSON schema v1 (see README.md for the full writeup):
 *   { "metadata": {k:v...}, "args": {k:v...},
 *     "nodes": [ { "type", "url", "metadata": {...}, ...extra } ] }
 * Everything optional; unknown keys pass through to props verbatim.
 *
 * On any error (bad config, missing view file, malformed fixture) this
 * plugin still renders a "raw" page pointing at its own views/error.view,
 * with the message in page.metadata.viewpreviewError -- so a broken
 * preview is always visible text, never a silent black screen -- and
 * logs the same message via console.log with a "viewpreview:" prefix
 * (see TAG below) so `mdev preview`/`mdev log --errors` can grep it.
 */

var page = require('movian/page');
var fs = require('fs');
var nfs = require('native/fs');

var TAG = 'viewpreview';
var ERROR_VIEW = Plugin.path + 'views/error.view';

function log(msg) {
  console.log(TAG + ': ' + msg);
}

function fail(pg, msg) {
  log('ERROR: ' + msg);
  pg.type = 'raw';
  pg.metadata.title = 'viewpreview error';
  pg.metadata.viewpreviewError = String(msg);
  pg.metadata.glwview = ERROR_VIEW;
  pg.loading = false;
}

// Existence + readability check for an absolute filesystem path -- used
// for both the target view and the fixture file so a missing file is
// reported by us (visible text + log line) instead of surfacing only as
// a generic GLW "Unable to open" trace with no on-screen text.
function fileReadable(path) {
  var fd;
  try {
    fd = nfs.open(path, 'r');
    return true;
  } catch (e) {
    return false;
  } finally {
    if (fd !== undefined) {
      Core.resourceDestroy(fd);
    }
  }
}

new page.Route('viewpreview:show:(.*)', function(pg, encoded) {

  var config;
  try {
    config = JSON.parse(Duktape.dec('base64', encoded));
  } catch (e) {
    fail(pg, 'malformed route config (bad base64/JSON) -- ' + e);
    return;
  }

  if (!config || !config.view) {
    fail(pg, 'config is missing the required "view" path');
    return;
  }

  if (!fileReadable(config.view)) {
    fail(pg, 'view file not found or unreadable: ' + config.view);
    return;
  }

  var fixture = {};
  if (config.fixture) {
    if (!fileReadable(config.fixture)) {
      fail(pg, 'fixture file not found or unreadable: ' + config.fixture);
      return;
    }
    try {
      fixture = JSON.parse(fs.readFileSync(config.fixture));
    } catch (e) {
      fail(pg, 'malformed fixture JSON in ' + config.fixture + ' -- ' + e);
      return;
    }
  }

  try {
    pg.type = config.type || 'raw';

    // Always give the page *some* title, even with no fixture / a
    // fixture without one: mdev's page-ready check (and `mdev open`)
    // waits for a non-void title, and a title-less raw page would
    // otherwise hang every `mdev preview` call forever. A fixture's own
    // "title" (below) overrides this.
    pg.metadata.title = TAG + ': ' + config.view.split('/').pop();

    if (fixture.metadata) {
      for (var mk in fixture.metadata) {
        pg.metadata[mk] = fixture.metadata[mk];
      }
    }

    if (fixture.args) {
      pg.model.args = fixture.args;
    }

    if (fixture.nodes) {
      for (var i = 0; i < fixture.nodes.length; i++) {
        var node = fixture.nodes[i] || {};
        var item = pg.appendItem(
          node.url || (TAG + ':node:' + i),
          node.type || 'item',
          node.metadata || {}
        );
        for (var nk in node) {
          if (nk == 'url' || nk == 'type' || nk == 'metadata')
            continue;
          item.root[nk] = node[nk];
        }
      }
    }

    // Any other top-level fixture key passes through verbatim onto the
    // page model (fixture schema v1: "unknown keys pass through to
    // props verbatim").
    var known = { metadata: 1, args: 1, nodes: 1 };
    for (var fk in fixture) {
      if (known[fk]) continue;
      pg.model[fk] = fixture[fk];
    }

    pg.metadata.glwview = config.view;
    pg.loading = false;
    log('showing ' + config.view +
        (config.fixture ? (' fixture=' + config.fixture) : ' (no fixture)'));
  } catch (e) {
    fail(pg, 'failed to apply fixture -- ' + e);
  }
});

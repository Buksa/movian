/*
 * Runtime surface dump for the modules in generated/movian-api.d.ts.
 * Keep this file ES5.1: it is loaded by Duktape, not Node.
 */

// What this capture has always inspected, kept as a literal in its original
// order because require() order is observable -- a module can mutate
// another's cached exports, and `movian/settings` does. Discovery only
// APPENDS, so today's tree observes exactly what it did before.
//
// Native modules cannot be discovered: they are registered from C
// (`ES_MODULE(...)`) rather than loaded from a file, and the runtime exposes
// no registry to enumerate. gen.py cross-checks that set against the C.
var knownModuleNames = [
  'fs',
  'http',
  'https',
  'movian/html',
  'movian/http',
  'movian/itemhook',
  'movian/page',
  'movian/popup',
  'movian/prop',
  'movian/service',
  'movian/settings',
  'movian/sqlite',
  'movian/store',
  'movian/subtitles',
  'movian/videoscrobbler',
  'movian/xml',
  'movian/xmlrpc',
  'native/crypto',
  'native/faprovider',
  'native/fs',
  'native/gumbo',
  'native/hook',
  'native/htsmsg',
  'native/io',
  'native/kvstore',
  'native/metadata',
  'native/misc',
  'native/popup',
  'native/prop',
  'native/route',
  'native/service',
  'native/sqlite',
  'native/string',
  'native/subtitle',
  'native/websocket',
  'querystring',
  'url',
  'websocket',
  'showtime/html',
  'showtime/http',
  'showtime/itemhook',
  'showtime/page',
  'showtime/popup',
  'showtime/prop',
  'showtime/service',
  'showtime/settings',
  'showtime/sqlite',
  'showtime/store',
  'showtime/subtitles',
  'showtime/videoscrobbler',
  'showtime/xml',
  'showtime/xmlrpc'
];

// Every module file that exists, plus the `showtime/` alias of each
// `movian/` one -- es_modsearch rewrites that prefix unconditionally
// (ecmascript.c:435-439), so the alias resolves to the same file through a
// separate module instance.
//
// This is what lets a recapture SEE a module nobody listed. Without it a new
// file is invisible to the capture and, in syntax the static scanner cannot
// read, invisible to the artifact too -- both sides blind, and the
// cross-check agrees about nothing.
// es_modsearch resolves a module by building its path in `char path[512]`
// with snprintf (ecmascript.c:411,449), which truncates in silence -- so an
// id that does not fit resolves to a path that is not the file, and the
// module cannot be loaded whatever it contains. gen.py applies the same
// arithmetic in `module_id_fits_resolver()`: two different bounds would make
// the census expect a module the capture cannot reach, and no recapture
// could ever clear that.
//
// The bound is also the only thing that terminates these walks. fs_scandir
// classifies entries with stat(), not lstat() (fa_fs.c:137-142), so a
// directory symlink pointing back at an ancestor reads as an ordinary
// directory and the recursion descends through it forever. Refusing here
// names the cause; a stack overflow does not, and a depth cap would quietly
// truncate the walk -- which is the failure this capture exists to refuse.
var MODSEARCH_PATH_SIZE = 512;

function refuseUnaddressablePath(url) {
  // A leaf already carries `.js`; a directory does not, and every module
  // under it will. Appending one unconditionally made this bound three bytes
  // tighter than the resolver's own and refused a 474-character id the
  // runtime loads without trouble -- the generator said it fit, this said it
  // did not, and no capture could satisfy both.
  var resolved = url.slice(-3) === '.js' ? url : url + '.js';
  if (resolved.length >= MODSEARCH_PATH_SIZE) {
    throw new Error('cannot address ' + url + ' -- es_modsearch builds a ' +
                    'module path in ' + MODSEARCH_PATH_SIZE + ' bytes and ' +
                    'truncates silently past that. A directory symlink ' +
                    'pointing back at an ancestor produces this.');
  }
}

function discoverFileModules() {
  var found = [];
  // No depth cap. es_modsearch joins a slash-separated id onto the module
  // root, so `movian/media/providers/local` is a loadable module, and the
  // generator's rglob already sees the file. A cap here and no cap there
  // means the census reports a module the capture cannot reach and no
  // recapture can clear it. The one bound is the resolver's own, which the
  // generator applies too -- see `refuseUnaddressablePath` above.
  // Ask the filesystem what an entry is instead of reading its name. A
  // directory called `vendor.js` is a legal layout -- es_modsearch loads
  // `vendor.js/helper` by joining the id onto the module root -- and
  // classifying by suffix would treat it as a module file and never look
  // inside, leaving a module the generator expects and no capture can reach.
  function walk(url, prefix, isRoot, name) {
    var names;
    refuseUnaddressablePath(url);
    try {
      names = require('fs').readdirSync(url);
    } catch (error) {
      if (isRoot) {
        throw new Error('cannot list ' + url + ' -- ' + error +
                        ' (run movian with --bypass-ecmascript-acl)');
      }
      if (/\.js$/.test(name)) {
        // Not a directory: an ordinary module file.
        found.push(prefix.slice(0, -(name.length + 1)) + name.slice(0, -3));
        return;
      }
      // Meant to be a directory and unreadable. Skipping it silently drops
      // every module beneath it, and the capture then reports fewer modules
      // with nothing saying why.
      throw new Error('cannot list ' + url + ' -- ' + error);
    }
    for (var i = 0; i < names.length; i++) {
      var entry = names[i];
      walk(url + '/' + entry, prefix + entry + '/', false, entry);
    }
  }
  walk('dataroot://res/ecmascript/modules', '', true, '');
  var aliases = [];
  for (var j = 0; j < found.length; j++) {
    if (found[j].indexOf('movian/') === 0) {
      aliases.push('showtime/' + found[j].slice('movian/'.length));
    }
  }
  return found.concat(aliases);
}

var moduleNames = knownModuleNames.slice();
var moduleDiscoveryError = null;
try {
  var discovered = discoverFileModules();
  for (var d = 0; d < discovered.length; d++) {
    if (moduleNames.indexOf(discovered[d]) < 0) {
      moduleNames.push(discovered[d]);
    }
  }
} catch (error) {
  // Recorded rather than swallowed: a capture that could not look is not a
  // capture that found nothing, and gen.py refuses to adopt it.
  moduleDiscoveryError = '' + error;
}

function describeMember(value) {
  var type = typeof value;
  var result = {
    type: type
  };

  if(type == 'function')
    result.length = value.length;

  return result;
}

function describeAccessor(descriptor) {
  var result = {
    type: 'accessor'
  };

  if(typeof descriptor.get == 'function')
    result.get = describeMember(descriptor.get);

  if(typeof descriptor.set == 'function')
    result.set = describeMember(descriptor.set);

  return result;
}

function describeKeys(value) {
  var result = {};
  var keys;
  var i;
  var key;

  try {
    keys = Object.keys(value);
  } catch(e) {
    return {
      error: String(e)
    };
  }

  for(i = 0; i < keys.length; i++) {
    key = keys[i];
    try {
      result[key] = describeMember(value[key]);
    } catch(e2) {
      result[key] = {
        type: '<error>',
        error: String(e2)
      };
    }
  }

  return result;
}

/*
 * Object.keys is the compatibility surface kept in the old records.  Shape
 * records additionally include accessor properties, which are normally
 * non-enumerable when created by Object.defineProperties.  Reading a
 * descriptor rather than value[key] keeps this walk side-effect free.
 */
function getShapeKeys(value) {
  var result = [];
  var seen = {};
  var keys;
  var i;
  var key;
  var descriptor;

  try {
    keys = Object.keys(value);
    for(i = 0; i < keys.length; i++) {
      key = keys[i];
      if(key == 'constructor' || seen[key] === true)
        continue;
      seen[key] = true;
      result.push(key);
    }
  } catch(e) {
  }

  try {
    keys = Object.getOwnPropertyNames(value);
    for(i = 0; i < keys.length; i++) {
      key = keys[i];
      if(key == 'constructor' || seen[key] === true)
        continue;

      try {
        descriptor = Object.getOwnPropertyDescriptor(value, key);
      } catch(e2) {
        descriptor = null;
      }

      if(descriptor &&
         (typeof descriptor.get == 'function' ||
          typeof descriptor.set == 'function')) {
        seen[key] = true;
        result.push(key);
      }
    }
  } catch(e3) {
  }

  return result;
}

function describeOwnProperties(value) {
  var result = {};
  var keys = getShapeKeys(value);
  var i;
  var key;
  var descriptor;

  for(i = 0; i < keys.length; i++) {
    key = keys[i];
    try {
      descriptor = Object.getOwnPropertyDescriptor(value, key);
      if(descriptor &&
         (typeof descriptor.get == 'function' ||
          typeof descriptor.set == 'function')) {
        result[key] = describeAccessor(descriptor);
      } else if(descriptor && 'value' in descriptor) {
        result[key] = describeMember(descriptor.value);
      } else {
        result[key] = describeMember(value[key]);
      }
    } catch(e) {
      result[key] = {
        type: '<error>',
        error: String(e)
      };
    }
  }

  return result;
}

function getAllPropertyKeys(value) {
  var result = [];
  var keys;
  var i;
  var key;

  try {
    keys = Object.getOwnPropertyNames(value);
  } catch(e) {
    return getShapeKeys(value);
  }

  for(i = 0; i < keys.length; i++) {
    key = keys[i];
    if(key != 'constructor')
      result.push(key);
  }

  return result;
}

function describeAllOwnProperties(value) {
  var result = {};
  var keys = getAllPropertyKeys(value);
  var i;
  var key;
  var descriptor;

  for(i = 0; i < keys.length; i++) {
    key = keys[i];
    try {
      descriptor = Object.getOwnPropertyDescriptor(value, key);
      if(descriptor &&
         (typeof descriptor.get == 'function' ||
          typeof descriptor.set == 'function')) {
        result[key] = describeAccessor(descriptor);
      } else if(descriptor && 'value' in descriptor) {
        result[key] = describeMember(descriptor.value);
      } else {
        result[key] = describeMember(value[key]);
      }
    } catch(e2) {
      result[key] = {
        type: '<error>',
        error: String(e2)
      };
    }
  }

  return result;
}

function describeEnumerableProperties(value) {
  var result = {};
  var keys;
  var i;
  var key;
  var descriptor;

  try {
    keys = Object.keys(value);
  } catch(e) {
    return {
      error: String(e)
    };
  }

  for(i = 0; i < keys.length; i++) {
    key = keys[i];
    try {
      descriptor = Object.getOwnPropertyDescriptor(value, key);
      if(descriptor &&
         (typeof descriptor.get == 'function' ||
          typeof descriptor.set == 'function')) {
        result[key] = describeAccessor(descriptor);
      } else if(descriptor && 'value' in descriptor) {
        result[key] = describeMember(descriptor.value);
      } else {
        result[key] = describeMember(value[key]);
      }
    } catch(e2) {
      result[key] = {
        type: '<error>',
        error: String(e2)
      };
    }
  }

  return result;
}

function describePrototypeLevel(value) {
  var result = {
    type: typeof value,
    keys: describeOwnProperties(value),
    prototype: null
  };
  var proto;

  try {
    proto = Object.getPrototypeOf(value);
    if(proto !== null) {
      result.prototype = {
        type: typeof proto,
        keys: describeOwnProperties(proto)
      };
    }
  } catch(e) {
    result.prototype = {
      error: String(e)
    };
  }

  return result;
}

function describeFunctionExport(value) {
  var result = {
    status: 'walked',
    prototype: null
  };
  var proto;
  var type;

  try {
    proto = value.prototype;
    type = typeof proto;
    if(proto !== null && type != 'object' && type != 'function') {
      result.prototype = {
        type: type,
        keys: {},
        prototype: null
      };
      return result;
    }

    if(proto !== null)
      result.prototype = describePrototypeLevel(proto);
  } catch(e) {
    result.status = 'failed';
    result.error = String(e);
  }

  return result;
}

function describeTier1(value) {
  var result = {
    status: 'walked',
    functionExports: {}
  };
  var keys;
  var i;
  var key;
  var descriptor;
  var member;
  var type;

  if(value === null) {
    result.status = 'not-applicable';
    return result;
  }

  type = typeof value;
  if(type != 'object' && type != 'function') {
    result.status = 'not-applicable';
    return result;
  }

  try {
    keys = Object.keys(value);
  } catch(e) {
    result.status = 'failed';
    result.error = String(e);
    return result;
  }

  for(i = 0; i < keys.length; i++) {
    key = keys[i];
    try {
      descriptor = Object.getOwnPropertyDescriptor(value, key);
      if(descriptor && !('value' in descriptor))
        continue;
      member = descriptor ? descriptor.value : value[key];
      if(typeof member == 'function')
        result.functionExports[key] = describeFunctionExport(member);
    } catch(e2) {
      result.functionExports[key] = {
        status: 'failed',
        error: String(e2)
      };
    }
  }

  return result;
}

function describeConstructed(value, depth) {
  var result = describeModule(value);
  var proto;
  var keys;
  var i;
  var key;
  var descriptor;
  var childType;
  var nested = {};

  if(value === null)
    return result;

  childType = typeof value;
  if(childType != 'object' && childType != 'function')
    return result;

  try {
    proto = Object.getPrototypeOf(value);
    if(proto !== null)
      result.prototype = describePrototypeLevel(proto);
  } catch(e) {
    result.prototype = {
      error: String(e)
    };
  }

  if(depth <= 0)
    return result;

  keys = getShapeKeys(value);
  for(i = 0; i < keys.length; i++) {
    key = keys[i];
    try {
      descriptor = Object.getOwnPropertyDescriptor(value, key);
      if(!descriptor || !('value' in descriptor))
        continue;

      childType = typeof descriptor.value;
      if(descriptor.value !== null &&
         (childType == 'object' || childType == 'function')) {
        nested[key] = describeConstructed(descriptor.value, depth - 1);
      }
    } catch(e2) {
      nested[key] = {
        type: '<error>',
        error: String(e2)
      };
    }
  }

  result.nested = nested;
  return result;
}

function describeLiveObject(value, depth) {
  var childType = typeof value;
  var result = {
    type: childType,
    keys: {},
    properties: {},
    prototype: null
  };
  var proto;
  var keys;
  var i;
  var key;
  var descriptor;
  var nested = {};

  if(value === null) {
    result.type = 'null';
    return result;
  }

  if(childType != 'object' && childType != 'function')
    return result;

  result.keys = describeEnumerableProperties(value);
  result.properties = describeAllOwnProperties(value);

  try {
    proto = Object.getPrototypeOf(value);
    if(proto !== null)
      result.prototype = describePrototypeLevel(proto);
  } catch(e) {
    result.prototype = {
      error: String(e)
    };
  }

  if(depth <= 0)
    return result;

  keys = getShapeKeys(value);
  for(i = 0; i < keys.length; i++) {
    key = keys[i];
    try {
      descriptor = Object.getOwnPropertyDescriptor(value, key);
      if(!descriptor || !('value' in descriptor))
        continue;

      childType = typeof descriptor.value;
      if(descriptor.value !== null &&
         (childType == 'object' || childType == 'function')) {
        nested[key] = describeLiveObject(descriptor.value, depth - 1);
      }
    } catch(e2) {
      nested[key] = {
        type: '<error>',
        error: String(e2)
      };
    }
  }

  result.nested = nested;
  return result;
}

function makeSkippedConstruction(reason, unreachable) {
  return {
    status: 'skipped',
    reason: reason,
    unreachable: unreachable
  };
}

function describeConstruction(name, value) {
  var parsed;

  if(name == 'movian/html' || name == 'showtime/html') {
    try {
      if(!value || typeof value.parse != 'function')
        throw new Error('parse export is not callable');

      parsed = value.parse('<p>x</p>');
      return {
        status: 'constructed',
        factory: 'parse',
        result: describeConstructed(parsed, 1),
        unreachable: []
      };
    } catch(e) {
      return {
        status: 'failed',
        factory: 'parse',
        error: String(e),
        unreachable: [{
          'class': 'Node',
          members: 'Node instance members',
          reason: 'HTML parse construction failed'
        }]
      };
    }
  }

  if(name == 'websocket') {
    return makeSkippedConstruction(
      'w3cwebsocket construction opens a socket',
      [{
        'class': 'w3cwebsocket',
        members: ['onopen', 'oninput', 'onclose', '_sock'],
        reason: 'The constructor calls native websocket clientCreate'
      }]
    );
  }

  if(name == 'movian/page' || name == 'showtime/page') {
    return makeSkippedConstruction(
      'Page construction requires a live page and navigation state',
      [{
        'class': 'Page',
        members: ['options'],
        scope: 'All Page instance members',
        reason: 'Page is created only from a live route or search callback'
      }, {
        'class': 'Route',
        members: ['route'],
        reason: 'Route construction registers a native route'
      }, {
        'class': 'Searcher',
        members: ['searcher'],
        reason: 'Searcher construction registers a global hook'
      }]
    );
  }

  if(name == 'movian/sqlite' || name == 'showtime/sqlite') {
    return makeSkippedConstruction(
      'DB construction opens a filesystem-backed database',
      [{
        'class': 'DB',
        members: ['db'],
        reason: 'The constructor calls native sqlite.create'
      }]
    );
  }

  if(name == 'movian/videoscrobbler' ||
     name == 'showtime/videoscrobbler') {
    return makeSkippedConstruction(
      'VideoScrobbler construction registers a global hook',
      [{
        'class': 'VideoScrobbler',
        members: ['paused', 'hook'],
        reason: 'The constructor calls native hook.register'
      }]
    );
  }

  if(name == 'http') {
    return makeSkippedConstruction(
      'HTTP construction is only reached by network I/O',
      [{
        'class': 'Request',
        members: ['url', 'headers', 'onResponse', 'onError'],
        reason: 'The request factory starts network I/O'
      }, {
        'class': 'Response',
        members: ['statusCode', 'encoding', 'bytes', 'onData', 'onEnd'],
        reason: 'Responses are created only by network I/O'
      }]
    );
  }

  if(name == 'movian/http' || name == 'showtime/http') {
    return makeSkippedConstruction(
      'HTTP construction is only reached by network I/O',
      [{
        'class': 'HttpResponse',
        members: ['bytes', 'allheaders', 'headers', 'headers_lc',
                  'multiheaders', 'multiheaders_lc', 'statuscode',
                  'contenttype'],
        reason: 'The request factory starts network I/O'
      }]
    );
  }

  if(name == 'movian/service' || name == 'showtime/service') {
    return makeSkippedConstruction(
      'Service construction mutates global service state',
      [{
        'class': 'Service',
        members: ['id', 'enabled'],
        reason: 'The create factory calls native service.create'
      }]
    );
  }

  return makeSkippedConstruction(
    'No side-effect-free construction is configured',
    [{
      'class': name,
      members: [],
      scope: 'Any constructor-created instance members',
      reason: 'No safe factory was called for this module'
    }]
  );
}

function makeTier3Skipped(reason, unreachable) {
  return {
    status: 'skipped',
    attempted: false,
    reason: reason,
    unreachable: unreachable
  };
}

function describeTier3Item(page, method, args) {
  var item;

  try {
    if(!page || typeof page[method] != 'function')
      throw new Error(method + ' is not callable on the route Page');

    item = page[method].apply(page, args);
    return {
      status: 'constructed',
      attempted: true,
      method: method,
      result: describeLiveObject(item, 0),
      unreachable: []
    };
  } catch(e) {
    return {
      status: 'failed',
      attempted: true,
      method: method,
      error: String(e),
      unreachable: [{
        'class': 'Item',
        members: 'Item instance members',
        reason: 'The route Page did not return an Item'
      }]
    };
  }
}

function describeTier3Websocket() {
  var url = 'ws://127.0.0.1:1/';
  var websocket = moduleRefs['websocket'];
  var socket;

  if(!websocket || typeof websocket.w3cwebsocket != 'function') {
    return {
      status: 'failed',
      attempted: true,
      url: url,
      error: 'websocket.w3cwebsocket is not callable',
      unreachable: [{
        'class': 'w3cwebsocket',
        members: ['onopen', 'oninput', 'onclose', '_sock'],
        reason: 'The websocket constructor was unavailable'
      }]
    };
  }

  try {
    socket = new websocket.w3cwebsocket(url, null);
    return {
      status: 'constructed',
      attempted: true,
      url: url,
      result: describeLiveObject(socket, 0),
      unreachable: []
    };
  } catch(e) {
    return {
      status: 'failed',
      attempted: true,
      url: url,
      error: String(e),
      unreachable: [{
        'class': 'w3cwebsocket',
        members: ['onopen', 'oninput', 'onclose', '_sock'],
        reason: 'Construction failed before the instance could be described'
      }]
    };
  }
}

function describeTier3Page(page) {
  try {
    if(!page || typeof page != 'object')
      throw new Error('Route callback did not receive a Page object');

    tier3.page = {
      status: 'constructed',
      attempted: true,
      source: 'route callback',
      result: describeLiveObject(page, 0),
      unreachable: []
    };
  } catch(e) {
    tier3.page = {
      status: 'failed',
      attempted: true,
      error: String(e),
      unreachable: [{
        'class': 'Page',
        members: ['options'],
        scope: 'All Page instance members',
        reason: 'The route callback Page could not be described'
      }]
    };
  }

  tier3.items.appendItem = describeTier3Item(page, 'appendItem', [
    'introspect:item',
    'directory',
    {
      title: 'Runtime API introspector item'
    }
  ]);
  tier3.items.appendAction = describeTier3Item(page, 'appendAction', [
    'Runtime API introspector action',
    function() {},
    'action'
  ]);
  tier3.items.appendPassiveItem = describeTier3Item(page,
                                                    'appendPassiveItem', [
    'directory',
    'introspector',
    {
      title: 'Runtime API introspector passive item'
    }
  ]);
  tier3.websocket = describeTier3Websocket();
  emitPayload(true);
}

function describeModule(value) {
  var type = typeof value;
  var result = {
    type: type,
    keys: {},
    prototype: null
  };
  var proto;

  if(value === null) {
    result.type = 'null';
    return result;
  }

  if(type != 'object' && type != 'function')
    return result;

  result.keys = describeKeys(value);

  try {
    proto = Object.getPrototypeOf(value);
    if(proto !== null) {
      result.prototype = {
        type: typeof proto,
        keys: describeKeys(proto)
      };
    }
  } catch(e) {
    result.prototype = {
      error: String(e)
    };
  }

  return result;
}

// Keyed by module name, and a module name comes off the filesystem. Duktape
// implements the `Object.prototype.__proto__` setter (duktape.c:33224), so
// `tier1['__proto__'] = record` on an ordinary object reassigns the
// prototype and creates no own property: `require('__proto__')` would
// succeed, its record would vanish from the payload, and the census would
// reject every capture with nothing to point at. A null prototype has no
// such setter to inherit.
var before = Object.create(null);
var tier1 = Object.create(null);
var tier2 = Object.create(null);
var tier3 = Object.create(null);
var moduleRefs = Object.create(null);
var loadErrors = Object.create(null);
var i;
var name;
var settings;
var routeRef = null;
var tier3RouteUrl = 'introspect:page';
var globalSettingsError = null;
var afterSettings = null;
var afterLegacySettings = null;

for(i = 0; i < moduleNames.length; i++) {
  name = moduleNames[i];
  try {
    moduleRefs[name] = require(name);
    before[name] = describeModule(moduleRefs[name]);
    tier1[name] = describeTier1(moduleRefs[name]);
    try {
      tier2[name] = describeConstruction(name, moduleRefs[name]);
    } catch(e2) {
      tier2[name] = {
        status: 'failed',
        error: String(e2),
        unreachable: [{
          'class': name,
          members: [],
          scope: 'Any constructor-created instance members',
          reason: 'Construction inspection failed before a safe result was recorded'
        }]
      };
    }
  } catch(e) {
    loadErrors[name] = String(e);
    before[name] = {
      type: '<require-error>',
      keys: {},
      prototype: null,
      error: String(e)
    };
    tier1[name] = {
      status: 'unavailable',
      functionExports: {},
      error: String(e)
    };
    tier2[name] = {
      status: 'skipped',
      reason: 'Module require failed; no runtime value is reachable',
      unreachable: [{
        'class': name,
        members: [],
        scope: 'Any constructor-created instance members',
        reason: String(e)
      }]
    };
  }
}

settings = moduleRefs['movian/settings'];
if(!settings) {
  try {
    settings = require('movian/settings');
  } catch(e3) {
    globalSettingsError = String(e3);
  }
}

if(settings && typeof settings.globalSettings == 'function') {
  try {
    settings.globalSettings(
      'runtime-api-introspector',
      'Runtime API introspector',
      null,
      'Runtime API surface inspection'
    );
  } catch(e4) {
    globalSettingsError = String(e4);
  }
} else if(globalSettingsError === null) {
  globalSettingsError = 'movian/settings.globalSettings is not callable';
}

if(settings)
  afterSettings = describeModule(settings);

try {
  afterLegacySettings = describeModule(require('showtime/settings'));
} catch(e5) {
  afterLegacySettings = {
    type: '<require-error>',
    keys: {},
    prototype: null,
    error: String(e5)
  };
}

tier3 = {
  route: {
    status: 'pending',
    attempted: false,
    url: tier3RouteUrl,
    reason: 'Route registration has not run'
  },
  page: makeTier3Skipped(
    'Route callback has not been reached',
    [{
      'class': 'Page',
      members: ['options'],
      scope: 'All Page instance members',
      reason: 'Open ' + tier3RouteUrl + ' to receive a live Page'
    }]
  ),
  items: {
    appendItem: makeTier3Skipped(
      'Route callback has not been reached',
      [{
        'class': 'Item',
        members: 'Item instance members',
        reason: 'appendItem runs only after a live Page is received'
      }]
    ),
    appendAction: makeTier3Skipped(
      'Route callback has not been reached',
      [{
        'class': 'Item',
        members: 'Item instance members',
        reason: 'appendAction runs only after a live Page is received'
      }]
    ),
    appendPassiveItem: makeTier3Skipped(
      'Route callback has not been reached',
      [{
        'class': 'Item',
        members: 'Item instance members',
        reason: 'appendPassiveItem runs only after a live Page is received'
      }]
    )
  },
  websocket: {
    status: 'skipped',
    attempted: false,
    url: 'ws://127.0.0.1:1/',
    reason: 'Route callback has not been reached',
    unreachable: [{
      'class': 'w3cwebsocket',
      members: ['onopen', 'oninput', 'onclose', '_sock'],
      reason: 'Loopback construction is deferred until the route is opened'
    }]
  }
};

// Two payloads are emitted per run and only the second is complete: at load
// time the tier3 page has not been opened, so its members are unattempted.
// They carried the SAME marker, which made "extract the unique marker" a
// coin flip -- and the documented procedure (run, then read the log without
// ---------------------------------------------------------------------------
// What this run actually read.
//
// The stamp gen.py writes is computed at ADOPTION time from whatever tree is
// on disk then. That binds the oracle to the tree it is committed with, not
// to the tree it was captured from -- and those differ whenever a capture is
// carried between checkouts, which is the normal case here: the run happens
// where build.debug lives and the adoption happens in the worktree. Edit a
// module in between, or adopt a capture taken from a slightly different
// checkout, and the old observations get stamped against sources they never
// saw.
//
// So the run records the identity of every file it could have loaded, and
// adoption refuses unless they still match byte for byte. Raw digests, not
// the comment-insensitive ones gen.py uses for staleness: capture and
// adoption are meant to be the same tree, minutes apart, so there is nothing
// to be tolerant of.
//
// Enumerating the plugin directory rather than naming its files is the point
// of doing it here: es_modsearch() tries the plugin directory BEFORE
// dataroot://res/ecmascript/modules (ecmascript.c:443-452), so dropping a
// url.js next to this file would silently shadow the core module for every
// require() below.
function hexOf(buffer) {
  var bytes = new Uint8Array(buffer);
  var out = '';
  for (var i = 0; i < bytes.length; i++) {
    var part = bytes[i].toString(16);
    out += part.length === 1 ? '0' + part : part;
  }
  return out;
}

function digestOf(path) {
  var crypto = require('native/crypto');
  var handle = crypto.hashCreate('sha256');
  crypto.hashUpdate(handle, require('fs').readFileSync(path).valueOf());
  return hexOf(crypto.hashFinalize(handle));
}

function collectInputs(url, prefix, into, required, name) {
  var names;
  refuseUnaddressablePath(url);
  try {
    names = require('fs').readdirSync(url);
  } catch (error) {
    // A ROOT that cannot be scanned is fatal, and swallowing it is how this
    // once returned two files and looked like it had worked: a plugin's fs
    // access is ACL-limited to its own directory (es_fs.c:100-106), so the
    // core module tree needs --bypass-ecmascript-acl and says so.
    if (required) {
      throw new Error('cannot read ' + url + ' -- ' + error +
                      ' (run movian with --bypass-ecmascript-acl)');
    }
    // Not a directory. Whether it is an input is decided here, by what it
    // turned out to be, not by what its name looks like -- a directory
    // called `vendor.js` is a legal layout and must be descended into.
    if (/\.(js|json)$/.test(name)) {
      into[prefix.slice(0, -(name.length + 1)) + name] = digestOf(url);
    }
    return;
  }
  for (var i = 0; i < names.length; i++) {
    var entry = names[i];
    if (entry === 'runtime-api.json') {
      // The oracle itself. Recording it would compare a capture-time digest
      // against a file adoption is about to rewrite.
      continue;
    }
    collectInputs(url + '/' + entry, prefix + entry + '/', into, false,
                  entry);
  }
}

function runtimeInputs() {
  var found = {};
  collectInputs('dataroot://res/ecmascript/modules',
                'res/ecmascript/modules/', found, true, '');
  found['res/ecmascript/legacy/api-v1.js'] =
    digestOf('dataroot://res/ecmascript/legacy/api-v1.js');
  if (!(typeof Plugin === 'object' && Plugin && Plugin.path)) {
    throw new Error('Plugin.path is not set, so the plugin directory that '
                    + 'shadows core modules cannot be enumerated');
  }
  collectInputs(Plugin.path, 'plugin/', found, true, '');
  return found;
}


// opening the route) reached only the partial one. The complete payload owns
// the documented marker; the partial one is labelled as what it is.
function emitPayload(complete) {
  var runtimeInputsResult = null;
  var runtimeInputsError = null;
  try {
    runtimeInputsResult = runtimeInputs();
  } catch (error) {
    // Recorded, never swallowed: adoption refuses a capture that cannot say
    // what it read, which is the honest outcome when this fails.
    runtimeInputsError = '' + error;
  }
  print((complete ? 'MOVIAN_API_INTROSPECTOR_JSON='
                  : 'MOVIAN_API_INTROSPECTOR_PARTIAL_JSON=') +
        JSON.stringify({
    version: 2,
    tier3PageOpened: !!complete,
    // What separates a fresh capture from a copy of the committed oracle.
    // Two runs never agree on it, so `--adopt-oracle` can refuse the file
    // already on disk without also refusing a genuine recapture that
    // happened to observe the same API -- which is what an
    // implementation-only change to a module produces.
    capturedAt: Date.now(),
    // Which BUILD answered. `mdev run` launches the binary that is there; it
    // does not rebuild. So a native registration can change in the C while
    // the running binary still reports the old surface, and stamping that
    // payload against the new sources certifies a reading nothing produced.
    // Adoption resolves this string back to a commit and refuses unless the
    // compiled sources there match the tree being stamped.
    movianVersion: (typeof Core === 'object' && Core)
                     ? Core.currentVersionString : null,
    runtimeInputs: runtimeInputsResult,
    runtimeInputsError: runtimeInputsError,
    moduleDiscoveryError: moduleDiscoveryError,
    modules: moduleNames,
    before: before,
    tier1: tier1,
    tier2: tier2,
    tier3: tier3,
    afterGlobalSettings: {
      'movian/settings': afterSettings,
      'showtime/settings': afterLegacySettings
    },
    loadErrors: loadErrors,
    globalSettingsError: globalSettingsError
  }));
}

try {
  if(!moduleRefs['movian/page'] ||
     typeof moduleRefs['movian/page'].Route != 'function')
    throw new Error('movian/page.Route is not callable');

  routeRef = new (moduleRefs['movian/page'].Route)(
    tier3RouteUrl,
    describeTier3Page
  );
  tier3.route = {
    status: 'constructed',
    attempted: true,
    url: tier3RouteUrl,
    result: describeLiveObject(routeRef, 0),
    unreachable: []
  };
} catch(e6) {
  tier3.route = {
    status: 'failed',
    attempted: true,
    url: tier3RouteUrl,
    error: String(e6),
    unreachable: [{
      'class': 'Page',
      members: 'Page and Item instance members',
      reason: 'The introspection route could not be registered'
    }]
  };
}

// Partial by construction: the tier3 page members stay unattempted until the
// route above is opened. Emitted anyway so a run that never reaches the route
// still leaves evidence in the log.
emitPayload(false);

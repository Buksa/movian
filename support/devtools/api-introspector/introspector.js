/*
 * Runtime surface dump for the modules in generated/movian-api.d.ts.
 * Keep this file ES5.1: it is loaded by Duktape, not Node.
 */

var moduleNames = [
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

var before = {};
var tier1 = {};
var tier2 = {};
var moduleRefs = {};
var loadErrors = {};
var i;
var name;
var settings;
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

print('MOVIAN_API_INTROSPECTOR_JSON=' + JSON.stringify({
  version: 2,
  modules: moduleNames,
  before: before,
  tier1: tier1,
  tier2: tier2,
  afterGlobalSettings: {
    'movian/settings': afterSettings,
    'showtime/settings': afterLegacySettings
  },
  loadErrors: loadErrors,
  globalSettingsError: globalSettingsError
}));

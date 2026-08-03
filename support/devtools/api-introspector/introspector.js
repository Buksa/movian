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
  } catch(e) {
    loadErrors[name] = String(e);
    before[name] = {
      type: '<require-error>',
      keys: {},
      prototype: null,
      error: String(e)
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
  version: 1,
  modules: moduleNames,
  before: before,
  afterGlobalSettings: {
    'movian/settings': afterSettings,
    'showtime/settings': afterLegacySettings
  },
  loadErrors: loadErrors,
  globalSettingsError: globalSettingsError
}));

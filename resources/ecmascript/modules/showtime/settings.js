var prop  = require('showtime/prop');
var store = require('showtime/store');


function createSetting(group, type, id, title) {

  var model = group.nodes[id];

  model.type = type;
  model.enabled = true;
  model.metadata.title = title;


  var item = {};

  Object.defineProperties(item, {
    model: {
      value: model
    },

    vale: {
      get: function() {
        return model.value;
      }
    },

    enabled: {
      set: function(val) {
        model.enabled = val;
      },
      get: function() {
        return parseInt(model.enabled) ? true : false;
      }
    }
  });

  return item;
}



var sp = {};

/// -----------------------------------------------
/// Destroy group
/// -----------------------------------------------

sp.destroy = function() {
  this.zombie = 1;
  if(this.id)
    delete prop.global.settings.apps.nodes[this.id];
}

/// -----------------------------------------------
/// Dump contents of group
/// -----------------------------------------------

sp.dump = function() {
  prop.print(this.nodes);
}


/// -----------------------------------------------
/// Bool
/// -----------------------------------------------

sp.createBool = function(id, title, def, callback, persistent) {
  var group = this;
  var item = createSetting(group, 'bool', id, title);

  var initial = group.getvalue(id, def, 'bool', persistent);
  item.model.value = initial;

  prop.subscribeValue(item.model.value, function(newval) {
    if(group.zombie)
      return;

    group.setvalue(id, newval, persistent);
    callback(newval);
  }, {
    noInitialUpdate: true,
    ignoreVoid: true,
    autoDestroy: true
  });
  callback(initial);
  return item;
}

/// -----------------------------------------------
/// String
/// -----------------------------------------------

sp.createString = function(id, title, def, callback, persistent) {
  var group = this;
  var item = createSetting(group, 'string', id, title);


  var initial = group.getvalue(id, def, 'string', persistent);
  item.model.value = initial;

  prop.subscribeValue(item.model.value, function(newval) {
    if(group.zombie)
      return;

    group.setvalue(id, newval, persistent);
    callback(newval);
  }, {
    noInitialUpdate: true,
    ignoreVoid: true,
    autoDestroy: true
  });

  callback(initial);
  return item;
}

/// -----------------------------------------------
/// Integer
/// -----------------------------------------------

sp.createInt = function(id, title, def, min, max, step, unit,
                        callback, persistent) {
  var group = this;

  var item = createSetting(group, 'integer', id, title);

  var initial = group.getvalue(id, def, 'int', persistent);
  item.model.value = initial;

  item.model.min  = min;
  item.model.max  = max;
  item.model.step = step;
  item.model.unit = unit;

  prop.subscribeValue(item.model.value, function(newval) {
    if(group.zombie)
      return;

    newval = parseInt(newval);
    group.setvalue(id, newval, persistent);
    callback(newval);
  }, {
    noInitialUpdate: true,
    ignoreVoid: true,
    autoDestroy: true
  });

  callback(initial);
  return item;
}


/// -----------------------------------------------
/// Divider
/// -----------------------------------------------

sp.createDivider = function(title) {
  var group = this;
  var node = prop.createRoot();
  node.type = 'separator';
  node.metadata.title = title;
  prop.setParent(node, group.nodes);
}


/// -----------------------------------------------
/// Info
/// -----------------------------------------------

sp.createInfo = function(id, icon, description) {
  var group = this;
  var node = prop.createRoot();
  node.type = 'info';
  node.description = description;
  node.image = icon;
  prop.setParent(node, group.nodes);
}


/// -----------------------------------------------
/// Action
/// -----------------------------------------------

sp.createAction = function(id, title, callback) {
  var group = this;

  var item = createSetting(group, 'action', id, title);

  prop.subscribe(item.model.action, function(type) {
    if(type == 'action')
      callback();
  }, {
    autoDestroy: true
  });

  return item;
}

/// -----------------------------------------------
/// Multiopt
/// -----------------------------------------------

sp.createMultiOpt = function(id, title, options, callback, persistent) {
  var group = this;

  var model = group.nodes[id];
  model.type = 'multiopt';
  model.enabled = true;
  model.metadata.title = title;

  var initial = group.getvalue(id, null, 'string', persistent);

  for(var i in options) {
    var o = options[i];

    var opt_id = o[0].toString();
    var opt_title = o[1];
    var opt_default = o[2];

    var opt = model.options[opt_id];

    opt.title = opt_title;

    if(initial == null && opt_default)
      initial = opt_id;
  }

  if(!initial)
    intital = o[0].toString();

  if(initial) {
    var opt = model.options[initial];
    prop.select(opt);
    prop.link(opt, model.current);
    model.value = opt_id;
    callback(initial);
  }

  prop.subscribe(model.options, function(type, a) {
    if(type == 'selectchild') {
      var selected = prop.getName(a);
      group.setvalue(id, selected, persistent);
      callback(selected);
      prop.link(a, model.current);
      model.value = id;
    }
  }, {
    noInitialUpdate: true,
    autoDestroy: true
  });


}


/// ---------------------------------------------------------------
/// Store settings using store.js and add to global settings tree
/// ---------------------------------------------------------------

exports.globalSettings = function(id, title, icon, desc) {

  this.__proto__ = sp;

  Showtime.fs.mkdirs('settings');

  this.id = id;

  var model = prop.global.settings.apps.nodes[id];
  var metadata = model.metadata;

  model.type = 'settings';
  model.url = prop.makeUrl(model);
  this.nodes = model.nodes;

  metadata.title = title;
  metadata.icon = icon;
  metadata.shortdesc = desc;

  var mystore = store.createFromPath('settings/' + id);

  this.getvalue = function(id, def) {
    return id in mystore ? mystore[id] : def;
  };

  this.setvalue = function(id, value) {
    mystore[id] = value;
  };
}



/// -----------------------------------------------
/// Store settings in the kvstore (key'ed on an URL)
/// -----------------------------------------------

exports.kvstoreSettings = function(nodes, url, domain) {

  this.__proto__ = sp;

  this.nodes = nodes;

  this.getvalue = function(id, def, type, persistent) {
    if(!persistent)
      return def;

    if(type == 'int')
      return Showtime.kvstoreGetInteger(url, domain, id, def);
    else if(type == 'bool')
      return Showtime.kvstoreGetBoolean(url, domain, id, def);
    else
      return Showtime.kvstoreGetString(url, domain, id) || def;
  };

  this.setvalue = function(id, value, persistent) {
    if(persistent)
      Showtime.kvstoreSet(url, domain, id, value);
  };
}




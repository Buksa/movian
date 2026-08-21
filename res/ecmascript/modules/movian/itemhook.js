var prop = require('movian/prop');

/**
 * @param {{itemtype?: string, title?: string, icon?: string,
 *          handler?: Function}} conf the fields this function reads
 */
exports.create = function(conf) {

  var node = prop.createRoot();

  node.itemtype = conf.itemtype;
  node.metadata.title = conf.title;
  node.metadata.icon = conf.icon;

  prop.unloadDestroy(node);

  prop.setParent(node, prop.global.itemhooks);

  prop.subscribe(node.eventSink, function(type, obj, nav) {
    if(type == 'propref') {

      nav = nav ? prop.makeProp(nav) : undefined;

      var navobj = {
        openURL: function(url) {
          if(nav)
            prop.sendEvent(nav.eventSink, 'openurl', {
              url: url
            });
        }
      };

      conf.handler(prop.makeProp(obj), navobj);
    }
  }, {
    autoDestroy: true
  });

  return {
    destroy: function() {
      prop.destroy(node);
    }
  }
}

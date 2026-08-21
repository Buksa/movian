var gumbo = require('native/gumbo');




function NodeProto() {

  Object.defineProperties(this, {
    nodeName: {
      get: function() {
        return gumbo.nodeName(this._gumboNode);
      }
    },

    nodeType: {
      get: function() {
        return gumbo.nodeType(this._gumboNode);
      }
    },

    children: {
      get: function() {
        return gumbo.nodeChilds(this._gumboNode, false).map(function(n) {
          return new Node(n);
        });
      }
    },

    textContent: {
      get: function() {
        return gumbo.nodeTextContent(this._gumboNode);
      }
    },

    attributes: {
      get: function() {
        var a = gumbo.nodeAttributes(this._gumboNode);
        a.getNamedItem = function(nam) {
          for(var i = 0; i < a.length; i++) {
            if(a[i].name == nam)
              return a[i];
          }
          return null;
        }
        return a;

      }
    }

  });
}


/**
 * @param {string} id
 * @returns {Node|null} native/gumbo.findById answers nothing when it misses
 */
NodeProto.prototype.getElementById = function(id) {
  var n = gumbo.findById(this._gumboNode, id);
  return n ? new Node(n) : null;
}

/**
 * @param {string} cls
 * @returns {Node[]}
 */
NodeProto.prototype.getElementByClassName = function(cls) {
  return gumbo.findByClassName(this._gumboNode, cls).map(function(n) {
    return new Node(n);
  });
}

NodeProto.prototype.getElementsByClassName =
  NodeProto.prototype.getElementByClassName;

/**
 * @param {string} tag
 * @returns {Node[]}
 */
NodeProto.prototype.getElementByTagName = function(tag) {
  return gumbo.findByTagName(this._gumboNode, tag).map(function(n) {
    return new Node(n);
  });
}

NodeProto.prototype.getElementsByTagName =
  NodeProto.prototype.getElementByTagName;

var nodeProto = new NodeProto();

function Node(node) {
  this._gumboNode = node;
  this.__proto__ = nodeProto;
}


/**
 *
 */
/** @param {string} html handed to native/gumbo.parse */
exports.parse = function(html) {
  var gdoc = gumbo.parse(html);

  return {
    document: new Node(gdoc.document),
    root: new Node(gdoc.root)
  };

}

# Plugin Development Notes

These notes collect small compatibility rules for Movian plugin development.
They are intentionally narrow for now and can grow into a fuller developer
guide together with more complete `plugin_examples/` coverage.

## HTML Parser Naming Compatibility

Plugins can parse HTML through:

```js
var html = require('movian/html');
var doc = html.parse(source);
```

The current long-standing API exposes these node methods:

- `getElementById(id)` returns one node or `null`.
- `getElementByClassName(className)` returns an array.
- `getElementByTagName(tagName)` returns an array.

The class and tag helpers return multiple nodes, even though their historical
names use singular `Element`. Newer Movian builds may also provide DOM-style
plural aliases:

- `getElementsByClassName(className)`
- `getElementsByTagName(tagName)`

For plugins that should run on both newer and older Movian builds, prefer a
small compatibility wrapper instead of copying the whole built-in HTML module.

Example `utils/html_compat.js`:

```js
var html = require('movian/html');

function patchNode(node) {
  if (!node)
    return node;

  var proto = node.__proto__ ||
    (Object.getPrototypeOf ? Object.getPrototypeOf(node) : null);

  if (!proto)
    return node;

  if (!proto.getElementsByClassName && proto.getElementByClassName)
    proto.getElementsByClassName = proto.getElementByClassName;

  if (!proto.getElementsByTagName && proto.getElementByTagName)
    proto.getElementsByTagName = proto.getElementByTagName;

  return node;
}

exports.parse = function(source) {
  var doc = html.parse(source);
  patchNode(doc.document);
  patchNode(doc.root);
  return doc;
};
```

Plugin code can then use the plural names consistently:

```js
var html = require('./utils/html_compat');

var doc = html.parse(body);
var items = doc.root.getElementsByClassName('item');
var links = doc.root.getElementsByTagName('a');
```

This keeps compatibility local to the plugin while leaving the built-in module
as the source of truth for parser behavior.

## Developer Guide Roadmap

A fuller plugin guide should eventually cover:

- project layout and `plugin.json`;
- route and page model basics;
- HTTP requests and HTML parsing;
- settings and persistent plugin state;
- item hooks, services, and subscriptions;
- image and metadata fields used by common skins;
- local debug runs with `support/plugin-smoke/run-plugin-smoke.sh`;
- compatibility wrappers for APIs that changed over time;
- focused examples under `plugin_examples/` for each topic.

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
names use singular `Element`. Movian also provides DOM-style plural aliases:

- `getElementsByClassName(className)`
- `getElementsByTagName(tagName)`

For plugins that should also run on older Movian builds, prefer a small
compatibility wrapper instead of copying the whole built-in HTML module.

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

## Horizontal Rows With `list_x`

Plugins can build pages with any number of independently scrollable rows. Add
each row as a passive page item whose `data` contains that row's cards:

```js
page.appendPassiveItem('list', upcomingCards, {
  title: 'Movies - Upcoming'
});
page.appendPassiveItem('list', nowPlayingCards, {
  title: 'Movies - Now Playing'
});
page.appendPassiveItem('list', topRatedCards, {
  title: 'Movies - Top Rated'
});
```

The outer cloner creates the sections. Each section then clones its own
`$self.data` into a separate `list_x`:

```view
widget(list_y, {
  cloner($self.model.nodes, container_y, {
    widget(label, {
      caption: $self.metadata.title;
    });

    widget(list_x, {
      id: "section-row";
      focusable: 1;
      chaseFocus: 0;
      navWrap: false;

      cloner($self.data, loader, {
        source: "skin://items/rect/default.view";
      });
    });

    widget(slider_x, {
      bind("section-row");
      focusable: canScroll();
      alpha: iir(canScroll(), 16);
    });
  });
});
```

Keep the optional scrollbar inside the same cloned section as its row. The
local `"section-row"` ID then binds to that section's `list_x`, so every row
keeps an independent scroll position.

Keyboard and D-pad navigation continue to use left/right focus movement. Touch
or simulated touch can drag the row horizontally, while a vertical swipe over
a card continues to scroll the outer `list_y`. On Linux/WSL this can be tested
with `--pointer-is-touch`.

A complete runnable implementation is available in
`plugin_examples/listx_cloner`.

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

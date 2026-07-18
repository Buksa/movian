# GLW widget catalog

Every registered `glw_class_t` in this tree, plus the global attribute
table shared by all widget classes. Companion to `glw-view-language.md`
(language mechanics) and `glw-patterns.md` (recipes). Every semantic claim
carries a `file:line` anchor; corpus citations are marked `(corpus: ...)`.

## Counting methodology

**51 widget classes.** Derived by:

```
grep -rhoE "GLW_REGISTER_CLASS\([a-z_0-9]+\)" src/ui/glw/*.c \
  | sed 's/GLW_REGISTER_CLASS(//;s/)//' | sort -u | wc -l
```

`GLW_REGISTER_CLASS(n)` (macro at `src/ui/glw/glw.h:769-770`) expands to a
constructor-attribute `INITIALIZER` that calls `glw_register_class(&n)` at
process startup — this is the *only* code path that registers a class with
`glw_class_find_by_name()` (used by `widget()`/`cloner()`/bare-name
sugar), so counting its call sites is exhaustive and exact for
*registered* classes (0 duplicates after `sort -u`). Two `glw_class_t`
initializers never register and are invisible to this grep: `style`
(`src/ui/glw/glw_style.c`) and `view` (`src/ui/glw/glw_view.c`) —
pseudo-classes instantiated directly by core code, never resolvable from
a view by name. The full census lives in
`generated/movian-metadata.json`, whose widget records carry a
`registered` flag (51 registered + 2 unregistered as of this note). The DSL-facing name is the class's `.gc_name` field
(**not** the C symbol) — e.g. `glw_container_x`'s `gc_name` is
`"container_x"`, and it additionally has a `gc_name2` alias `"hbox"`. Two
classes are compiled from the same C function pointers with only the name
differing (`quad`/`border`; `container_x`/`container_y`/`container_z`
share layout code pairwise) — each is still one registration, one row
below. `mdev viewdoc --check` recomputes this count from the same grep.

**Alias note**: `container_x`/`container_y`/`container_z` additionally
answer to `hbox`/`vbox`/`zbox` (`.gc_name2`, `glw_container.c:996,1012,1028`)
— the only classes in this tree with a second name.

**Near-miss note**: `glw_view.c:97-104` defines a 52nd `glw_class_t` with
`gc_name = "view"`, but it is **never registered** (no
`GLW_REGISTER_CLASS` in that file) — it's instantiated directly by
`glw_view_create()` (`glw_view.c:407`) as the internal wrapper widget
around every loaded `.view` file and is not resolvable by name from the
DSL. A `grep` for `gc_name =` therefore finds 52 names; the registered,
DSL-visible count is 51.

## Global attributes (shared by all classes)

Source: `attribtab[]`, `src/ui/glw/glw_view_attrib.c:1365-1502`. **116
entries**, counted the same way as the function table:
`grep -oE '\{"[a-zA-Z0-9_]+"' src/ui/glw/glw_view_attrib.c` over lines
1365-1502, deduplicated (0 duplicates). Resolved by exact-name `strcmp` in
`glw_view_attrib_resolve()` (`glw_view_attrib.c:1636-1650`); an unmatched
name becomes `TOKEN_UNRESOLVED_ATTRIBUTE` and is routed to the target
class's own `gc_set_*_unresolved` hook instead (see per-class tables
below) — so **not finding a name here means check the widget's own
"class-specific attributes" row**, not that it's invalid.

Only attributes whose behavior isn't fully explained by their name +
target class are called out individually; the rest are one row per group
with the anchor being the group's first line in `attribtab[]`.

| group | attributes | anchor | semantics |
|---|---|---|---|
| identity/misc strings | `style, id, how, description, parentUrl, caption, font, fragmentShader, source, alt` | `glw_view_attrib.c:1366-1376` | `id` names a widget for `glw_find_neighbour()`/`--debug-glw` lookups; `source`/`alt` set a widget's primary/fallback content URL (image, loader, video); `caption` sets label/text content (interacts with rich-string parsing, §1 of the language doc). |
| `hidden` | `hidden` | `glw_view_attrib.c:1378` | Sets/clears `GLW_HIDDEN`; hidden widgets are skipped by layout/render and (depending on class) by `cloner()`'s child-visibility bookkeeping. |
| `GLW2_*` boolean flags (`mod_flags2`) | `filterConstraintX, filterConstraintY, filterConstraintWeight, debug, noInitialTransform, focusOnClick, autoRefocusable, navFocusable, homogenous, enabled, alwaysGrabKnob, autohide, shadow, autofade, expediteSubscriptions, navWrap, autoFocusLimit, cursor, navPositional, clickable, fhpSpill, selectOnFocus, selectOnHover` | `glw_view_attrib.c:1379-1401` | 23 boolean flags on `glw_flags2`, each `{name, mod_flag, GLW2_*, mod_flags2}` — see individual behaviors below for the ones with non-obvious semantics. |
| image flags (`mod_img_flags`) | `fixedSize, bevelLeft, bevelTop, bevelRight, bevelBottom, aspectConstraint, additive, borderOnly, leftBorder, rightBorder, cornerTopLeft, cornerTopRight, cornerBottomLeft, cornerBottomRight` | `glw_view_attrib.c:1403-1417` | Only meaningful on `image`/`icon`/`backdrop`/`frontdrop`/`repeatedimage` (the classes wiring `gc_mod_image_flags`, see catalog below). |
| text flags (`mod_text_flags`) | `password, ellipsize, bold, italic, outline, permanentCursor, oskPassword, fileRequest, dirRequest` | `glw_view_attrib.c:1420-1428` | Only meaningful on `label`/`text` (the classes wiring `gc_mod_text_flags`). |
| video flags (`mod_video_flags`) | `primary, noAudio` | `glw_view_attrib.c:1430-1431` | Only meaningful on `video`. |
| simple float/int (dedicated setter) | `alpha, blur, weight, focusable, height, width, divider, zoffset` | `glw_view_attrib.c:1433-1440` | Generic widget geometry/compositing knobs (`glw_set_*` — apply to every widget uniformly, not class-routed): `weight` is the flex-weight used by `container_x/y`'s weighted-space distribution (see catalog); `focusable` sets the nav focus weight (0 = not focusable). |
| numeric (`GLW_ATTRIB_*`, `set_number`) | `maxlines, sizeScale, size, maxWidth, alphaSelf, bgalpha, saturation, time, transitionTime, angle, expansion, min, max, step, value, childAspect, center, audioVolume, aspect, alphaFallOff, blurFallOff, fill, childScale, childTilesX, childTilesY, alphaEdges, priority, spacing, Xspacing, Yspacing, cornerRadius` | `glw_view_attrib.c:1442-1474` | Generic numeric slot dispatched to the target class's `gc_set_int`/`gc_set_float` switch; **meaningful only for classes whose switch has a matching `case`** — see each class's "specific attributes" row in the catalog below (e.g. `spacing` → container/list/clist; `childTilesX/Y` → array; `min/max/step` → slider). |
| color/vector (`set_float3`/`set_float4`) | `color, translation, scaling, color1, color2, bgcolor, rotation, plane` | `glw_view_attrib.c:1476-1484` | RGB or 3/4-vector attributes, class-routed the same way as the numeric group. |
| box geometry | `padding, border, margin` | `glw_view_attrib.c:1486-1488` | `[left, top, right, bottom]` int16 vectors (`margin` is computed from a single value, `set_margin`); meaningful wherever `gc_set_int16_4` is wired. |
| enums | `align, effect` | `glw_view_attrib.c:1490-1491` | `align`: one of `center, left, right, top, bottom, topLeft, topRight, bottomLeft, bottomRight, justified` (`aligntab[]`, `glw_view_attrib.c:1014-1025`). `effect`: one of `blend, flipHorizontal, flipVertical, slideHorizontal, slideVertical` (`transitiontab[]`, `glw_view_attrib.c:1048-1054`) — the transition used by `deck`/`loader`/`view_loader` when swapping children. |
| scope/prop-reference | `args, self, itemModel, parentModel, tentative` | `glw_view_attrib.c:1493-1501` | The 4 flagged `GLW_ATTRIB_FLAG_NO_SUBSCRIPTION` (`self, itemModel, parentModel, tentative`, `glw_view_attrib.c:1494-1501`) receive the raw `prop_t*` rather than a resolved value even under plain `=` (see language doc §3's `:=` row) — this is how a `loader`/`widget` block rebinds `$self` for its subtree. |

`debug` deserves its own callout: setting it also flips on
`PROP_SUB_DEBUG`-style subscription tracing for that one widget (see
`SKILL.md`'s "Widget-local debug tracing" section, anchored at
`glw_view_attrib.c:1382`).

## Widget classes

Legend: **flags** = notable `gc_flags` (`GLW_CAN_HIDE_CHILDS` = required
for `cloner()`/hide-based child retirement; `GLW_NAVIGATION_SEARCH_BOUNDARY`
= stops keyboard nav search from escaping this widget; `GLW_DRIVE_PAGINATION`
= drives paginated backend fetches as it scrolls). **Class-specific
attributes** lists names resolved through this class's own
`gc_set_*_unresolved` hook (bespoke string-keyed, not in the global table)
or `case`s in its `gc_set_int`/`gc_set_float` that are *not* self-evident
from the global-attribute row above.

### Containers & layout

| name (`gc_name`/alias) | anchor | flags | purpose & layout behavior | class-specific attributes | snippet |
|---|---|---|---|---|---|
| `container_x` / `hbox` | `glw_container.c:994-1008` | `GLW_CAN_HIDE_CHILDS` | Lays children left-to-right. Non-weighted, X-constrained children get their requested width; weighted children (via `weight`) split remaining space; `homogenous` (global flag) makes all X-constrained children the biggest child's width (`glw_container_x_constraints`, `glw_container.c:115-246`). | `tableMode` (bool, unresolved int — joins/leaves the nearest ancestor `table` as a row, `glw_container.c:933-957`) | `widget(container_x, { spacing: 0.5em; ... })` (corpus: `support/devtools/viewpreview/views/demo-list.view:35-49`) |
| `container_y` / `vbox` | `glw_container.c:1010-1024` | `GLW_CAN_HIDE_CHILDS` | Same as `container_x`, vertical axis; supports `GLW2_AUTOFADE` retire-with-fade-out for removed children (`retire_child`, `glw_container.c:902-912`). | (same `spacing`/`padding` as `container_x`, via shared `glw_container_set_int`, `glw_container.c:857-877`) | `widget(container_y, { padding: 1em; ...})` (corpus: `support/devtools/viewpreview/views/demo-list.view:9-51`) |
| `container_z` / `zbox` | `glw_container.c:1026-1034` | `GLW_CAN_HIDE_CHILDS` | Stacks children on the Z axis; each child renders at increasing z-index (`glw_container_z_render`, `glw_container.c:766-794`); constraints are copied from the first non-hidden, non-skipped child (`glw_container_z_constraints`, `glw_container.c:598-645`). | none beyond global | `widget(container_z, { widget(loader, {...}); ...})` (corpus: `glwskins/flat/universe.view:44`) |
| `table` | `glw_container.c:1036-1042` | — | Groups `container_x` rows (joined via `tableMode: true`) into aligned columns: per-column width is the max width requested by any row's same-index child (`table_recompute`, `glw_container.c:75-109`). | (rows opt in via `container_x`'s `tableMode`) | `glwskins/flat/pages/settings.view` (settings rows) |
| `array` | `glw_array.c:749-765` | `GLW_NAVIGATION_SEARCH_BOUNDARY \| GLW_CAN_HIDE_CHILDS \| GLW_DRIVE_PAGINATION` | Fixed-grid tiler: `childTilesX`/`childTilesY` set the visible grid dimensions, `Xspacing`/`Yspacing` the gutters (`case GLW_ATTRIB_CHILD_TILES_X/Y/X_SPACING/Y_SPACING`, `glw_array.c:529-553`). Scrolls via shared `glw_scroll` mechanics (see "Scroll attributes" below). | `chaseFocus, scrollThresholdTop, scrollThresholdBottom, clipOffsetTop, clipOffsetBottom, clipAlpha, clipBlur, bottomGravity` (all via `glw_scroll_set_*_attributes`, `glw_scroll.c:315-390`, delegated from `glw_array_set_*_unresolved`, `glw_array.c:666-691`) | `widget(array, { childTilesX: 5; childTilesY: 4; ...})` (corpus: `glwskins/flat/pages/grid.view:6-36`) |
| `list_y` | `glw_list.c:722-746` | `GLW_NAVIGATION_SEARCH_BOUNDARY \| GLW_CAN_HIDE_CHILDS \| GLW_DRIVE_PAGINATION` | Vertically scrollable list; `spacing`/`padding` handled at `glw_list.c:554,600`. Same scroll-attribute set as `array` (shared `glw_scroll_control_t`). | same scroll attrs as `array` (`glw_list.c:611-631`) | `widget(list_y, { navWrap: true; cloner($self.model.nodes, loader, {...}); })` (corpus: `glwskins/flat/pages/list.view:12-41`) |
| `list_x` | `glw_list.c:747-765` | same as `list_y` | Horizontally scrollable list; otherwise identical mechanics to `list_y` (shared layout/render function pair, axis-parameterized). | same scroll attrs as `array`/`list_y` | `glwskins/flat/pages/playqueue.view` |
| `clist` | `glw_clist.c:283-295` | `GLW_NAVIGATION_SEARCH_BOUNDARY \| GLW_CAN_HIDE_CHILDS` | "Compact list" — `spacing` (`GLW_ATTRIB_SPACING`) and `center` (`GLW_ATTRIB_CENTER`) cases at `glw_clist.c:243,268`; vertical-only navigation via `glw_navigate_vertical` bubble handler. | none beyond global `spacing`/`center` | `glwskins/flat/items/list/*` rows inside a `clist` |
| `coverflow` | `glw_coverflow.c:234-245` | `GLW_NAVIGATION_SEARCH_BOUNDARY \| GLW_CAN_HIDE_CHILDS` | 3D cover-flow carousel; horizontal navigation only (`glw_navigate_horizontal`); no class-specific attributes found (`grep` of `case GLW_ATTRIB_`/`strcmp(a,` in `glw_coverflow.c` is empty). | none found | `~/movian-plugin-tmdb/views/posters.view:38` `widget(coverflow, {...})` (corpus, third-party fork — see language doc §2 `$page` caveat for the rest of that file) |
| `freefloat` | `glw_freefloat.c:276-286` | `GLW_CAN_HIDE_CHILDS` | Free-floating/particle-style child layout (no linear axis); children retire via `glw_freefloat_retire_child`. | none found | — |
| `playfield` | `glw_playfield.c:327-336` | `GLW_CAN_HIDE_CHILDS \| GLW_NAVIGATION_SEARCH_BOUNDARY` | Single-active-child stage (used for the top-level page stack, `glwskins/flat/universe.view:64`); `gc_select_child` picks the active child. | none found | `widget(playfield, { effect: blend; ... })` (corpus: `glwskins/flat/universe.view:64-79`) |
| `deck` | `glw_deck.c:492-509` | `GLW_CAN_HIDE_CHILDS` | Transition-animated single-active-child stage; `effect`/`time` control the swap animation (`GLW_ATTRIB_TRANSITION_EFFECT`/`GLW_ATTRIB_TIME` cases, `glw_deck.c:374,396`). | `page` (unresolved rstr — select active child by name/index, `glw_deck.c:419,457`), `keepPreviousActive`, `keepNextActive`, `keepLastActive`, `preloadedAreVisible` (unresolved int, `glw_deck.c:422-436`), `segway` (unresolved rstr, `glw_deck.c:460`) | `widget(deck, { effect: slideVertical; keepPreviousActive: true; cloner(...); })` (corpus: `glwskins/flat/pages/list.view:49-69`) |
| `layer` | `glw_layer.c:174-184` | `GLW_CAN_HIDE_CHILDS` | Z-stacked overlay layer (used for screensaver/OSK layering); children retire via `glw_layer_retire_child`. | none found | `widget(layer, { filterConstraintY: true; widget(playfield, {...}); })` (corpus: `glwskins/flat/universe.view:60-79`) |
| `expander_x` | `glw_expander.c:217-227` | — | Animates a single child's width open/closed by `expansion` (`GLW_ATTRIB_EXPANSION`, `glw_expander.c:180`), 0..1. | `alwaysLayout` (unresolved int — keep laying out the child even at `expansion≈0`, `glw_expander.c:204-211`) | `widget(expander_x, { expansion: iir(...); alwaysLayout: true; widget(deck, {...}); })` (corpus: `glwskins/flat/pages/list.view:46-70`) |
| `expander_y` | `glw_expander.c:228-237` | — | Same as `expander_x`, vertical axis. | same as `expander_x` | — |
| `resizer` | `glw_resizer.c:119-125` | — | Resizes/clamps a child to a target size. | `fixedWidth` (unresolved int, `glw_resizer.c:110`) | — |
| `segway` | `glw_segway.c:151-158` | — | Directional transition wrapper (used by `deck`'s `segway` unresolved attribute above) driven by a `direction` string. | `direction` (unresolved rstr, `glw_segway.c:135`) | — |

### Visual / graphics primitives

| name | anchor | flags | purpose & layout behavior | class-specific attributes | snippet |
|---|---|---|---|---|---|
| `quad` | `glw_primitives.c:232-244` | — | Flat-colored (or fragment-shaded, via `fragmentShader`) rectangle; `color`/`border`/`padding` cases at `glw_primitives.c:174,217,223`. | none beyond global | `widget(quad, { height: 1; alpha: 0.15; })` (corpus: `glwskins/flat/theme.view:6-9`) |
| `border` | `glw_primitives.c:245-254` | — | Same rendering code as `quad` with a border-only layout variant (`glw_border_layout`). | none beyond global | `glwskins/flat` bevel macros |
| `linebox` | `glw_primitives.c:288-295` | — | Wireframe/line-drawn box. | none found | — |
| `image` | `glw_image.c:1498-1519` | — | Textured rectangle loaded from `source`; `angle, saturation, aspect, childAspect, alphaSelf, sizeScale` (float, `glw_image.c:1298-1345`), `alphaEdges, cornerRadius (radius), size` (int, `glw_image.c:1351-1391`) cases; `mod_img_flags`-family flags apply (bevel/corner/border, see global table). | `maxIntensity` (unresolved float, `glw_image.c:1440-1453`) | `widget(image, { source: "..."; })` |
| `icon` | `glw_image.c:1528-1550` | — | Same class implementation as `image`; `size`/`sizeScale` additionally resize the widget itself (icon-specific branch, `w->glw_class != &glw_icon` guard, `glw_image.c:1276,1376`), because icons are meant to be self-sizing UI glyphs. | same as `image` | `glwskins/flat/items/list/*` row icons |
| `backdrop` | `glw_image.c:1559-1581` | — | Same as `image`, conventionally used for background-fill imagery (nine-patch border support via `mod_img_flags`). | same as `image` | `glwskins/flat/items/rect/*` card backgrounds |
| `frontdrop` | `glw_image.c:1590-1612` | — | Same as `image`, rendered in front of siblings (foreground overlay imagery, e.g. play-state badges). | same as `image` | `~/movian-plugin-tmdb/views/posters.view:47` (corpus, third-party) |
| `repeatedimage` | `glw_image.c:1621-1642` | — | Same as `image`, tiled/repeated instead of stretched. | same as `image` | — |
| `stencil` | `glw_clip.c:495-506` | — | Uses its `source` image as an alpha stencil mask for children. | `set_int16_4`/`set_float3`/`set_float4` wired but no bespoke unresolved names found | `widget(stencil, { source: "skin://graphics/stencil2.png"; ... })` (corpus, third-party: `~/movian-plugin-tmdb/views/posters.view:45-60`) |
| `clip` | `glw_clip.c:153-162` | — | Clips children to a rectangle; `left/top/right/bottom` and `leftPx/topPx/rightPx/bottomPx` (unresolved floats, `glw_clip.c:55-71`), `alphaOutside`/`blurOutside` (unresolved, `glw_clip.c:73-76`), plus global `plane, alphaFallOff, blurFallOff, border, scaling, rotation`. | `left, top, right, bottom, leftPx, topPx, rightPx, bottomPx, alphaOutside, blurOutside` | list/array `clipOffsetTop`/`clipOffsetBottom` idiom builds on this mechanism indirectly via `glw_scroll` |
| `fader` | `glw_clip.c:292-302` | — | Edge-fade wrapper (alpha falls off near clip edges); `float4`/`float` setters wired. | none named beyond global | — |
| `bar` | `glw_bar.c:181-189` | — | Progress/level bar; `fill` (`GLW_ATTRIB_FILL`), `color1`/`color2` cases (`glw_bar.c:135,160,165`). | none beyond global | settings/progress rows |
| `bloom` | `glw_bloom.c:298-307` | — | Bloom/glow post-effect; `value` (`GLW_ATTRIB_VALUE`) case (`glw_bloom.c:280`). | none beyond global | — |
| `cube` | `glw_cube.c:102-107` | — | 3D cube primitive (e.g. cube-transition backdrop). | none found | — |
| `cursor` | `glw_cursor.c:244-250` | — | Focus-highlight cursor overlay that tracks the currently focused widget's on-screen box; only meaningfully visible in real GLW keyboard mode (see `isNavFocused` keyboard-mode caveat in the language doc). | none found | flat skin's list-row highlight |
| `displacement` | `glw_displacement.c:201-211` | — | Applies a translation/scaling/rotation displacement to its child without affecting layout constraints; `translation`/`scaling`/`rotation`/`padding` cases (`glw_displacement.c:150-188`). | none beyond global | — |
| `rotator` | `glw_rotator.c:70-75` | — | Continuously rotating child wrapper. | none found | — |
| `mirror` | `glw_mirror.c:92-98` | — | Renders a mirrored reflection of its child. | none found | — |
| `flicker` | `glw_flicker.c:98-104` | — | Randomized flicker/glitch effect wrapper. | none found | — |
| `slideshow` | `glw_slideshow.c:325-336` | `GLW_CAN_HIDE_CHILDS` | Auto-advancing image slideshow; `time`/`transitionTime` cases (`glw_slideshow.c:300,307`). | none beyond global | `glwskins/flat/pages/slideshow.view` |
| `throbber` | `glw_throbber.c:271-279` | — | 2D spinner; `color` case (`glw_throbber.c:259`). | none beyond global | `widget(throbber, { alpha: iir(!$clone.ready, 8); })` (corpus, third-party: `~/movian-plugin-tmdb/views/posters.view:54-56`) |
| `throbber3d` | `glw_throbber.c:141-147` | — | 3D variant of `throbber`. | none found | — |
| `throbbertri` (`gc_name`) | `glw_throbber.c:385-391` | — | Triangular-motif variant of `throbber`. | none found | — |
| `underscan` | `glw_underscan.c:80-85` | — | Applies a fixed inset (TV safe-area underscan compensation) around its child. | none found | top-level skin wrapper (`glwskins/flat/universe.view:59`) |

### Text

| name | anchor | flags | purpose & layout behavior | class-specific attributes | snippet |
|---|---|---|---|---|---|
| `label` | `glw_text_bitmap.c:1512-1533` | — | Read-only text; `color`/`bgcolor` (`glw_text_bitmap.c:915,917`), `padding` (935), `font` (982), `size`/`sizeScale` (1066,1091), `bgalpha` (1094), `maxWidth` (1124), `maxlines` (1131) cases; text-flag family from the global table (`bold, italic, outline, ellipsize, ...`). Rich-string (`'...'`) captions get HTML-tag/entity parsing (language doc §1). | none beyond global | `widget(label, { caption: $self.model.metadata.title ?? "(untitled)"; size: 1.6em; })` (corpus: `support/devtools/viewpreview/views/demo-list.view:12-15`) |
| `text` | `glw_text_bitmap.c:1543-1571` | — | Same rendering as `label` plus editable-cursor support (`fileRequest`/`dirRequest`/`oskPassword`/`permanentCursor` text flags meaningfully apply here) and `description` (placeholder text, `set_description`). | none beyond global | OSK / settings text-entry rows (`glwskins/flat/osk.view`) |

### Input / interactive

| name | anchor | flags | purpose & layout behavior | class-specific attributes | snippet |
|---|---|---|---|---|---|
| `slider_x` | `glw_slider.c:706-722` | — | Horizontal draggable slider; `min`/`max`/`step` (`GLW_ATTRIB_INT_MIN/MAX/STEP`, `glw_slider.c:600-624`), `tentative` prop output (`GLW_ATTRIB_TENTATIVE_VALUE`, `glw_slider.c:675-687`). | `keyStep` (bool, arrow-key stepping on/off), `knobOverEdges` (bool), `secondBarValue` (float — a second fill-bar value, e.g. buffered-vs-played) — all unresolved, `glw_slider.c:653-702` | scrollbars (`ScrollBar()` macro), volume/seek sliders |
| `slider_y` | `glw_slider.c:723-737` | — | Vertical variant of `slider_x`, same attribute surface. | same as `slider_x` | — |
| `keyintercept` | `glw_keyintercept.c:224-231` | — | Transparent widget that intercepts raw key/input events for a subtree without rendering anything itself; binds to a property (`gc_bind_to_property`). | none found | OSK / custom key handling |

### Structural / special

| name | anchor | flags | purpose & layout behavior | class-specific attributes | snippet |
|---|---|---|---|---|---|
| `dummy` | `glw_dummy.c:46-51` | — | Empty placeholder widget (used as `cloner()`'s internal anchor child, language doc's `cloner` entry, and as a plain spacer). | none | `widget(dummy, { height: 0.5em; })` (corpus: `support/devtools/viewpreview/views/demo-list.view:24-26`) |
| `detachable` | `glw_detachable.c:112-120` | — | Lets a subtree be detached and rendered through an alternate render-context path (`gc_detach_control`/`gc_get_rctx`) — e.g. picture-in-picture-style detachment. | none found | — |
| `loader` (`gc_name`) | `glw_view_loader.c:411-428` | — | Loads and instantiates another `.view` file (or `void` to show nothing) as its child, with `effect`/`time` transition on swap (`GLW_ATTRIB_TRANSITION_EFFECT`/`GLW_ATTRIB_TIME`, `glw_view_loader.c:311,334`). `args:` (`GLW_ATTRIB_ARGS`, line 358) sets `$args` for the loaded view; `self:` (`GLW_ATTRIB_PROP_SELF`, line 365) rebinds `$self` — when neither is set, the loaded view **inherits the exact same scope** as the loader (`vl->scope = glw_scope_retain(w->glw_scope)`, `glw_view_loader.c:200`; this is the mechanism the viewpreview README's "how the target view sees the page model" section is built on). | none beyond global `source`/`alt` | `widget(loader, { source: $self.model.metadata.glwview; })` (corpus: `glwskins/flat/pages/raw.view:1-6`) |
| `popup` | `glw_popup.c:192-199` | — | Floating popup positioned relative to a screen coordinate; `aspect` case (`glw_popup.c:153`). | `screenPositionX`, `screenPositionY` (unresolved floats, `glw_popup.c:175,181`) | `cloner($core.popups, loader, { source: "popups/" + $self.type + ".view"; })` (corpus: `glwskins/flat/universe.view:80-82`) |
| `video` | `glw_video_common.c:1007-1027` | — | Video-rendering surface; `audioVolume`/`priority` (`glw_video_common.c:841,862`), `itemModel`/`parentModel` propref cases (886,891), `parentUrl` (913); `noAudio`/`primary` video flags from the global table. | `bottomOverlayDisplacement` (unresolved float, `glw_video_common.c:992`) | `glwskins/flat/pages/video.view` |

## Scroll attributes (shared helper, not a widget class)

`array`, `list_x`, and `list_y` all delegate their bespoke scroll-related
unresolved attributes to one shared helper,
`glw_scroll_set_int_attributes()`/`glw_scroll_set_float_attributes()`
(`src/ui/glw/glw_scroll.c:315-390`) — **not** a `GLW_REGISTER_CLASS`
registration itself, so it is not counted among the 51, but it's the
actual anchor for these commonly-seen names:

| name | type | anchor |
|---|---|---|
| `clipAlpha` | float | `glw_scroll.c:318` |
| `clipBlur` | float | `glw_scroll.c:326` |
| `chaseFocus` | int | `glw_scroll.c:344` |
| `scrollThresholdTop` | int | `glw_scroll.c:349` |
| `scrollThresholdBottom` | int | `glw_scroll.c:357` |
| `clipOffsetTop` | int | `glw_scroll.c:365` |
| `clipOffsetBottom` | int | `glw_scroll.c:373` |
| `bottomGravity` | int | `glw_scroll.c:381` |

## Coverage & gaps

Documented: name, `gc_flags` of note, purpose, layout behavior, and every
class-specific (`gc_set_*_unresolved` or non-global `case`) attribute found
by grep for all 51 registered classes, plus the 116-entry global attribute
table and the 8-name shared scroll-attribute helper.

Intentionally **not** documented at the same depth:

- **Video backend internals** (`glw_video_opengl.c`, `glw_video_rsx.c`,
  `glw_video_vdpau.c`, `glw_video_android.c`, `glw_video_ios.c`,
  `glw_video_sunxi.c`, `glw_video_vda.c`, `glw_video_yuvp.c`,
  `glw_video_overlay.c`) — these are platform-specific rendering
  backends for the single `video` class, not separate widget classes (none
  of them call `GLW_REGISTER_CLASS`); out of scope per issue #88's
  boundaries ("video/media widget internals").
- **`glw_rsx.c`, `glw_opengl_*.c`, `glw_texture_*.c`, `glw_renderer.c`,
  `glw_math*.c`** — rendering/GPU plumbing shared by all widgets, not
  widget classes themselves.
- **`glw_settings.c`, `glw_navigation.c`, `glw_event.c`, `glw_style.c`** —
  cross-cutting support modules (settings-prop wiring, focus/nav search,
  event bubbling, style cascade) referenced from the tables above but not
  separately cataloged as widgets, because they don't register one
  (`glw_style.c` does define the unregistered `style` pseudo-class — see
  the counting note at the top).
- Exact pixel-level layout math for `container_x/y`'s weighted-space
  distribution and `slider`'s knob/edge math is anchored above but not
  reproduced formula-by-formula — read the cited functions directly for
  that level of detail.
- The `<style>`-cascade specificity/inheritance model (`style()`/
  `newstyle()` as language functions are documented in
  `glw-view-language.md`; the full cascade algorithm in `glw_style.c` is
  not traced here).

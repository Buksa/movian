/**
 * WebSocket Client Example
 *
 * Real-time communication with a WebSocket server.
 *
 * There is no `WebSocket` global in Movian. The client is a CommonJS module:
 * `require('websocket').w3cwebsocket(url, protocol)`, which wraps
 * `native/websocket` (res/ecmascript/modules/websocket.js). Its surface is
 * close to the browser one but not identical, and the differences are the
 * point of this example:
 *
 *   - incoming data arrives on `oninput`, not `onmessage`
 *   - there is no `onerror` and no `readyState`; a failure surfaces as
 *     `onclose`, so connection state is tracked here instead
 *   - `close()` destroys the underlying resource and then fires `onclose`
 */

var page = require('movian/page');
var prop = require('movian/prop');
var websocket = require('websocket');

// The live client, or null while disconnected. There is no readyState to ask.
var ws = null;
var connected = false;

new page.Route("example:websocket:", function(page) {
  page.type = "directory";
  page.metadata.title = "WebSocket Demo";

  page.appendPassiveItem("label", {
    title: "WebSocket Status: " + (connected ? "Connected" : "Disconnected")
  });

  page.appendItem("", "directory", {
    title: ws ? "Disconnect" : "Connect to Server"
  }).onSelect = function() {
    if (ws) {
      ws.close();
      ws = null;
      connected = false;
    } else {
      ws = new websocket.w3cwebsocket("wss://echo.websocket.org/");

      ws.onopen = function() {
        connected = true;
        console.log("WebSocket connected");
      };

      // `oninput`, not `onmessage`. websocket.js:17-21 calls it with a
      // `{ data: <payload> }` object, so the argument reads the same way.
      ws.oninput = function(event) {
        console.log("Received:", event.data);
        // `prop.global` is a proxied prop object: assigning through it writes
        // the value. There is no prop.setGlobal.
        prop.global.example.websocket.message = event.data;
      };

      // No onerror exists -- a failed connection arrives here too.
      ws.onclose = function() {
        connected = false;
        ws = null;
        console.log("WebSocket closed");
      };
    }
    page.redirect("example:websocket:");
  };

  if (ws) {
    page.appendItem("", "directory", {
      title: "Send Test Message"
    }).onSelect = function() {
      if (connected) {
        ws.send("Hello from Movian plugin!");
      }
    };
  }

  page.loading = false;
});

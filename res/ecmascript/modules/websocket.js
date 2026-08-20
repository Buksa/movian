var ws = require('native/websocket');


/**
 * @param {string} URL passed to native/websocket.clientCreate
 * @param {string} protocol the subprotocol, clientCreate's second argument
 */
exports.w3cwebsocket = function(URL, protocol) {

  var self = this;

  /** @type {() => void} */
  self.onopen  = function() {}
  /** @type {(event: {data: any}) => void} */
  self.oninput = function() {}
  /** @type {() => void} */
  self.onclose = function() {}

  this._sock = ws.clientCreate(URL, protocol, {

    onConnect: function() {
      self.onopen();
    },
    onInput: function(d) {
      self.oninput({
        data: d
      });
    },
    onClose: function(code, msg) {
      self.onclose();
    }
  });
}


/**
 * @param {string|DuktapeBuffer} d es_websocket_client_send tries
 *   duk_get_buffer_data first and falls back to duk_to_string, so a buffer is
 *   a binary frame (opcode 2) and a string is a text frame (opcode 1)
 */
exports.w3cwebsocket.prototype.send = function(d) {
  ws.clientSend(this._sock, d);
}

/** @param {*} [d] never read; close takes no code or reason here */
exports.w3cwebsocket.prototype.close = function(d) {
  Core.resourceDestroy(this._sock);
  setTimeout(function() {
    this.onclose();
  }.bind(this), 0);
}

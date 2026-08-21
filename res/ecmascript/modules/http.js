var string = require('native/string');

function Response(res) {
  this.statusCode = res.statuscode;
  this.encoding = 'utf8';
  this.bytes = res.buffer;
  /**
    * Replaced by on('data'). Declared with the handler's signature rather than
    * left to infer `() => void` from the placeholder -- the module calls it
    * with the decoded body one line below, which a zero-argument type rejects.
    * @type {(data: any) => void} */
  this.onData = function() {}
  /** @type {() => void} */
  this.onEnd = function() {}

  var resp = this;

  setTimeout(function() {
    resp.onData(string.utf8FromBytes(resp.bytes, resp.encoding));
    resp.onEnd();
  }, 0);
}

/** @param {string} enc handed to native/string.utf8FromBytes as the charset */
Response.prototype.setEncoding = function(enc) {
  this._encoding = enc;
}

/**
 * @param {string} event 'data' or 'end'; anything else is ignored
 * @param {(...args: any[]) => void} fn replaces the placeholder set in the
 *   constructor. Deliberately variadic: one `on` serves handlers of two
 *   different arities -- 'data' takes the decoded body, 'end' takes nothing --
 *   so no single fixed signature is assignable to both.
 */
Response.prototype.on = function(event, fn) {
  if(event == 'data')
    this.onData = fn;
  if(event == 'end')
    this.onEnd = fn;
}


function Request(url) {
  this.url = url
  this.headers = [];
  /** @type {(response: Response) => void} */
  this.onResponse = function() {}
  /** @type {(err: any) => void} */
  this.onError = function() {}
}


Request.prototype.end = function() {
  var io = require('native/io')

  var ctrl = {};
  ctrl.debug = true;
  var req = this;

  io.httpReq(this.url, ctrl, function(err, res) {
    if(err) {
      req.onError(err);
    } else {
      req.onResponse(new Response(res));
    }
  });
}

/**
 * @param {string} name 'response' or 'error'; anything else is ignored
 * @param {(...args: any[]) => void} fn replaces the placeholder set in the
 *   constructor. Variadic for the same reason as Response.prototype.on.
 */
Request.prototype.on = function(name, fn) {
  if(name == 'response')
    this.onResponse = fn;
  if(name == 'error')
    this.onError = fn;
}

/**
 * @param {string|Object} opts a URL, or an object url.format() can render
 * @param {unknown} [callback] accepted and NEVER invoked anywhere in this
 *   module -- Request.on('response') is the real callback surface. Typing it
 *   `Function` would advertise a callable contract the code does not have and
 *   reject values the wrapper safely ignores; the accepted declaration at
 *   tests/reference/http.d.ts:26 says `unknown` for the same reason.
 * @param {boolean} [https] set by the https module wrapper
 * @returns {Request}
 */
exports.request = function(opts, callback, https) {
  var url = typeof(opts) === 'string' ? opts : require('url').format(opts);
  return new Request(url);
}


/**
 * @param {string|Object} opts a URL, or an object url.format() can render
 * @param {unknown} [callback] forwarded to request, which never invokes it
 * @param {boolean} [https] set by the https module wrapper
 * @returns {Request}
 */
exports.get = function(opts, callback, https) {

  var r = exports.request(opts, callback, https);
  r.end();
  return r;
}

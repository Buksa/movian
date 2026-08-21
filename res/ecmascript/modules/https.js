

/**
 * @param {string|Object} opts forwarded to http.request
 * @param {unknown} [cb] forwarded to http.request, which never invokes it
 */
exports.request = function(opts, cb) {
  return require('./http').request(opts, cb, true);
}
/**
 * @param {string|Object} opts forwarded to http.get
 * @param {unknown} [cb] forwarded to http.get, which never invokes it
 */
exports.get = function(opts, cb) {
  return require('./http').get(opts, cb, true);
}

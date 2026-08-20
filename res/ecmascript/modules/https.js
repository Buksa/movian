

/**
 * @param {string|Object} opts forwarded to http.request
 * @param {Function} [cb] forwarded to http.request
 */
exports.request = function(opts, cb) {
  return require('./http').request(opts, cb, true);
}
/**
 * @param {string|Object} opts forwarded to http.get
 * @param {Function} [cb] forwarded to http.get
 */
exports.get = function(opts, cb) {
  return require('./http').get(opts, cb, true);
}

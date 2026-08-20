

/**
 * @param {string} filename passed to native/fs.open
 * @param {string|DuktapeBuffer} data native/fs.write coerces with
 *   duk_to_buffer, so a string payload and a buffer are both real: the
 *   file-copy round trip writeFileSync(dst, readFileSync(src)) depends on it
 * @param {*} [opts] accepted for Node compatibility and never read here
 */
exports.writeFileSync = function(filename, data, opts) {
  var fs = require('native/fs');
  var fd = fs.open(filename, 'w');
  try {
    fs.write(fd, data, 0, null, 0);
  }
  finally {
    Core.resourceDestroy(fd);
  }
}

/**
 * @param {string} filename passed to native/fs.open
 * @param {*} [opts] accepted for Node compatibility and never read here
 * @returns {DuktapeBuffer} the buffer native/fs.read filled
 */
exports.readFileSync = function(filename, opts) {
  var fs = require('native/fs');
  var fd = fs.open(filename, 'r');
  try {
    var buf = new Duktape.Buffer(fs.fsize(fd));
    fs.read(fd, buf.valueOf(), 0, buf.length, 0);
    return buf;
  } finally {
    Core.resourceDestroy(fd);
  }
}

/**
 * @param {string} path
 * @returns {string[]}
 */
exports.readdirSync = function(path) {
  return require('native/fs').readdir(path);
}

/** @param {string} filename */
exports.unlinkSync = function(filename) {
  require('native/fs').unlink(filename);
}

/** @param {string} path */
exports.mkdirSync = function(path) {
  require('native/fs').mkdirs(path);
}

/** @param {string} path */
exports.rmdirSync = function(path) {
  require('native/fs').rmdir(path);
}

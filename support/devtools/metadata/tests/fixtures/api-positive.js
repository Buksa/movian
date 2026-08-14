// ES5.1 API contract fixture; this file is parsed, never executed.
var fs = require('fs');
var page = require('movian/page');
var prop = require('movian/prop');
var settings = require('movian/settings');
var nativeFs = require('native/fs');

function exerciseApi() {
  var route = new page.Route('api-fixture:', function(current) {
    current.type = 'directory';
    current.metadata.title = 'API fixture';
  });
  var root = prop.createRoot('api-fixture');
  var model = settings.globalSettings(
    'api-fixture', 'API fixture', null, 'Contract fixture');
  var names = fs.readdirSync('fixtures');
  fs.mkdirSync('fixtures/tmp');
  fs.unlinkSync('fixtures/tmp/old');
  fs.rmdirSync('fixtures/tmp');
  nativeFs.readdir(root, names);
  return [route, model, names];
}

console.log(exerciseApi, Plugin, Core, Duktape);

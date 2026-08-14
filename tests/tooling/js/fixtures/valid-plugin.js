// ES5.1/CommonJS-looking plugin source; analyzer must compile only.
var fs = require('fs');
var page = require('movian/page');
var settings = require('movian/settings');

function register() {
  var route = new page.Route('fixture:', function(p) {
    p.type = 'directory';
    p.metadata.title = 'Fixture';
  });
  var setting = settings.globalSettings(
    'fixture', 'Fixture', null, 'Analyzer fixture');
  return [route, setting, fs.readdirSync];
}

module.exports = register;

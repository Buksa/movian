var page = require('movian/page');
var http = require('movian/http');
var io = require('native/io');

var inspectors = [];

new page.Route('http401test:(.*)', function(pg, encoded) {
  pg.type = 'directory';
  pg.metadata.title = 'HTTP 401 inspector test';

  try {
    var config = JSON.parse(Duktape.dec('base64', encoded));

    if(config.inspector) {
      inspectors.push(io.httpInspectorCreate(
        '^' + config.url + '$',
        function(req) {
          req.proceed();
        },
        false
      ));
    }

    var options = { debug: true };
    if(config.noFail)
      options.noFail = true;

    var response = http.request(config.url, options);
    pg.metadata.outcome = JSON.stringify({
      ok: true,
      status: response.statuscode,
      body: response.toString()
    });
  } catch(error) {
    pg.metadata.outcome = JSON.stringify({
      ok: false,
      error: String(error)
    });
  }

  pg.loading = false;
});

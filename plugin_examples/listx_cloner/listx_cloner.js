var page = require('movian/page');
var service = require('movian/service');

var PREFIX = 'example:listx-cloner:';

service.create('list_x Cloner Example', PREFIX, 'other', true);

function makeCards(prefix, count) {
  var cards = [];

  for (var i = 1; i <= count; i++) {
    cards.push({
      title: prefix + (i < 10 ? '0' : '') + i,
      url: PREFIX + 'item:' + prefix + i
    });
  }

  return cards;
}

new page.Route(PREFIX, function(page) {
  page.metadata.title = 'list_x Cloner Example';
  page.metadata.glwview = Plugin.path + 'listx_cloner.view';
  page.type = 'raw';

  page.appendPassiveItem('list', makeCards('U', 20), {
    title: 'Movies - Upcoming'
  });
  page.appendPassiveItem('list', makeCards('N', 18), {
    title: 'Movies - Now Playing'
  });
  page.appendPassiveItem('list', makeCards('T', 16), {
    title: 'Movies - Top Rated'
  });
  page.appendPassiveItem('list', makeCards('D', 14), {
    title: 'Movies - Documentaries'
  });
  page.appendPassiveItem('list', makeCards('C', 12), {
    title: 'Movies - Classics'
  });

  page.loading = false;
});

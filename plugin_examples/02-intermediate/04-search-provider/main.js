/**
 * Search Provider Example
 * 
 * Adds custom search to Movian's global search.
 * User searches from home screen, results appear from this plugin.
 * 
 * NOTE: Uses sample videos for demonstration. 
 * In production, query your actual video database/API.
 */

var page = require('movian/page');

// Sample video database for mock search
var VIDEO_DATABASE = [
  { title: "Big Buck Bunny", url: "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4", tags: ["animation", "bunny", "short"] },
  { title: "Elephants Dream", url: "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ElephantsDream.mp4", tags: ["animation", "dream", "open source"] },
  { title: "Sintel", url: "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/Sintel.mp4", tags: ["animation", "fantasy", "dragon"] },
  { title: "Tears of Steel", url: "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/TearsOfSteel.mp4", tags: ["scifi", "robot", "short"] },
  { title: "Volkswagen GTI", url: "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/VolkswagenGTIReview.mp4", tags: ["car", "review", "gti"] }
];

// Global searcher registration
new page.Searcher("Example Search", "icon.png", function(page, query) {
  page.metadata.title = "Search: " + query;
  page.type = "directory";
  
  // Mock search: filter sample database by query
  var lowerQuery = query.toLowerCase();
  var results = VIDEO_DATABASE.filter(function(video) {
    return video.title.toLowerCase().indexOf(lowerQuery) !== -1 ||
           video.tags.some(function(tag) { return tag.indexOf(lowerQuery) !== -1; });
  });
  
  if (results.length === 0) {
    page.appendPassiveItem("label", { 
      title: "No results for '" + query + "'" 
    });
  } else {
    results.forEach(function(r) {
      page.appendItem(r.url, "video", {
        title: r.title,
        description: "Tags: " + r.tags.join(", ")
      });
    });
  }
  
  page.loading = false;
});

// A plugin's OWN search entry point.
//
// There is no per-page search in Movian: a Page has neither a `searchable`
// flag nor an `onsearch` hook, and nothing in the core would call them. The
// only search integration is the global Searcher registered above, which the
// user reaches from the home screen.
//
// What a plugin can offer on its own is a route that carries the query in the
// URL. `page.Route` passes each regex capture group to the callback after the
// Page (page.js:386-396), so the group below arrives as `query`.
new page.Route("example:search:(.*)", function(page, query) {
  page.type = "directory";
  page.metadata.title = "Search: " + query;

  var lowerQuery = decodeURIComponent(query).toLowerCase();
  var matches = VIDEO_DATABASE.filter(function(video) {
    return video.title.toLowerCase().indexOf(lowerQuery) !== -1;
  });

  matches.forEach(function(video) {
    page.appendItem(video.url, "video", {
      title: video.title,
      description: "Matched: '" + lowerQuery + "'"
    });
  });

  if (matches.length === 0) {
    page.appendPassiveItem("label", {
      title: "No matches for '" + lowerQuery + "'"
    });
  }

  page.loading = false;
});

// The directory that links into it. Try: 'animation', 'scifi', 'car'.
new page.Route("example:searchable:", function(page) {
  page.type = "directory";
  page.metadata.title = "Searchable Directory";

  ["animation", "scifi", "car"].forEach(function(term) {
    page.appendItem("example:search:" + encodeURIComponent(term), "directory", {
      title: "Search for '" + term + "'"
    });
  });

  page.loading = false;
});

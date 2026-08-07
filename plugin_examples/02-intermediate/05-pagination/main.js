/**
 * Pagination Example
 * 
 * Handles multi-page content (like "Load More" or page numbers).
 * Shows both: append-more and replace-content patterns.
 * 
 * NOTE: Uses sample videos for demonstration.
 */

var page = require('movian/page');

// Sample video database for pagination demo
var SAMPLE_VIDEOS = [
  { id: 1, url: "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4", title: "Big Buck Bunny" },
  { id: 2, url: "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ElephantsDream.mp4", title: "Elephants Dream" },
  { id: 3, url: "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/Sintel.mp4", title: "Sintel" },
  { id: 4, url: "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/TearsOfSteel.mp4", title: "Tears of Steel" },
  { id: 5, url: "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/VolkswagenGTIReview.mp4", title: "Volkswagen GTI Review" },
  { id: 6, url: "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/WeAreGoingOnBullrun.mp4", title: "Bullrun" },
  { id: 7, url: "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/WhatCarCanYouGetForAGrand.mp4", title: "What Car Can You Get" }
];

// Helper to get video by index (with wraparound)
function getVideo(index) {
  return SAMPLE_VIDEOS[index % SAMPLE_VIDEOS.length];
}

// Pattern 1: Append more items (infinite scroll style)
new page.Route("example:paginate:append", function(page) {
  page.type = "directory";
  page.metadata.title = "Append More Pattern";
  
  var currentOffset = 0;
  var limit = 3;
  
  function loadItems(offset) {
    // Load items from sample database
    for (var i = 0; i < limit; i++) {
      var video = getVideo(offset + i);
      page.appendItem(video.url, "video", {
        title: video.title + " (Item " + (offset + i + 1) + ")"
      });
    }
    return true; // always has more in demo
  }
  
  // Load first batch
  loadItems(currentOffset);
  currentOffset += limit;
  
  // A row that runs a function is `page.appendAction(title, func)`
  // (page.js:299-315) -- it creates an item of type "action" and calls the
  // function on Activate.
  //
  // An `Item` has NO `onSelect`. The earlier version of this example assigned
  // one, which is silently accepted (the item object is `any` to a type
  // checker, and a plain property assignment is legal JavaScript) and then
  // never called: "Load More" did nothing. Nothing in the corpus of nine real
  // plugins uses `onSelect`; it exists only in examples. What an item CAN
  // carry is a URL — that is how a row navigates — and `Item.onEvent(type, cb)`
  // is the low-level hook underneath.
  function appendMore() {
    page.appendAction("Load More...", function() {
      loadItems(currentOffset);
      currentOffset += limit;
      if (currentOffset < 100) {
        appendMore();
      }
    });
  }
  appendMore();
  
  page.loading = false;
});

// Pattern 2: Numbered pages
new page.Route("example:paginate:pages:(.*)", function(page, pageNum) {
  pageNum = parseInt(pageNum) || 1;
  var itemsPerPage = 2;
  
  page.type = "directory";
  page.metadata.title = "Page " + pageNum;
  
  // Calculate range
  var start = (pageNum - 1) * itemsPerPage;
  var end = start + itemsPerPage;
  
  // Load current page items
  for (var i = start; i < end; i++) {
    var video = getVideo(i);
    page.appendItem(video.url, "video", {
      title: video.title + " (Item " + (i + 1) + ")"
    });
  }
  
  // Navigation
  if (pageNum > 1) {
    page.appendItem("example:paginate:pages:" + (pageNum - 1), "directory", {
      title: "← Previous Page"
    });
  }
  
  // Show page numbers (simplified)
  page.appendPassiveItem("label", {
    title: "Page " + pageNum + " of unlimited"
  });
  
  page.appendItem("example:paginate:pages:" + (pageNum + 1), "directory", {
    title: "Next Page →"
  });
  
  page.loading = false;
});

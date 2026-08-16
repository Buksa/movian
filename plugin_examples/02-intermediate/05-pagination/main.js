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
  
  // Add "More" entry
  page.appendItem("", "directory", {
    title: "Load More...",
    description: "Click to load next " + limit + " items"
  }).onSelect = function() {
    // Remove the "More" item and load next batch
    page.flush(); // Or use smarter removal
    loadItems(currentOffset);
    currentOffset += limit;
    
    // Re-add "More" button if we want
    if (currentOffset < 100) { // arbitrary limit for demo
      page.appendItem("", "directory", {
        title: "Load More...",
        description: "Loaded " + currentOffset + " items so far"
      }).onSelect = arguments.callee; // recursive reference
    }
  };
  
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

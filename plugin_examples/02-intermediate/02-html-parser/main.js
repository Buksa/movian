/**
 * HTML Parser Example
 * 
 * Scrapes video links from HTML pages.
 * Uses movian/html module (Gumbo parser).
 * 
 * NOTE: This example uses MOCK HTML for demonstration.
 * In production, fetch HTML with: http.request(url)
 */

var page = require('movian/page');
var html = require('movian/html');
var service = require('movian/service');

// MOCK: Sample HTML structure (like from a real video site)
var mockHtml = `
<!DOCTYPE html>
<html>
<head><title>Video Gallery</title></head>
<body>
  <div class="video-item">
    <a href="http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4">
      <h2>Big Buck Bunny</h2>
    </a>
    <img class="thumb" src="http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/images/BigBuckBunny.jpg">
    <p class="desc">Open source animated film</p>
  </div>
  
  <div class="video-item">
    <a href="http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ElephantsDream.mp4">
      <h2>Elephants Dream</h2>
    </a>
    <img class="thumb" src="http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/images/ElephantsDream.jpg">
    <p class="desc">World's first open movie</p>
  </div>
  
  <div class="video-item">
    <a href="http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/Sintel.mp4">
      <h2>Sintel</h2>
    </a>
    <img class="thumb" src="http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/images/Sintel.jpg">
    <p class="desc">Open source fantasy animation</p>
  </div>
</body>
</html>
`;

// Parse videos from HTML
new page.Route("example:scrape:", function(page) {
  page.type = "directory";
  page.metadata.title = "Scraped Content";
  
  try {
    // MOCK: In production, fetch HTML from real URL:
    // var response = http.request("http://example.com/videos");
    // var doc = html.parse(response.toString());
    
    // Parse mock HTML
    var doc = html.parse(mockHtml);
    
    // Find all video items by class name
    var items = doc.root.getElementsByClassName("video-item");
    
    items.forEach(function(item) {
      // Extract data using various selectors
      var link = item.getElementsByTagName("a")[0];
      var titleEl = item.getElementsByTagName("h2")[0];
      var thumbEl = item.getElementsByClassName("thumb")[0];
      var descEl = item.getElementsByClassName("desc")[0];
      
      if (link && titleEl) {
        var url = link.getAttribute("href");
        var title = titleEl.textContent.trim();
        var thumb = thumbEl ? thumbEl.getAttribute("src") : null;
        var desc = descEl ? descEl.textContent.trim() : null;
        
        page.appendItem(url, "video", {
          title: title,
          icon: thumb,
          description: desc
        });
      }
    });
    
    // Alternative: Find all video links by extension
    var allLinks = doc.root.getElementsByTagName("a");
    var foundVideos = 0;
    
    allLinks.forEach(function(link) {
      var href = link.getAttribute("href");
      var text = link.textContent.trim();
      
      // Filter for video file extensions
      if (href && href.match(/\.(mp4|mkv|avi|mov|webm)$/i)) {
        // Only add if not already added from class selector above
        foundVideos++;
      }
    });
    
    if (foundVideos === 0) {
      page.appendPassiveItem("label", { 
        title: "No video items found by extension filter" 
      });
    }
    
  } catch (err) {
    page.error("Parse error: " + err.message);
  }
  
  page.loading = false;
});

// Example with direct link parsing (no container class)
new page.Route("example:scrape:links", function(page) {
  page.type = "directory";
  page.metadata.title = "Direct Links";
  
  // MOCK: Simple HTML with direct video links
  var simpleHtml = `
    <a href="http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/TearsOfSteel.mp4">
      Tears of Steel
    </a>
    <a href="http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/VolkswagenGTIReview.mp4">
      Car Review
    </a>
  `;
  
  try {
    var doc = html.parse(simpleHtml);
    var links = doc.root.getElementsByTagName("a");
    
    links.forEach(function(link) {
      var href = link.getAttribute("href");
      var title = link.textContent.trim();
      
      if (href && href.match(/\.mp4$/i)) {
        page.appendItem(href, "video", {
          title: title || "Video"
        });
      }
    });
    
  } catch (err) {
    page.error("Parse error: " + err.message);
  }
  
  page.loading = false;
});

service.create("HTML Scraper", "example:scrape:", "video", true);

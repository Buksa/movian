/**
 * HTTP JSON API Example
 * 
 * Fetches data from JSON API and displays as video items.
 * Uses http.request() for API calls.
 * 
 * Principles: DRY - Don't Repeat Yourself
 * 
 * NOTE: This example uses MOCK data for demonstration.
 * In production, replace mockApiCall() with real http.request()
 */

var page = require('movian/page');
var http = require('movian/http');
var service = require('movian/service');

// MOCK: Simulates API response with sample video data
function mockApiCall(endpoint) {
  console.log("Mock API call to: " + endpoint);
  
  // Sample video data from public domain sources
  return {
    items: [
      {
        title: "Big Buck Bunny",
        url: "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4",
        thumbnail: "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/images/BigBuckBunny.jpg",
        description: "Open source animated short film"
      },
      {
        title: "Elephants Dream",
        url: "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ElephantsDream.mp4",
        thumbnail: "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/images/ElephantsDream.jpg",
        description: "World's first open source movie"
      },
      {
        title: "Sintel",
        url: "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/Sintel.mp4",
        thumbnail: "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/images/Sintel.jpg",
        description: "Open source fantasy animation"
      }
    ]
  };
}

// Helper function to avoid repetition
function fetchAndDisplay(page, apiUrl, itemBuilder) {
  try {
    // MOCK: Replace this with real HTTP request:
    // var response = http.request(apiUrl, {
    //   headers: { 
    //     "User-Agent": "MyPlugin/1.0",
    //     "Accept": "application/json"
    //   },
    //   timeout: 30
    // });
    // var data = JSON.parse(response.toString());
    
    // Using mock data for demonstration
    var data = mockApiCall(apiUrl);
    
    // Build items using provided function
    itemBuilder(page, data);
    
  } catch (err) {
    // Handle errors gracefully
    page.error("Failed to load data: " + err.message);
  }
  
  page.loading = false;
}

// Route using the helper
new page.Route("example:api:popular", function(page) {
  page.metadata.title = "Popular Videos";
  
  fetchAndDisplay(page, "/api/popular", 
    function(page, data) {
      (data.items || []).forEach(function(item) {
        page.appendItem(item.url, "video", {
          title: item.title,
          icon: item.thumbnail,
          description: item.description
        });
      });
    }
  );
});

// Another route using same helper
new page.Route("example:api:search:(.*)", function(page, query) {
  page.metadata.title = "Search: " + query;
  
  // MOCK: Filter mock data by query
  fetchAndDisplay(page, "/api/search?q=" + encodeURIComponent(query),
    function(page, data) {
      // Filter items that match the query
      var filtered = data.items.filter(function(item) {
        return item.title.toLowerCase().indexOf(query.toLowerCase()) !== -1;
      });
      
      if (filtered.length === 0) {
        page.appendPassiveItem("label", { 
          title: "No results for '" + query + "'" 
        });
      } else {
        filtered.forEach(function(item) {
          page.appendItem(item.url, "video", {
            title: item.title,
            icon: item.thumbnail,
            description: item.description
          });
        });
      }
    }
  );
});

service.create("HTTP API Demo", "example:api:popular", "video", true);

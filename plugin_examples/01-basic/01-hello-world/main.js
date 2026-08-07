/**
 * Hello World - Minimal Movian Plugin Example (API v2)
 * 
 * This is the simplest possible plugin. It creates:
 * 1. A service (appears on home screen)
 * 2. A route (handles URL navigation)
 * 
 * Principles: KISS - Keep It Simple, Stupid
 */

// Load required modules
var page = require('movian/page');
var service = require('movian/service');

// Create a route that displays a simple page
new page.Route("example:hello:", function(page) {
  // Set page type and title
  page.type = "directory";
  page.metadata.title = "Hello World";
  
  // Add a passive item (just text)
  page.appendPassiveItem("label", { 
    title: "Your first Movian plugin works!" 
  });
  
  // Mark loading complete
  page.loading = false;
});

// Create service - appears on home screen
// Parameters: title, URL, category, enabled, icon
service.create("Hello World", "example:hello:", "other", true);

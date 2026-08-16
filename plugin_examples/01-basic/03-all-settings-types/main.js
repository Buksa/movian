/**
 * All Settings Types Demo
 * 
 * Shows every available setting type in API v2.
 * Settings persist between app restarts.
 */

var settings = require('movian/settings');

// Create global settings panel
// Parameters: pluginId, title, icon, description
var s = new settings.globalSettings(
  "examples.all-settings",
  "Settings Demo",
  null,
  "Demonstrates all setting types available in Movian API v2"
);

// Boolean - on/off toggle
s.createBool("enabled", "Enable Feature", true, function(v) {
  console.log("Feature enabled:", v);
});

// String - text input
s.createString("username", "Username", "guest", function(v) {
  console.log("Username:", v);
});

// Integer - number with range
s.createInt("volume", "Volume", 50, 0, 100, 1, "%", function(v) {
  console.log("Volume:", v);
});

// Multi-option - radio buttons / dropdown
s.createMultiOpt("quality", "Video Quality", [
  ["sd", "SD (480p)", true],
  ["hd", "HD (720p)", false],
  ["fhd", "Full HD (1080p)", false],
  ["4k", "4K UHD", false]
], function(v) {
  console.log("Quality:", v);
});

// Action - button that triggers code
s.createAction("test", "Test Connection", function() {
  console.log("Testing connection...");
  // Here you would test API connectivity
});

// Visual separator
s.createDivider("Advanced Options");

// Info panel - just displays text
s.createInfo("about", null, "This plugin demonstrates all setting types. Changes are saved automatically.");

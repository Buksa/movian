/**
 * Video Scrobbler Example (API v2)
 * 
 * Tracks video playback events:
 * - onstart: video started
 * - onstop: video stopped/finished  
 * - onpause: playback paused
 * - onresume: playback resumed
 */

var videoscrobbler = require('movian/videoscrobbler');
var store = require('movian/store');

// Storage for watch history
var history = store.create("scrobble_history");

// Create scrobbler instance
var scrobbler = new videoscrobbler.VideoScrobbler();

scrobbler.onstart = function(data, prop, origin) {
  // data contains video metadata
  console.log("▶️ Started: " + data.title);
  
  // Log to history
  history.last_watched = {
    title: data.title,
    url: data.url,
    started_at: Date.now()
  };
};

scrobbler.onstop = function(data, prop, origin) {
  // Calculate watch percentage
  var duration = data.duration || 0;
  var position = data.position || 0;
  var percent = duration > 0 ? (position / duration) * 100 : 0;
  
  console.log("⏹️ Stopped: " + data.title + " at " + percent.toFixed(1) + "%");
  
  // Mark as watched if > 90%
  if (percent > 90) {
    markAsWatched(data);
  }
};

scrobbler.onpause = function(data, prop, origin) {
  console.log("⏸️ Paused: " + data.title);
};

scrobbler.onresume = function(data, prop, origin) {
  console.log("▶️ Resumed: " + data.title);
};

// Helper function
function markAsWatched(video) {
  var watched = history.watched_list || [];
  
  // Add to watched, avoid duplicates
  var existing = watched.findIndex(function(w) {
    return w.url === video.url;
  });
  
  if (existing === -1) {
    watched.push({
      title: video.title,
      url: video.url,
      watched_at: Date.now()
    });
    
    history.watched_list = watched;
    console.log("✅ Marked as watched: " + video.title);
  }
}

// Cleanup on unload (optional)
// scrobbler.destroy();

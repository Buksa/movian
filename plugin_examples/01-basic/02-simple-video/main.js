/**
 * Simple Video Example
 * 
 * Demonstrates different item types:
 * - video: Video file/stream
 * - audio: Audio file/stream
 * - directory: Subdirectory
 * 
 * Principles: YAGNI - only what we need
 * 
 * NOTE: Uses real public domain sample videos that work for testing
 */

var page = require('movian/page');
var service = require('movian/service');

// Sample video URLs from Google's public test data
var SAMPLE_VIDEOS = [
  {
    url: "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4",
    title: "Big Buck Bunny",
    icon: "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/images/BigBuckBunny.jpg",
    desc: "Open source animated short film"
  },
  {
    url: "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ElephantsDream.mp4",
    title: "Elephants Dream",
    icon: "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/images/ElephantsDream.jpg",
    desc: "World's first open source movie"
  },
  {
    url: "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/Sintel.mp4",
    title: "Sintel",
    icon: "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/images/Sintel.jpg",
    desc: "Open source fantasy animation"
  }
];

new page.Route("example:video:", function(page) {
  page.type = "directory";
  page.metadata.title = "Video Examples";
  
  // Video item with full metadata
  page.appendItem(SAMPLE_VIDEOS[0].url, "video", {
    title: SAMPLE_VIDEOS[0].title,
    icon: SAMPLE_VIDEOS[0].icon,
    description: SAMPLE_VIDEOS[0].desc
  });
  
  // Audio item
  page.appendItem("http://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3", "audio", {
    title: "SoundHelix Song",
    artist: "T. Schürger"
  });
  
  // Directory that leads to another page
  page.appendItem("example:video:more", "directory", {
    title: "More Videos",
    description: "Click to see more sample videos"
  });
  
  page.loading = false;
});

// Sub-page with more videos
new page.Route("example:video:more", function(page) {
  page.type = "directory";
  page.metadata.title = "More Videos";
  
  // Add all sample videos
  SAMPLE_VIDEOS.forEach(function(video, index) {
    page.appendItem(video.url, "video", {
      title: video.title + " (Sample " + (index + 1) + ")",
      icon: video.icon,
      description: video.desc
    });
  });
  
  page.loading = false;
});

service.create("Simple Video", "example:video:", "video", true);

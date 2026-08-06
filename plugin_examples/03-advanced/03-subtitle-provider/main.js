/**
 * Subtitle Provider Example
 * 
 * Provides subtitles for videos.
 * Implements the subtitle provider interface.
 */

var subtitles = require('movian/subtitles');
var http = require('movian/http');

// Register subtitle provider
subtitles.addProvider(function(req) {
  // req contains:
  // - file: filename of video
  // - url: URL of video (if available)
  // - imdb: IMDB ID (if known)
  // - season, episode: for TV shows
  
  console.log("Searching subtitles for: " + req.file);
  
  // Search by filename hash or IMDB
  var query = req.imdb || req.file;
  var lang = req.lang || "eng"; // requested language
  
  // Simulate subtitle search
  // In real implementation, query subtitle API
  var searchUrl = "https://api.opensubtitles.org/search?q=" + encodeURIComponent(query) + "&lang=" + lang;
  
  try {
    var response = http.request(searchUrl, { timeout: 10 });
    var results = JSON.parse(response.toString());
    
    results.forEach(function(sub) {
      // Add subtitle to results
      req.addSubtitle(
        sub.url,           // download URL
        sub.title,          // subtitle name
        sub.lang,           // language code (eng, rus, etc)
        sub.format,         // srt, ass, etc
        "ExampleProvider",  // source name
        sub.score           // relevance score (0-100)
      );
    });
    
  } catch (err) {
    console.error("Subtitle search failed:", err);
  }
});

// Optional: show available languages
var availableLangs = subtitles.getLanguages();
console.log("Supported languages:", availableLangs);

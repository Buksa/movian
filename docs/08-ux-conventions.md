# UX Conventions

This chapter covers Movian's user experience conventions, UI guidelines, and best practices for creating consistent, intuitive plugin interfaces that integrate seamlessly with the Movian ecosystem.

## Table of Contents

1. [Design Principles](#design-principles)
2. [Navigation Paradigms](#navigation-paradigms)
3. [Page Layout and Structure](#page-layout-and-structure)
4. [Item Types and Usage](#item-types-and-usage)
5. [Property System Integration](#property-system-integration)
6. [Iconography and Visual Design](#iconography-and-visual-design)
7. [Localization and Internationalization](#localization-and-internationalization)
8. [Settings and Configuration](#settings-and-configuration)
9. [Responsive Design](#responsive-design)
10. [Accessibility Considerations](#accessibility-considerations)

## Design Principles

### Consistency First

Movian plugins should follow established patterns to provide a consistent user experience:

```javascript
// Good: Follows established patterns
plugin.addURI('myplugin:start', function(page) {
  page.type = 'directory';
  page.metadata.title = 'My Plugin';
  
  // Use standard item creation
  page.appendItem('http://example.com/video.mp4', 'video', {
    title: 'Sample Video',
    description: 'A sample video from my plugin'
  });
});

// Avoid: Custom navigation patterns
// Don't implement custom back buttons or navigation
```

### Progressive Disclosure

Show information progressively to avoid overwhelming users:

```javascript
// Good: Start with overview, drill down to details
plugin.addURI('myplugin:category:(.*)', function(page, category) {
  page.type = 'directory';
  page.metadata.title = category + ' Videos';
  
  // Show video list first
  videos.forEach(function(video) {
    page.appendItem(video.url, 'video', {
      title: video.title,
      description: video.shortDescription // Only short description here
    });
  });
});

// Detailed view when user selects item
plugin.addURI('myplugin:video:(.*)', function(page, videoId) {
  // Show full details, cast, reviews, etc.
});
```

### Performance Awareness

UI should feel responsive and fast:

```javascript
// Good: Async loading with feedback
page.appendItem('dummy', 'separator', {
  title: 'Loading videos...'
});

// Load asynchronously
loadVideos(function(videos) {
  page.entries = 0; // Clear loading message
  videos.forEach(function(video) {
    page.appendItem(video.url, 'video', {
      title: video.title
    });
  });
});

// Avoid: Synchronous loading that blocks UI
var videos = loadVideosSync(); // Blocks UI
```

## Navigation Paradigms

### Hierarchical Navigation

Movian uses a hierarchical navigation model:

```
Home → Services → Plugin → Category → Item → Details
```

```javascript
// Implement hierarchical navigation
(function(plugin) {
  var BASE = 'myplugin:';
  
  // Level 1: Service (appears on home screen)
  plugin.createService('My Plugin', BASE, 'video', true);
  
  // Level 2: Categories
  plugin.addURI(BASE, function(page) {
    page.type = 'directory';
    page.metadata.title = 'My Plugin';
    
    page.appendItem(BASE + 'movies', 'directory', {
      title: 'Movies',
      icon: 'movie'
    });
    
    page.appendItem(BASE + 'series', 'directory', {
      title: 'TV Series',
      icon: 'tv'
    });
  });
  
  // Level 3: Content lists
  plugin.addURI(BASE + 'movies', function(page) {
    page.type = 'directory';
    page.metadata.title = 'Movies';
    
    loadMovies(function(movies) {
      movies.forEach(function(movie) {
        page.appendItem(movie.url, 'video', {
          title: movie.title,
          year: movie.year
        });
      });
    });
  });
})(this);
```

### Breadcrumb Navigation

Movian automatically provides breadcrumb navigation. Maintain clear titles:

```javascript
// Good: Clear, hierarchical titles
plugin.addURI('myplugin:category:(.*)', function(page, category) {
  page.metadata.title = 'My Plugin > ' + category; // Shows navigation path
});

// Better: Use localized titles
var localized = require('movian/localization');
plugin.addURI('myplugin:category:(.*)', function(page, category) {
  page.metadata.title = localized.t('myplugin.title') + ' > ' + 
                        localized.t('category.' + category);
});
```

### Deep Linking

Support deep linking to specific content:

```javascript
// Support direct links to content
plugin.addURI('myplugin:video:(.*):(.*)', function(page, videoId, title) {
  // Direct access to specific video
  loadVideo(videoId, function(video) {
    page.appendItem(video.url, 'video', {
      title: video.title,
      description: video.description
    });
  });
});
```

## Page Layout and Structure

### Page Types

Movian supports different page types:

```javascript
// Directory page (list of items)
page.type = 'directory';

// Video page (video player)
page.type = 'video';

// Audio page (audio player)
page.type = 'audio';

// Search results
page.type = 'directory';
page.metadata.search = true;
```

### Standard Page Structure

```javascript
function createStandardPage(page, title) {
  page.type = 'directory';
  page.metadata.title = title;
  
  // Add loading indicator
  page.appendItem('dummy', 'separator', {
    title: 'Loading...'
  });
  
  return {
    loaded: false,
    items: [],
    
    addItem: function(url, type, metadata) {
      if (!this.loaded) {
        page.entries = 0; // Clear loading message
        this.loaded = true;
      }
      
      page.appendItem(url, type, metadata);
      this.items.push({url: url, type: type, metadata: metadata});
    },
    
    showError: function(message) {
      page.entries = 0;
      page.appendItem('dummy', 'video', {
        title: 'Error: ' + message,
        icon: 'error'
      });
    }
  };
}
```

### Metadata Standards

Use standard metadata fields:

```javascript
// Standard metadata fields
var metadata = {
  // Required
  title: 'Video Title',
  
  // Recommended
  description: 'Video description',
  year: 2024,
  duration: 7200, // seconds
  rating: 8.5,
  
  // Optional
  genre: ['Action', 'Adventure'],
  director: 'Director Name',
  cast: ['Actor 1', 'Actor 2'],
  icon: 'https://example.com/poster.jpg',
  thumbnail: 'https://example.com/thumb.jpg',
  
  // Plugin-specific
  source: 'My Plugin',
  quality: 'HD'
};
```

## Item Types and Usage

### Core Item Types

```javascript
// Video items - for playable video content
page.appendItem(url, 'video', {
  title: 'Movie Title',
  description: 'Movie description',
  year: 2024,
  duration: 7200
});

// Directory items - for navigation to other pages
page.appendItem('myplugin:category:movies', 'directory', {
  title: 'Movies',
  description: 'Browse movies',
  icon: 'movie'
});

// Audio items - for playable audio content
page.appendItem(url, 'audio', {
  title: 'Song Title',
  artist: 'Artist Name',
  album: 'Album Name',
  duration: 180
});

// Stream items - for live streams
page.appendItem(url, 'stream', {
  title: 'Live Stream',
  description: 'Live broadcast'
});

// Action items - for user actions
page.appendItem('dummy', 'action', {
  title: 'Refresh',
  callback: function() {
    // Refresh content
  }
});

// Separator items - for visual separation
page.appendItem('dummy', 'separator', {
  title: 'Section Title'
});
```

### Custom Item Types

Create custom item types for specific use cases:

```javascript
// Custom item type with enhanced metadata
page.appendItem(url, 'video', {
  title: 'Featured Video',
  description: 'A featured video from our collection',
  
  // Custom styling
  featured: true,
  
  // Custom actions
  actions: [
    {
      title: 'Add to Favorites',
      callback: function() {
        // Add to favorites
      }
    }
  ]
});
```

### Item Icons

Use appropriate icons for different content types:

```javascript
// Built-in icons
var icons = {
  movie: 'movie',
  tv: 'tv',
  music: 'music',
  search: 'search',
  settings: 'settings',
  favorites: 'star',
  download: 'download',
  refresh: 'refresh',
  error: 'error',
  warning: 'warning'
};

page.appendItem(url, 'directory', {
  title: 'Movies',
  icon: icons.movie
});
```

## Property System Integration

Movian's property system enables reactive UI updates. The property handling is implemented in `res/ecmascript/modules/movian/prop.js`:

```javascript
// From res/ecmascript/modules/movian/prop.js
var prop = require('movian/prop');

// Create reactive properties
var pageState = prop.createRoot();
pageState.loading = true;
pageState.items = [];

// Subscribe to property changes
var subscription = prop.subscribe(pageState, function(event, value) {
  console.log('Property changed:', event, value);
  
  if (event.path === 'loading') {
    if (value === false) {
      // Loading finished, update UI
      updatePage();
    }
  }
});

// Update properties
pageState.loading = false;
pageState.items.push(newItem);
```

### Property-Based UI Updates

```javascript
// Advanced property usage
function createReactivePage(page) {
  var state = prop.createRoot();
  state.loading = true;
  state.items = [];
  state.error = null;
  
  // Watch for changes
  prop.subscribe(state, function(event) {
    if (event.path === 'loading' && !event.value) {
      // Loading finished
      if (state.error) {
        showError(state.error);
      } else {
        displayItems(state.items);
      }
    }
  });
  
  return {
    setLoading: function(loading) {
      state.loading = loading;
    },
    
    setItems: function(items) {
      state.items = items;
      state.loading = false;
    },
    
    setError: function(error) {
      state.error = error;
      state.loading = false;
    }
  };
}
```

## Iconography and Visual Design

### Built-in Icons

Movian provides a set of built-in icons available through the GLW skin system. Icons are defined in the `glwskins/flat/icons/` directory:

```javascript
// Common icon usage
var standardIcons = {
  // Navigation
  home: 'home',
  back: 'back',
  search: 'search',
  
  // Content types
  movie: 'movie',
  tv: 'tv',
  music: 'music',
  video: 'video',
  audio: 'audio',
  
  // Actions
  play: 'play',
  pause: 'pause',
  stop: 'stop',
  refresh: 'refresh',
  download: 'download',
  favorite: 'favorite',
  settings: 'settings',
  
  // Status
  loading: 'loading',
  error: 'error',
  warning: 'warning',
  success: 'success'
};

// Usage in items
page.appendItem(url, 'directory', {
  title: 'Movies',
  icon: standardIcons.movie
});
```

### Custom Icons

Include custom icons in your plugin:

```javascript
// Custom icon from plugin assets
page.appendItem(url, 'directory', {
  title: 'My Content',
  icon: 'plugin://myplugin/assets/custom-icon.png'
});
```

### Visual Hierarchy

Use visual hierarchy to guide user attention:

```javascript
// Featured content
page.appendItem(featuredUrl, 'video', {
  title: 'Featured Movie',
  description: 'This week\'s featured movie',
  featured: true,
  icon: 'featured-banner'
});

// Regular content
movies.forEach(function(movie) {
  page.appendItem(movie.url, 'video', {
    title: movie.title,
    description: movie.description
  });
});

// Section separators
page.appendItem('dummy', 'separator', {
  title: 'Popular Movies'
});
```

## Localization and Internationalization

### String Localization

Movian supports localization through language files in the `lang/` directory. Each language has its own `.lang` file:

```javascript
// Use Movian's localization system
var _ = function(str) {
  // Movian provides _() function for localization
  return _(str); // Will be replaced with localized string
};

// In your plugin
plugin.addURI('myplugin:start', function(page) {
  page.type = 'directory';
  page.metadata.title = _('My Plugin'); // Will be localized
  
  page.appendItem(url, 'video', {
    title: _('Play Video'), // Localized title
    description: _('Click to play this video') // Localized description
  });
});
```

### Date and Number Formatting

```javascript
// Localized date formatting
function formatDate(date) {
  // Use Movian's built-in date formatting
  return new Date(date).toLocaleDateString();
}

// Localized number formatting
function formatNumber(num) {
  return num.toLocaleString();
}

// Usage
page.appendItem(url, 'video', {
  title: movie.title,
  year: movie.year,
  views: formatNumber(movie.views) + ' ' + _('views'),
  releaseDate: formatDate(movie.releaseDate)
});
```

### Right-to-Left Support

Consider RTL languages in your UI:

```javascript
// Check text direction
function isRTL() {
  // Movian provides direction info
  return Movian.locale && Movian.locale.direction === 'rtl';
}

// Adjust layout for RTL
function adjustLayoutForRTL() {
  if (isRTL()) {
    // Adjust margins, padding, text alignment
    page.style = page.style || {};
    page.style.direction = 'rtl';
  }
}
```

## Settings and Configuration

### Settings Integration

Use Movian's settings system for plugin configuration. Settings are handled by `res/ecmascript/modules/movian/settings.js`:

```javascript
// From res/ecmascript/modules/movian/settings.js
var settings = require('movian/settings');

// Create settings group
var pluginSettings = settings.createGroup('myplugin', {
  title: 'My Plugin Settings',
  description: 'Configure My Plugin behavior'
});

// Add settings
var qualitySetting = settings.createSetting(pluginSettings, 'string', 'quality', {
  title: 'Video Quality',
  type: 'select',
  options: [
    { value: 'auto', title: 'Auto' },
    { value: '360p', title: '360p' },
    { value: '720p', title: '720p' },
    { value: '1080p', title: '1080p' }
  ],
  default: 'auto'
});

var autoPlaySetting = settings.createSetting(pluginSettings, 'bool', 'autoPlay', {
  title: 'Auto-play next episode',
  default: true
});
```

### Settings Usage

```javascript
// Use settings in plugin logic
function getVideoUrl(video) {
  var quality = qualitySetting.value || 'auto';
  
  switch (quality) {
    case '360p':
      return video.url_360p;
    case '720p':
      return video.url_720p;
    case '1080p':
      return video.url_1080p;
    default:
      return video.url; // Auto quality
  }
}

// Apply auto-play setting
function playNextEpisode(currentEpisode) {
  if (autoPlaySetting.value) {
    // Find and play next episode
    var nextEpisode = findNextEpisode(currentEpisode);
    if (nextEpisode) {
      playVideo(nextEpisode);
    }
  }
}
```

### Settings Validation

```javascript
// Add validation to settings
var urlSetting = settings.createSetting(pluginSettings, 'string', 'apiUrl', {
  title: 'API URL',
  default: 'https://api.example.com',
  validator: function(value) {
    // Validate URL format
    try {
      new URL(value);
      return true;
    } catch (e) {
      return false;
    }
  },
  errorMessage: 'Please enter a valid URL'
});
```

## Responsive Design

### Screen Size Adaptation

Movian runs on various screen sizes. Adapt your UI accordingly:

```javascript
// Get screen information
var screenInfo = Movian.screen || {
  width: 1920,
  height: 1080,
  density: 1
};

// Adjust layout based on screen size
function getLayoutConfig() {
  var isSmall = screenInfo.width < 1280;
  var isMobile = screenInfo.width < 768;
  
  return {
    itemsPerPage: isMobile ? 10 : isSmall ? 20 : 30,
    showThumbnails: !isMobile,
    compactMode: isMobile
  };
}

// Use layout configuration
var config = getLayoutConfig();
videos.forEach(function(video, index) {
  var metadata = {
    title: video.title
  };
  
  if (config.showThumbnails) {
    metadata.icon = video.thumbnail;
  }
  
  if (config.compactMode) {
    metadata.description = null; // Save space
  }
  
  page.appendItem(video.url, 'video', metadata);
});
```

### Touch vs. Input Adaptation

```javascript
// Detect input method
var isTouch = Movian.input && Movian.input.type === 'touch';
var isRemote = Movian.input && Movian.input.type === 'remote';

// Adjust interaction patterns
if (isTouch) {
  // Touch-friendly interface
  page.appendItem(url, 'video', {
    title: video.title,
    // Larger touch targets
    height: 80
  });
} else if (isRemote) {
  // Remote control interface
  // Focus navigation is important
  page.appendItem(url, 'video', {
    title: video.title,
    // Clear focus indicators
    focusable: true
  });
}
```

## Accessibility Considerations

### Keyboard Navigation

Ensure keyboard navigation works properly:

```javascript
// Make sure all interactive items are focusable
page.appendItem(url, 'video', {
  title: video.title,
  // Ensure item can be focused with keyboard
  focusable: true,
  
  // Provide keyboard shortcuts
  shortcuts: {
    'enter': function() {
      playVideo(url);
    },
    'f': function() {
      addToFavorites(url);
    }
  }
});
```

### Screen Reader Support

```javascript
// Provide accessible labels
page.appendItem(url, 'video', {
  title: video.title,
  description: video.description,
  
  // Screen reader specific attributes
  accessibleLabel: video.title + ', ' + video.duration + ' minutes, ' + video.genre,
  accessibleRole: 'video'
});
```

### Color Contrast

```javascript
// Ensure sufficient color contrast
var themes = {
  default: {
    background: '#000000',
    text: '#ffffff',
    accent: '#0066cc'
  },
  highContrast: {
    background: '#000000',
    text: '#ffffff',
    accent: '#ffff00' // Higher contrast accent
  }
};

// Apply theme based on user preferences
var currentTheme = themes.default;
if (Movian.accessibility && Movian.accessibility.highContrast) {
  currentTheme = themes.highContrast;
}
```

## Performance and Optimization

### Lazy Loading

Implement lazy loading for better performance:

```javascript
// Lazy loading implementation
function createLazyLoader(page, loadFunction) {
  var loaded = false;
  var loading = false;
  
  function loadMore() {
    if (loading || loaded) return;
    
    loading = true;
    page.appendItem('dummy', 'separator', {
      title: 'Loading more...'
    });
    
    loadFunction(function(items) {
      loading = false;
      if (items.length === 0) {
        loaded = true;
        page.haveMore(false);
      } else {
        page.entries--; // Remove loading message
        items.forEach(function(item) {
          page.appendItem(item.url, item.type, item.metadata);
        });
        page.haveMore(true);
      }
    });
  }
  
  page.asyncPaginator = loadMore;
  return loadMore;
}
```

### Caching Strategies

```javascript
// Implement caching for better performance
var cache = {
  data: {},
  ttl: 5 * 60 * 1000, // 5 minutes
  
  get: function(key) {
    var item = this.data[key];
    if (item && Date.now() - item.timestamp < this.ttl) {
      return item.data;
    }
    return null;
  },
  
  set: function(key, data) {
    this.data[key] = {
      data: data,
      timestamp: Date.now()
    };
  }
};

// Use cache in API calls
function loadVideos(category, callback) {
  var cacheKey = 'videos:' + category;
  var cached = cache.get(cacheKey);
  
  if (cached) {
    callback(cached);
    return;
  }
  
  // Load from API
  api.getVideos(category, function(videos) {
    cache.set(cacheKey, videos);
    callback(videos);
  });
}
```

This comprehensive UX guide ensures your Movian plugins provide consistent, intuitive, and accessible user experiences that integrate seamlessly with the Movian ecosystem.
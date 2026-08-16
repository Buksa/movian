/**
 * OAuth Authentication Example
 * 
 * Demonstrates OAuth 2.0 flow with external API.
 * Stores access token securely.
 */

var page = require('movian/page');
var store = require('movian/store');
var settings = require('movian/settings');

// Storage for tokens
var authStore = store.create("oauth_tokens");
var ACCESS_TOKEN = authStore.access_token;

// Settings with auth action
var s = new settings.globalSettings("examples.oauth", "OAuth Demo", null, "OAuth 2.0 authentication flow");

s.createAction("auth", ACCESS_TOKEN ? "Re-authenticate" : "Authenticate with Service", function() {
  // Step 1: open the web flow so the user can authorize.
  var popup = require('native/popup');

  // The TRAP url. webpopup watches for the popup navigating here and closes
  // itself when it does -- that is how the flow hands control back.
  var redirectUri = "http://localhost:8080/callback";
  var clientId = "your-client-id";
  var authUrl = "https://api.example.com/oauth/authorize?client_id=" +
    clientId + "&redirect_uri=" + encodeURIComponent(redirectUri) +
    "&response_type=code";

  // `popup.webpopup(url, title, trap)` -- not `popup.web`, and NOT a
  // callback: es_misc.c:41-95 runs the flow and RETURNS the result. See
  // plugin_examples/webpopupplugin for the same idiom against a live service.
  var result = popup.webpopup(authUrl, "Authenticate", redirectUri);

  if (result.result !== 'trapped') {
    // 'userclose', 'neterror', 'error', or 'unsupported' on a build without
    // ENABLE_WEBPOPUP. There is no error callback to route this to.
    console.error("Authorization did not complete: " + result.result);
    return;
  }

  // Query arguments of the trapped URL arrive already split into `args`, so
  // the code does not have to be parsed back out of the URL by hand.
  var code = result.args.code;
  if (!code) {
    console.error("Callback carried no authorization code");
    return;
  }

  // Step 2: exchange the code for a token.
  exchangeCodeForToken(code);
});

// Helper: exchange code for access token
function exchangeCodeForToken(code) {
  var http = require('movian/http');
  
  var response = http.request("https://api.example.com/oauth/token", {
    method: "POST",
    args: {
      grant_type: "authorization_code",
      code: code,
      client_id: "your-client-id",
      client_secret: "your-client-secret",
      redirect_uri: "http://localhost:8080/callback"
    }
  });
  
  var data = JSON.parse(response.toString());
  ACCESS_TOKEN = data.access_token;
  authStore.access_token = ACCESS_TOKEN;
  authStore.refresh_token = data.refresh_token;
  
  console.log("Authenticated! Token saved.");
}

// Authenticated API request
function apiRequest(endpoint) {
  var http = require('movian/http');
  
  return http.request("https://api.example.com" + endpoint, {
    headers: {
      "Authorization": "Bearer " + ACCESS_TOKEN
    }
  });
}

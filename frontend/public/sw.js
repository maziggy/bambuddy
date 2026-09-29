// Bambuddy Service Worker
const CACHE_NAME = 'bambuddy-v32';
const STATIC_CACHE = 'bambuddy-static-v30';

// Static assets to cache on install
const STATIC_ASSETS = [
  '/',
  '/manifest.json',
  '/img/favicon.png',
  '/img/favicon-16x16.png',
  '/img/favicon-32x32.png',
  '/img/notification-badge.png',
  '/img/android-chrome-192x192.png',
  '/img/android-chrome-512x512.png',
  '/img/apple-touch-icon.png',
  '/img/bambuddy_logo_dark.png',
  // Self-hosted Inter font (#1460) - cached so the UI renders offline.
  '/fonts/inter-latin.woff2',
  '/fonts/inter-latin-ext.woff2',
];

// Install event - cache static assets
self.addEventListener('install', (event) => {
  console.log('[SW] Installing service worker...');
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => {
      console.log('[SW] Caching static assets');
      return cache.addAll(STATIC_ASSETS);
    })
  );
  // Activate immediately
  self.skipWaiting();
});

// Activate event - clean up old caches and claim existing clients.
//
// The forced reload that picks up a new bundle on already-open clients (the
// kiosk deploy-pickup scenario) lives in sw-register.js via a
// `controllerchange` listener, gated on whether the page already had a SW
// controller at load time. That gate distinguishes first-install (where a
// reload would race the in-flight React mount — observed on every fresh
// *.demo.bambuddy.cool subdomain, and in Firefox the activate's waitUntil
// hung on `client.navigate` until the document load was aborted with a
// Corrupted-Content error) from upgrade-on-existing-client (where the reload
// is wanted).
self.addEventListener('activate', (event) => {
  console.log('[SW] Activating service worker...');
  event.waitUntil(
    (async () => {
      const cacheNames = await caches.keys();
      await Promise.all(
        cacheNames
          .filter((name) => name !== CACHE_NAME && name !== STATIC_CACHE)
          .map((name) => {
            console.log('[SW] Deleting old cache:', name);
            return caches.delete(name);
          }),
      );
      await self.clients.claim();
    })(),
  );
});

// Fetch event - network-first for API, cache-first for static
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip non-GET requests
  if (request.method !== 'GET') {
    return;
  }

  // Skip cross-origin requests - let the browser handle them directly.
  // Without this the catch-all HTML branch below would answer a failed
  // cross-origin request with our cached index.html, so e.g. a blocked
  // Google Fonts request came back as text/html (#1460).
  if (url.origin !== self.location.origin) {
    return;
  }

  // Skip WebSocket connections
  if (url.protocol === 'ws:' || url.protocol === 'wss:') {
    return;
  }

  // Skip camera stream/snapshot requests - Safari has issues with streaming through SW
  if (url.pathname.includes('/camera/stream') || url.pathname.includes('/camera/snapshot')) {
    return;
  }

  // API requests - network first, no cache (real-time data is critical)
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(request).catch(() => {
        // Return offline response for API failures
        return new Response(
          JSON.stringify({ error: 'offline', message: 'You are currently offline' }),
          {
            status: 503,
            headers: { 'Content-Type': 'application/json' },
          }
        );
      })
    );
    return;
  }

  // Static assets - cache first, then network
  if (
    url.pathname.startsWith('/img/') ||
    url.pathname.startsWith('/icons/') ||
    url.pathname.startsWith('/fonts/') ||
    url.pathname.endsWith('.png') ||
    url.pathname.endsWith('.jpg') ||
    url.pathname.endsWith('.svg') ||
    url.pathname.endsWith('.ico') ||
    url.pathname.endsWith('.woff2')
  ) {
    event.respondWith(
      caches.match(request).then((cached) => {
        if (cached) {
          return cached;
        }
        return fetch(request).then((response) => {
          // Cache successful responses
          if (response.ok) {
            const clone = response.clone();
            caches.open(STATIC_CACHE).then((cache) => {
              cache.put(request, clone);
            });
          }
          return response;
        });
      })
    );
    return;
  }

  // JS/CSS assets - network first (Vite content-hashes filenames, so
  // cache-busting is built in; network-first ensures new builds load immediately)
  if (
    url.pathname.startsWith('/assets/') ||
    url.pathname.endsWith('.js') ||
    url.pathname.endsWith('.css')
  ) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(request, clone);
            });
          }
          return response;
        })
        .catch(() => {
          return caches.match(request);
        })
    );
    return;
  }

  // HTML pages - network first, fall back to cache
  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(request, clone);
          });
        }
        return response;
      })
      .catch(() => {
        return caches.match(request).then((cached) => {
          if (cached) {
            return cached;
          }
          // Return cached index for SPA navigation
          return caches.match('/');
        });
      })
  );
});

// Notifications can only open this application, never an external/action URL.
function pushTarget(value) {
  try {
    const url = new URL(typeof value === 'string' ? value : '/', self.location.origin);
    if (url.origin === self.location.origin && ['/', '/settings', '/archives', '/queue'].includes(url.pathname)) return url.href;
  } catch { /* Fall back to the application's home page. */ }
  return self.location.origin + '/';
}

// Handle visible notifications, including empty or malformed payloads.
self.addEventListener('push', (event) => {
  let data = {};
  try {
    const parsed = event.data?.json();
    if (parsed && typeof parsed === 'object') data = parsed;
  } catch { /* Still show a notification for userVisibleOnly subscriptions. */ }
  const options = {
    body: typeof data.body === 'string' ? data.body : 'New notification from Bambuddy',
    icon: '/img/android-chrome-192x192.png',
    // Android uses only the alpha mask for its small status-bar icon.
    badge: '/img/notification-badge.png',
    vibrate: [100, 50, 100],
    data: {
      url: pushTarget(data.url),
    },
  };

  event.waitUntil(
    self.registration.showNotification(typeof data.title === 'string' ? data.title : 'Bambuddy', options)
  );
});

// Handle notification clicks
self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  const url = pushTarget(event.notification.data?.url);

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).catch(() => []).then(async (windowClients) => {
      // Check if there's already a window open
      for (const client of windowClients) {
        if (new URL(client.url).origin === self.location.origin && 'focus' in client) {
          try {
            // Bring the app forward while the notification tap is still active.
            // Waiting for a background page to load first can prevent focus.
            await client.focus();
            if (client.url === url) return;
            const navigated = await client.navigate(url);
            if (navigated) return;
          } catch {
            // A suspended/closed client must not prevent the openWindow fallback.
          }
        }
      }
      // Open a new window if none exists
      if (clients.openWindow) {
        return clients.openWindow(url);
      }
    })
  );
});

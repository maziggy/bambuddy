// Bambuddy Service Worker
const CACHE_NAME = 'bambuddy-v31';
const STATIC_CACHE = 'bambuddy-static-v29';

// Static assets to cache on install
const STATIC_ASSETS = [
  '/',
  '/manifest.json',
  '/img/favicon.png',
  '/img/favicon-16x16.png',
  '/img/favicon-32x32.png',
  '/img/android-chrome-192x192.png',
  '/img/android-chrome-512x512.png',
  '/img/apple-touch-icon.png',
  '/img/bambuddy_logo_dark.png',
  // Self-hosted Inter font (#1460) - cached so the UI renders offline.
  '/fonts/inter-latin.woff2',
  '/fonts/inter-latin-ext.woff2',
];

// <name>-<8-char hash>.<ext> directly under /assets/, as Vite emits them.
const HASHED_ASSET_RE = /^\/assets\/[^/]+-[A-Za-z0-9_-]{8}\.[A-Za-z0-9]+$/;
const ENTRY_SCRIPT_RE = /\/assets\/index-[A-Za-z0-9_-]{8}\.js/;

// An update ships new file names, and the old files would stay cached for
// good. When a page names an entry script the cache has not seen, that is a
// new build: drop the cached /assets files, and the new ones are cached as
// they are used.
async function dropOldBuildAssets(response) {
  try {
    const html = await response.text();
    const entry = html.match(ENTRY_SCRIPT_RE);
    if (!entry) return;
    const cache = await caches.open(CACHE_NAME);
    // Listed first, so files the new build caches meanwhile are kept.
    const keys = await cache.keys();
    if (await cache.match(entry[0])) return;
    await Promise.all(
      keys
        .filter((key) => new URL(key.url).pathname.startsWith('/assets/'))
        .map((key) => cache.delete(key)),
    );
  } catch {
    // Pruning is housekeeping; never let it break a page load.
  }
}

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

  // The page polls /health to learn whether the server is back (#3175); an
  // answer from the cache would say it is when it is not.
  if (url.pathname === '/health') {
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

  // Files Vite names by content hash never change (#3175): serve them from
  // the cache, so an installed app opens without asking the server for the
  // bundle. The backend marks them immutable too.
  if (HASHED_ASSET_RE.test(url.pathname)) {
    event.respondWith(
      caches.match(request).then((cached) => {
        if (cached) {
          return cached;
        }
        return fetch(request).then((response) => {
          // Kept for good, so never keep a page a proxy sent for a missing file.
          const isHtml = (response.headers.get('content-type') || '').includes('text/html');
          if (response.ok && !isHtml) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(request, clone);
            });
          }
          return response;
        });
      })
    );
    return;
  }

  // Other JS/CSS (the pdf.js runtime data under /assets/pdfjs/, ...) - network
  // first, the cache only when offline.
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
          const pruning = dropOldBuildAssets(response.clone());
          try {
            event.waitUntil(pruning);
          } catch {
            // Too late to extend the event in this browser; pruning still runs.
          }
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

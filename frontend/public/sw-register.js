if ('serviceWorker' in navigator) {
  if (location.pathname.startsWith('/spoolbuddy')) {
    navigator.serviceWorker.getRegistrations().then(async (regs) => {
      const removed = await Promise.all(regs.map(async (r) => {
        // Unregistering also ends push subscriptions shared with the main app.
        // Preserve the registration if its subscription cannot be checked.
        try {
          if (r.pushManager && await r.pushManager.getSubscription()) return false;
          return await r.unregister();
        } catch {
          return false;
        }
      }));
      if (!removed.some(Boolean)) return;
      // Retained workers may still need these same-origin caches.
      if (removed.every(Boolean)) {
        const names = await caches.keys();
        await Promise.all(names.map((n) => caches.delete(n)));
      }
      location.reload();
    }).catch(() => {
      console.warn('SpoolBuddy service worker cleanup failed');
    });
  } else {
    // Capture controller state at script-load. Used to decide whether a
    // subsequent `controllerchange` is a deploy-pickup (had a prior SW →
    // reload so the new bundle takes over) or a first install (no prior SW →
    // skip the reload; the in-flight React mount would otherwise race the
    // forced navigation, leaving the page wedged on a spinner. The previous
    // approach — `client.navigate(client.url)` from the SW's activate
    // handler — exhibited that race in Chromium and a waitUntil hang in
    // Firefox, both surfaced on every fresh demo subdomain).
    const hadController = !!navigator.serviceWorker.controller;
    let reloading = false;
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (!hadController || reloading) return;
      reloading = true;
      location.reload();
    });
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js')
        .then((registration) => {
          console.log('SW registered:', registration.scope);
        })
        .catch((error) => {
          console.log('SW registration failed:', error);
        });
    });
  }
}

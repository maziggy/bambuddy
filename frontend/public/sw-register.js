if ('serviceWorker' in navigator) {
  if (location.pathname.startsWith('/spoolbuddy')) {
    navigator.serviceWorker.getRegistrations().then((regs) => {
      if (regs.length > 0) {
        Promise.all([
          ...regs.map((r) => r.unregister()),
          caches.keys().then((names) => Promise.all(names.map((n) => caches.delete(n)))),
        ]).then(() => location.reload());
      }
    });
  } else {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('sw.js')
        .then((registration) => {
          console.log('SW registered:', registration.scope);
        })
        .catch((error) => {
          console.log('SW registration failed:', error);
        });
    });
  }
}

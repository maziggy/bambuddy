export function supportsWebPush(): boolean {
  return window.isSecureContext && 'serviceWorker' in navigator &&
    'PushManager' in window && 'Notification' in window;
}

export async function prepareWebPush(): Promise<ServiceWorkerRegistration> {
  // Use the application's real service worker, not a second worker or scope.
  await navigator.serviceWorker.register('/sw.js');
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      navigator.serviceWorker.ready,
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error('Service worker unavailable')), 15000);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

export async function subscribeWebPush(registration: ServiceWorkerRegistration, publicKey: string) {
  const raw = atob(publicKey.replace(/-/g, '+').replace(/_/g, '/'));
  const key = Uint8Array.from(raw, (character) => character.charCodeAt(0));
  const existing = await registration.pushManager.getSubscription();
  if (existing) {
    const existingKey = existing.options.applicationServerKey;
    if (!existingKey || !key.every((byte, i) => byte === new Uint8Array(existingKey)[i]) ||
        existingKey.byteLength !== key.byteLength) {
      // Do not silently invalidate another provider using this subscription.
      throw new Error('pushKeyChanged');
    }
    return existing.toJSON();
  }
  return (await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: key,
  })).toJSON();
}

import { afterEach, describe, expect, it, vi } from 'vitest';
import { prepareWebPush, subscribeWebPush, supportsWebPush } from '../../utils/webPush';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('Web Push browser helpers', () => {
  it('rejects insecure or unsupported contexts', () => {
    vi.stubGlobal('isSecureContext', false);
    expect(supportsWebPush()).toBe(false);
    vi.stubGlobal('isSecureContext', true);
    vi.stubGlobal('PushManager', class {});
    vi.stubGlobal('Notification', class {});
    vi.stubGlobal('navigator', { serviceWorker: {} });
    expect(supportsWebPush()).toBe(true);
  });

  it('registers the real app worker and returns its ready registration', async () => {
    const registration = {};
    const register = vi.fn().mockResolvedValue(registration);
    vi.stubGlobal('navigator', { serviceWorker: { register, ready: Promise.resolve(registration) } });
    expect(await prepareWebPush()).toBe(registration);
    expect(register).toHaveBeenCalledWith('/sw.js');
  });

  it('times out rather than leaving enrollment stuck waiting for an active worker', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('navigator', { serviceWorker: { register: vi.fn().mockResolvedValue({}), ready: new Promise(() => {}) } });
    const result = expect(prepareWebPush()).rejects.toThrow('Service worker unavailable');
    await vi.advanceTimersByTimeAsync(15000);
    await result;
  });

  it('reuses a matching device subscription without subscribing again', async () => {
    const subscription = { endpoint: 'https://fcm.googleapis.com/example' };
    const subscribe = vi.fn();
    const registration = { pushManager: { subscribe, getSubscription: vi.fn().mockResolvedValue({
      options: { applicationServerKey: new Uint8Array([1, 2, 3]).buffer }, toJSON: () => subscription,
    }) } } as unknown as ServiceWorkerRegistration;
    expect(await subscribeWebPush(registration, 'AQID')).toEqual(subscription);
    expect(subscribe).not.toHaveBeenCalled();
  });

  it.each([null, new Uint8Array([1, 2]).buffer, new Uint8Array([3, 2, 1]).buffer])(
    'does not silently invalidate an existing subscription with another key', async (key) => {
      const unsubscribe = vi.fn();
      const subscribe = vi.fn();
      const registration = { pushManager: { subscribe, getSubscription: vi.fn().mockResolvedValue({
        options: { applicationServerKey: key }, unsubscribe,
      }) } } as unknown as ServiceWorkerRegistration;
      await expect(subscribeWebPush(registration, 'AQID')).rejects.toThrow('pushKeyChanged');
      expect(unsubscribe).not.toHaveBeenCalled();
      expect(subscribe).not.toHaveBeenCalled();
    },
  );

  it('creates a visible-only subscription with the decoded application key', async () => {
    const subscription = { endpoint: 'https://fcm.googleapis.com/example' };
    const subscribe = vi.fn().mockResolvedValue({ toJSON: () => subscription });
    const registration = { pushManager: { subscribe, getSubscription: vi.fn().mockResolvedValue(null) } } as unknown as ServiceWorkerRegistration;
    expect(await subscribeWebPush(registration, 'AQID')).toEqual(subscription);
    expect(subscribe).toHaveBeenCalledWith({ userVisibleOnly: true, applicationServerKey: new Uint8Array([1, 2, 3]) });
  });
});

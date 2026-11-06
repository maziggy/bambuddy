/**
 * How a camera stream opens: a separate browser window, or a floating overlay
 * on the page you are already on.
 *
 * This used to be one global switch in Settings > General > Camera. It is now
 * chosen per click from the camera button's own menu, and the stored setting
 * survives only as the default a browser starts from.
 */
export type CameraViewMode = 'window' | 'embedded';

/** The mode the last camera button click chose, in this browser. */
const CAMERA_VIEW_MODE_KEY = 'cameraViewMode';

/** Geometry the camera popup was last left at, written by CameraPage. */
const CAMERA_WINDOW_STATE_KEY = 'cameraWindowState';

export function isCameraViewMode(value: unknown): value is CameraViewMode {
  return value === 'window' || value === 'embedded';
}

/**
 * The local choice, or null if this browser has never made one.
 *
 * Null rather than a default so the caller can fall back to the server-side
 * setting: that is what makes a fresh browser open the same way the last one
 * did, without the local choice ever being overwritten by it.
 */
export function readStoredCameraViewMode(): CameraViewMode | null {
  try {
    const saved = localStorage.getItem(CAMERA_VIEW_MODE_KEY);
    return isCameraViewMode(saved) ? saved : null;
  } catch {
    // Private-mode Safari and friends. A camera that opens the default way is
    // a better outcome than a page that throws on render.
    return null;
  }
}

export function storeCameraViewMode(mode: CameraViewMode): void {
  try {
    localStorage.setItem(CAMERA_VIEW_MODE_KEY, mode);
  } catch {
    // Choice applies to this session regardless; only persistence is lost.
  }
}

/**
 * True in a web app started from the iOS home screen ("Add to Home Screen").
 *
 * There is no second window to open there: iOS loads a `window.open` URL into
 * the app itself, full screen, and the camera page it showed had no way back
 * to the rest of Bambuddy (#3419). Cameras open as the overlay instead.
 *
 * iOS only, deliberately not `display-mode: standalone`: Bambuddy installed as
 * an app on a desktop opens a real popup window, and Android a closable
 * in-app browser, so their window mode works and stays.
 */
export function isStandaloneWebApp(): boolean {
  try {
    return (navigator as Navigator & { standalone?: boolean }).standalone === true;
  } catch {
    return false;
  }
}

/**
 * Open a printer's camera in its own browser window, reusing whatever size and
 * position the user last left one at.
 *
 * Deliberately not `noopener`: the popup is same-origin and needs `opener` set
 * so the browser copies sessionStorage -- which is where the auth token lives
 * -- into the new window. Without it the camera page loads unauthenticated.
 */
export function openCameraWindow(printerId: number): boolean {
  let state: { width?: number; height?: number; left?: number; top?: number } = {
    width: 640,
    height: 400,
  };
  try {
    const saved = localStorage.getItem(CAMERA_WINDOW_STATE_KEY);
    if (saved) {
      const parsed = JSON.parse(saved) as typeof state;
      if (parsed && typeof parsed === 'object') state = parsed;
    }
  } catch {
    // Corrupt or unreadable geometry falls back to the defaults above rather
    // than leaving the camera unopenable.
  }

  const features = [
    `width=${state.width ?? 640}`,
    `height=${state.height ?? 400}`,
    state.left !== undefined ? `left=${state.left}` : '',
    state.top !== undefined ? `top=${state.top}` : '',
    'menubar=no,toolbar=no,location=no,status=no',
  ]
    .filter(Boolean)
    .join(',');

  // False when a popup blocker stopped it, so a caller closing the overlay it
  // came from can leave the camera where it was.
  return window.open(`/camera/${printerId}`, `camera-${printerId}`, features) !== null;
}

/**
 * Moving a camera window back into a Bambuddy tab as an overlay (#2737).
 *
 * The camera window asks over a BroadcastChannel, every tab showing the app
 * answers, and exactly one of them is told to open the overlay: a window can
 * be open beside several tabs, and the camera should not appear in each.
 * The tab the window was opened from is preferred. Its id is in
 * sessionStorage, which the browser copies into the popup when it opens.
 */
const CAMERA_CHANNEL = 'bambuddy-camera';
const TAB_ID_KEY = 'bambuddyTabId';

type PopInMessage =
  | { type: 'pop-in-offer'; requestId: string; printerId: number; printerName: string }
  | { type: 'pop-in-candidate'; requestId: string; tabId: string; instanceId: string }
  | { type: 'pop-in-claim'; requestId: string; instanceId: string; deadline: number }
  | { type: 'pop-in-done'; requestId: string }
  | { type: 'pop-in-refused'; requestId: string; instanceId: string };

function randomId(prefix: string): string {
  // Math.random: crypto.randomUUID needs a secure context (see useCameraViewerId).
  return `${prefix}${Math.random().toString(36).slice(2, 12)}`;
}

/**
 * This tab's id, kept in sessionStorage so a popup it opens inherits it. It
 * survives a reload or a sign-in again, so a camera window opened before
 * still finds the tab it came from.
 */
export function registerCameraTab(): string {
  try {
    const existing = sessionStorage.getItem(TAB_ID_KEY);
    if (existing) return existing;
    const tabId = randomId('t');
    sessionStorage.setItem(TAB_ID_KEY, tabId);
    return tabId;
  } catch {
    // Without it the window still pops in, just not necessarily to this tab.
    return randomId('t');
  }
}

/** The tab this camera window was opened from, if it was. */
export function readOpenerTabId(): string | null {
  try {
    return sessionStorage.getItem(TAB_ID_KEY);
  } catch {
    return null;
  }
}

/**
 * Answer camera windows asking to move into a tab; `open` shows the overlay.
 * Returns the cleanup.
 *
 * A duplicated tab starts with a copy of sessionStorage and so the same tab
 * id; the claim names one listener (`instanceId`), so only one of them opens.
 * `canShow` is asked before saying done: each tab has its own sign-in, and a
 * tab whose user cannot see the printer would drop the overlay right after
 * the window had closed. It refuses instead, and the window asks the next.
 */
export function listenForCameraPopIn(
  tabId: string,
  open: (printerId: number, printerName: string) => void,
  canShow: (printerId: number) => Promise<boolean> = async () => true,
): () => void {
  if (typeof BroadcastChannel === 'undefined') return () => {};
  const channel = new BroadcastChannel(CAMERA_CHANNEL);
  let closed = false;
  const instanceId = randomId('i');
  const offers = new Map<string, { printerId: number; printerName: string }>();
  channel.onmessage = async (event: MessageEvent<PopInMessage>) => {
    const msg = event.data;
    if (msg?.type === 'pop-in-offer' && Number.isInteger(msg.printerId) && msg.printerId > 0) {
      offers.set(msg.requestId, { printerId: msg.printerId, printerName: String(msg.printerName ?? '') });
      channel.postMessage({ type: 'pop-in-candidate', requestId: msg.requestId, tabId, instanceId } satisfies PopInMessage);
    } else if (msg?.type === 'pop-in-claim') {
      if (msg.instanceId !== instanceId) return;
      const offer = offers.get(msg.requestId);
      offers.delete(msg.requestId);
      if (!offer) return;
      let allowed = false;
      try {
        allowed = await canShow(offer.printerId);
      } catch {
        allowed = false;
      }
      if (closed) return;
      if (!allowed) {
        channel.postMessage({ type: 'pop-in-refused', requestId: msg.requestId, instanceId } satisfies PopInMessage);
        return;
      }
      // A claim that reaches a throttled background tab after the window
      // gave up would show the camera twice: the window kept its own.
      if (!(Date.now() <= msg.deadline)) return;
      open(offer.printerId, offer.printerName);
      channel.postMessage({ type: 'pop-in-done', requestId: msg.requestId } satisfies PopInMessage);
    } else if (msg?.type === 'pop-in-done') {
      // Another tab took it; an offer this one was not claimed for is over.
      offers.delete(msg.requestId);
    }
  };
  return () => {
    closed = true;
    channel.close();
  };
}

/**
 * Ask a Bambuddy tab to take this camera as an overlay. Resolves true once one
 * has opened it, false when no tab answered (none open, or all signed out),
 * none could show the printer, or the claimed one did not open it in time.
 */
export function requestCameraPopIn(
  printerId: number,
  printerName: string,
  preferredTabId: string | null,
  { answerMs = 400, doneMs = 3000 }: { answerMs?: number; doneMs?: number } = {},
): Promise<boolean> {
  if (typeof BroadcastChannel === 'undefined') return Promise.resolve(false);
  const channel = new BroadcastChannel(CAMERA_CHANNEL);
  const requestId = randomId('r');
  return new Promise<boolean>((resolve) => {
    const candidates: Array<{ tabId: string; instanceId: string }> = [];
    let claimedId: string | null = null;
    let timer: ReturnType<typeof setTimeout>;
    const finish = (ok: boolean) => {
      clearTimeout(timer);
      channel.close();
      resolve(ok);
    };
    const claimNext = () => {
      clearTimeout(timer);
      const target = candidates.find(c => c.tabId === preferredTabId) ?? candidates[0];
      if (!target) {
        finish(false);
        return;
      }
      candidates.splice(candidates.indexOf(target), 1);
      claimedId = target.instanceId;
      // The tab must open it before this window stops waiting; a margin
      // covers the done message's own trip back.
      const deadline = Date.now() + doneMs / 2;
      channel.postMessage({ type: 'pop-in-claim', requestId, instanceId: target.instanceId, deadline } satisfies PopInMessage);
      timer = setTimeout(() => finish(false), doneMs);
    };
    channel.onmessage = (event: MessageEvent<PopInMessage>) => {
      const msg = event.data;
      if (msg?.requestId !== requestId) return;
      if (msg.type === 'pop-in-candidate') {
        // Kept after a claim too: a tab that refuses hands on to the others.
        candidates.push({ tabId: msg.tabId, instanceId: msg.instanceId });
        // The tab it came from needs no waiting for the others.
        if (claimedId === null && preferredTabId && msg.tabId === preferredTabId) claimNext();
      } else if (msg.type === 'pop-in-refused' && msg.instanceId === claimedId) {
        claimNext();
      } else if (msg.type === 'pop-in-done') {
        finish(true);
      }
    };
    timer = setTimeout(claimNext, answerMs);
    channel.postMessage({ type: 'pop-in-offer', requestId, printerId, printerName } satisfies PopInMessage);
  });
}

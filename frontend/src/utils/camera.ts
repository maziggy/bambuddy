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
 * Open a printer's camera in its own browser window, reusing whatever size and
 * position the user last left one at.
 *
 * Deliberately not `noopener`: the popup is same-origin and needs `opener` set
 * so the browser copies sessionStorage -- which is where the auth token lives
 * -- into the new window. Without it the camera page loads unauthenticated.
 */
export function openCameraWindow(printerId: number): void {
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

  window.open(`/camera/${printerId}`, `camera-${printerId}`, features);
}

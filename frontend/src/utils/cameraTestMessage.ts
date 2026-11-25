import type { TFunction } from 'i18next';

interface CameraTestResult {
  resolution?: string;
  coalesced?: boolean;
  live_view?: boolean;
}

/**
 * The toast for a successful external camera test.
 *
 * A frame from the live view or from a capture already running is real, but
 * it was not fetched over a connection this test opened, so the message says
 * where it came from rather than implying the camera was just reached.
 * The strings put the resolution straight after the word, so it carries its
 * own leading space and brackets.
 */
export function cameraTestSuccessMessage(result: CameraTestResult, t: TFunction): string {
  const resolution = result.resolution ? ` (${result.resolution})` : '';
  if (result.live_view) return t('settings.toast.cameraConnectedLiveView', { resolution });
  if (result.coalesced) return t('settings.toast.cameraConnectedCoalesced', { resolution });
  return t('settings.toast.cameraConnected', { resolution });
}

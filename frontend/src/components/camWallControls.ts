// When a Cam Wall tile shows its controls (#3479): on hover (and focus), so the
// picture stays clear, or always. Touch screens have no hover and always show
// them. Stored per browser, like the wall's other settings.
export type CamWallControlsMode = 'hover' | 'always';

export const CAM_WALL_CONTROLS_KEY = 'camWallControls';

export function savedCamWallControlsMode(): CamWallControlsMode {
  return localStorage.getItem(CAM_WALL_CONTROLS_KEY) === 'always' ? 'always' : 'hover';
}

export function saveCamWallControlsMode(mode: CamWallControlsMode): void {
  localStorage.setItem(CAM_WALL_CONTROLS_KEY, mode);
}

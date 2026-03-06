export interface CameraState {
  x: number;
  y: number;
  width: number;
  height: number;
}

export const getDefaultState = (): CameraState => ({
  x: window.innerWidth - 420,
  y: 20,
  width: 400,
  height: 300,
});

// Cam Wall tile sizes (#2735): 1=S, 2=M, 3=L, 4=XL, the same scale as the
// printer cards. The size sets how many tiles share a row; tiles stay 16:9.
export const CAM_WALL_TILE_SIZES = ['s', 'm', 'l', 'xl'] as const;
export const DEFAULT_CAM_WALL_TILE_SIZE = 2;

export function camWallGridClasses(tileSize: number): string {
  switch (tileSize) {
    case 1: return 'grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5';
    case 3: return 'grid-cols-1 lg:grid-cols-2';
    case 4: return 'grid-cols-1';
    default: return 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4';
  }
}

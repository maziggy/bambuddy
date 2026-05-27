import { useSyncExternalStore } from 'react';
import { subscribeColorCatalog, getColorCatalogVersion } from '../utils/colors';

/**
 * Subscribe to color-catalog updates. Returns the current catalog version —
 * the value itself is opaque; what matters is that calling components re-render
 * when the catalog is (re)populated by ColorCatalogProvider.
 *
 * Use this in a high-level component (Layout) so that pages which cache color
 * names during render (via getColorName) refresh when the backend catalog
 * finishes loading after the first paint.
 */
export function useColorCatalogVersion(): number {
  return useSyncExternalStore(
    subscribeColorCatalog,
    getColorCatalogVersion,
    // SSR snapshot — we never SSR, but useSyncExternalStore requires the param.
    getColorCatalogVersion,
  );
}

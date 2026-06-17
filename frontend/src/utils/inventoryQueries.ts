import type { QueryClient } from '@tanstack/react-query';

/** React Query key for GET /inventory/locations (catalog + spool counts). */
export const inventoryLocationsQueryKey = ['inventory-locations'] as const;

export function invalidateInventoryLocations(queryClient: QueryClient) {
  return queryClient.invalidateQueries({ queryKey: inventoryLocationsQueryKey });
}

/** Refresh spool list and location counts after inventory mutations. */
export function invalidateSpoolAndLocationQueries(
  queryClient: QueryClient,
  spoolsQueryKey: readonly string[],
) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: [...spoolsQueryKey] }),
    invalidateInventoryLocations(queryClient),
  ]);
}

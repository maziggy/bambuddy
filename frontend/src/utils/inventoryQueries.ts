import type { QueryClient } from '@tanstack/react-query';

/** React Query key for GET /inventory/locations (catalog + spool counts). */
export const inventoryLocationsQueryKey = ['inventory-locations'] as const;

export function invalidateInventoryLocations(queryClient: QueryClient) {
  return queryClient.invalidateQueries({ queryKey: inventoryLocationsQueryKey });
}

/** React Query key for GET /inventory/suppliers (master list + spool counts). */
export const inventorySuppliersQueryKey = ['inventory-suppliers'] as const;

export function invalidateInventorySuppliers(queryClient: QueryClient) {
  return queryClient.invalidateQueries({ queryKey: inventorySuppliersQueryKey });
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

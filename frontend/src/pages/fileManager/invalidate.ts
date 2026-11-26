import type { QueryClient } from '@tanstack/react-query';

// What a deleted library file changes on screen: the listing, the folder
// counts, the storage stats and the trash badge. One list for every delete path
// (#3628) so a new one cannot forget a query.
export function invalidateAfterLibraryDelete(queryClient: QueryClient): void {
  queryClient.invalidateQueries({ queryKey: ['library-files'] });
  queryClient.invalidateQueries({ queryKey: ['library-folders'] });
  queryClient.invalidateQueries({ queryKey: ['library-stats'] });
  queryClient.invalidateQueries({ queryKey: ['library-trash-count'] });
}

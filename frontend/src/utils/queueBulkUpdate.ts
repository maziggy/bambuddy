/** The toast for a queue bulk edit, from what the backend updated and skipped. */
export function bulkUpdateToast(result: { updated_count: number; skipped_count: number }): {
  key: string;
  params: Record<string, number>;
  type: 'success' | 'warning' | 'error';
} {
  const params = { updated: result.updated_count, skipped: result.skipped_count };
  if (result.updated_count === 0) return { key: 'queue.toast.bulkUpdatedNone', params, type: 'error' };
  if (result.skipped_count > 0) return { key: 'queue.toast.bulkUpdatedSome', params, type: 'warning' };
  return { key: 'queue.toast.bulkUpdated', params, type: 'success' };
}

/**
 * A queue bulk edit said only "Updated N items" in a neutral toast, also when
 * it updated none of them.
 */
import { describe, it, expect } from 'vitest';
import { bulkUpdateToast } from '../../utils/queueBulkUpdate';

describe('bulkUpdateToast', () => {
  it('is an error when nothing was updated', () => {
    expect(bulkUpdateToast({ updated_count: 0, skipped_count: 3 })).toMatchObject({
      key: 'queue.toast.bulkUpdatedNone',
      type: 'error',
    });
  });

  it('is a warning when some were skipped', () => {
    expect(bulkUpdateToast({ updated_count: 2, skipped_count: 1 })).toEqual({
      key: 'queue.toast.bulkUpdatedSome',
      params: { updated: 2, skipped: 1 },
      type: 'warning',
    });
  });

  it('is a success when all were updated', () => {
    expect(bulkUpdateToast({ updated_count: 4, skipped_count: 0 }).type).toBe('success');
  });
});

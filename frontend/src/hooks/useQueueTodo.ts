import { useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { useAuth } from '../contexts/AuthContext';

export const QUEUE_TODO_KEY = ['queue-todo'] as const;

// What the queue waits on (#3615). No polling of its own: the WebSocket
// handler, the queue page's mutations and useQueueTodoAutoRefresh invalidate
// this key.
export function useQueueTodo() {
  const { hasAnyPermission } = useAuth();
  const canRead = hasAnyPermission('queue:read', 'queue:read_own', 'queue:read_all');
  return useQuery({
    queryKey: QUEUE_TODO_KEY,
    queryFn: api.getQueueTodo,
    enabled: canRead,
    staleTime: 5_000,
    refetchOnWindowFocus: true,
  });
}

// The queries that follow the queue as a whole: the layout's poll
// ['queue', 'pending'] and the queue page's list ['queue', printer, statuses]
// and history. Not the per-printer cards (['queue', id, 'pending'|'printing']),
// of which a farm has dozens polling on their own.
function isQueueListKey(key: readonly unknown[]): boolean {
  if (key[0] !== 'queue') return false;
  if (key.length === 2 && key[1] === 'pending') return true;
  if (key[1] === 'history') return true;
  // The page's list: ['queue', printer, statuses]. A card's key ends in a
  // single status of its own ('pending' / 'printing') or carries a model.
  return key.length === 3 && key[2] !== 'pending' && key[2] !== 'printing';
}

const REFETCH_DEBOUNCE_MS = 1500;

// Refetch the to-do list once the queue's list queries have refetched, so it
// stays as fresh as the queue without an interval of its own (#3615). A burst
// of refetches (the layout poll and the queue page together) coalesces into
// one trailing invalidation. Mount once, in the layout.
export function useQueueTodoAutoRefresh() {
  const queryClient = useQueryClient();
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const unsubscribe = queryClient.getQueryCache().subscribe((event) => {
      if (
        event.type === 'updated' &&
        event.action.type === 'success' &&
        isQueueListKey(event.query.queryKey)
      ) {
        clearTimeout(timer);
        timer = setTimeout(() => {
          queryClient.invalidateQueries({ queryKey: QUEUE_TODO_KEY });
        }, REFETCH_DEBOUNCE_MS);
      }
    });
    return () => {
      unsubscribe();
      clearTimeout(timer);
    };
  }, [queryClient]);
}

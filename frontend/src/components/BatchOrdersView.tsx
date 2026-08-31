import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Package, Layers, PlayCircle, XCircle, AlertTriangle, Clock, Coins } from 'lucide-react';
import { api } from '../api/client';
import type { PrintBatch, PrintBatchPlateProgress, Permission } from '../api/client';
import { Card } from './Card';
import { Button } from './Button';
import { ConfirmModal } from './ConfirmModal';
import { useToast } from '../contexts/ToastContext';
import { formatDuration, parseUTCDate } from '../utils/date';
import { getCurrencySymbol } from '../utils/currency';

type StatusFilter = 'active' | 'completed' | 'cancelled' | 'all';

interface BatchOrdersViewProps {
  hasPermission: (p: Permission) => boolean;
  t: (key: string, options?: Record<string, unknown>) => string;
}

/**
 * Batch orders tab (#342).
 *
 * An order lives longer than the queue it spawned: once its runs finish they
 * leave the active queue entirely, so the Queue and History tabs each hold
 * only half the picture. This is the one place that shows what was asked for
 * against what has actually been produced — including the runs that failed and
 * are therefore still owed.
 */
export function BatchOrdersView({ hasPermission, t }: BatchOrdersViewProps) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('active');
  const [cancelTarget, setCancelTarget] = useState<PrintBatch | null>(null);

  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: api.getSettings });
  const currency = getCurrencySymbol(settings?.currency || 'USD');

  const { data: batches, isLoading } = useQuery({
    queryKey: ['batches', statusFilter],
    queryFn: () => api.getBatches(statusFilter === 'all' ? undefined : statusFilter),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['batches'] });
    queryClient.invalidateQueries({ queryKey: ['queue'] });
  };

  const dispatchMutation = useMutation({
    mutationFn: ({ id, plateId }: { id: number; plateId?: number | null }) =>
      api.dispatchBatch(id, plateId !== undefined ? { plate_id: plateId, only_plate: true } : {}),
    onSuccess: (batch) => {
      invalidate();
      showToast(t('queue.batchOrders.dispatched', { name: batch.name }), 'success');
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const cancelMutation = useMutation({
    mutationFn: (id: number) => api.cancelBatch(id),
    onSuccess: () => {
      invalidate();
      setCancelTarget(null);
      showToast(t('queue.batchCancelled'), 'success');
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const canDispatch = hasPermission('queue:create' as Permission);
  const canCancel = hasPermission('queue:delete_all' as Permission);

  const filters: StatusFilter[] = ['active', 'completed', 'cancelled', 'all'];

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <Package className="w-5 h-5 text-cyan-700 dark:text-cyan-300" />
        <h2 className="text-base sm:text-lg font-semibold text-white">{t('queue.batchOrders.title')}</h2>
        <div className="flex gap-1 ml-auto">
          {filters.map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => setStatusFilter(value)}
              className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                statusFilter === value
                  ? 'border-bambu-green bg-bambu-green/10 text-bambu-green'
                  : 'border-bambu-dark-tertiary text-bambu-gray hover:border-bambu-gray'
              }`}
            >
              {t(`queue.batchOrders.filter.${value}`)}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div className="text-center py-12 text-bambu-gray">{t('common.loading')}</div>
      ) : !batches?.length ? (
        <Card className="p-12 text-center border-dashed">
          <Package className="w-16 h-16 text-bambu-gray mx-auto mb-4 opacity-50" />
          <h3 className="text-xl font-medium text-white mb-2">{t('queue.batchOrders.emptyTitle')}</h3>
          <p className="text-bambu-gray max-w-md mx-auto">{t('queue.batchOrders.emptyDescription')}</p>
        </Card>
      ) : (
        <div className="space-y-4">
          {batches.map((batch) => (
            <BatchOrderCard
              key={batch.id}
              batch={batch}
              currency={currency}
              canDispatch={canDispatch}
              canCancel={canCancel}
              isDispatching={dispatchMutation.isPending && dispatchMutation.variables?.id === batch.id}
              onDispatch={(plateId) => dispatchMutation.mutate({ id: batch.id, plateId })}
              onCancel={() => setCancelTarget(batch)}
              t={t}
            />
          ))}
        </div>
      )}

      {cancelTarget && (
        <ConfirmModal
          title={t('queue.cancelBatchConfirmTitle')}
          message={t('queue.cancelBatchConfirmMessage')}
          confirmText={t('queue.cancelBatch')}
          variant="warning"
          onConfirm={() => cancelMutation.mutate(cancelTarget.id)}
          onCancel={() => setCancelTarget(null)}
        />
      )}
    </div>
  );
}

const STATUS_STYLES: Record<string, string> = {
  active: 'bg-blue-100 dark:bg-blue-500/20 text-blue-700 dark:text-blue-300',
  completed: 'bg-green-100 dark:bg-green-500/20 text-green-700 dark:text-green-300',
  cancelled: 'bg-bambu-dark-tertiary text-bambu-gray',
};

function BatchOrderCard({
  batch,
  currency,
  canDispatch,
  canCancel,
  isDispatching,
  onDispatch,
  onCancel,
  t,
}: {
  batch: PrintBatch;
  currency: string;
  canDispatch: boolean;
  canCancel: boolean;
  isDispatching: boolean;
  onDispatch: (plateId?: number | null) => void;
  onCancel: () => void;
  t: (key: string, options?: Record<string, unknown>) => string;
}) {
  // Progress is measured against the target, not against what was queued —
  // that is the whole difference between an order and a grouping.
  const denominator = batch.has_targets ? batch.target_count : batch.completed_count + batch.pending_count
    + batch.printing_count + batch.failed_count;
  const percent = denominator > 0 ? Math.round((batch.completed_count / denominator) * 100) : 0;
  // Runs the order owes that nothing can produce any more: their plate's last
  // queue item was deleted, so there is no configuration left to clone (#2960).
  const strandedCount = batch.has_targets ? batch.remaining_count - batch.dispatchable_count : 0;
  const dueDate = batch.due_date ? parseUTCDate(batch.due_date) : null;
  const isOverdue = dueDate != null && batch.status === 'active' && dueDate.getTime() < Date.now();

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start gap-3 mb-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="text-white font-medium truncate">{batch.name}</p>
            <span className={`text-xs px-2 py-0.5 rounded-full ${STATUS_STYLES[batch.status] ?? STATUS_STYLES.cancelled}`}>
              {t(`queue.batchOrders.status.${batch.status}`)}
            </span>
            {!batch.has_targets && (
              <span
                className="text-xs px-2 py-0.5 rounded-full bg-bambu-dark-tertiary text-bambu-gray"
                title={t('queue.batchOrders.noTargetsHint')}
              >
                {t('queue.batchOrders.noTargets')}
              </span>
            )}
          </div>
          <p className="text-xs text-bambu-gray mt-1">
            {batch.created_by_username
              ? t('queue.addedBy', { name: batch.created_by_username })
              : null}
            {dueDate && (
              <span className={isOverdue ? 'text-orange-600 dark:text-orange-400' : ''}>
                {batch.created_by_username ? ' • ' : ''}
                {t('queue.batchOrders.due', { date: dueDate.toLocaleDateString() })}
              </span>
            )}
          </p>
          {batch.notes && <p className="text-xs text-bambu-gray mt-1 whitespace-pre-wrap">{batch.notes}</p>}
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          {batch.has_targets && batch.dispatchable_count > 0 && batch.status !== 'cancelled' && canDispatch && (
            <Button variant="primary" size="sm" onClick={() => onDispatch()} disabled={isDispatching}>
              <PlayCircle className="w-4 h-4 mr-1" />
              {t('queue.batchOrders.dispatchRemaining', { count: batch.dispatchable_count })}
            </Button>
          )}
          {/* Cancel is the only way to close an order out, so it must not be
              gated on there being pending items to cancel: an order whose runs
              were all deleted has none, and is exactly the one that needs
              closing (#2960). */}
          {batch.status === 'active' && canCancel && (
            <Button variant="ghost" size="sm" onClick={onCancel}>
              <XCircle className="w-4 h-4 mr-1" />
              {t('queue.cancelBatch')}
            </Button>
          )}
        </div>
      </div>

      <div className="flex items-center gap-3 mb-2">
        <div className="flex-1 h-2 bg-bambu-dark-tertiary rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${
              batch.status === 'completed' ? 'bg-bambu-green' : 'bg-blue-500'
            }`}
            style={{ width: `${Math.min(100, percent)}%` }}
          />
        </div>
        <span className="text-xs text-bambu-gray whitespace-nowrap tabular-nums">
          {t('queue.batchProgress', { completed: batch.completed_count, total: denominator })}
        </span>
      </div>

      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-bambu-gray mb-1">
        {batch.printing_count > 0 && <span>{t('queue.batchOrders.printing', { count: batch.printing_count })}</span>}
        {batch.pending_count > 0 && <span>{t('queue.batch.pendingCount', { count: batch.pending_count })}</span>}
        {batch.failed_count > 0 && (
          <span className="text-orange-600 dark:text-orange-400 flex items-center gap-1">
            <AlertTriangle className="w-3 h-3" />
            {t('queue.batchOrders.failed', { count: batch.failed_count })}
          </span>
        )}
        {batch.has_targets && batch.remaining_count > 0 && (
          <span>{t('queue.batchOrders.remaining', { count: batch.remaining_count })}</span>
        )}
        {batch.print_time_seconds > 0 && (
          <span className="flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {formatDuration(batch.print_time_seconds)}
          </span>
        )}
        {batch.actual_cost != null && (
          <span className="flex items-center gap-1">
            <Coins className="w-3 h-3" />
            <span>
              {t('queue.batchOrders.costSoFar', {
                amount: `${currency} ${batch.actual_cost.toFixed(2)}`,
              })}
            </span>
            {batch.estimated_remaining_cost != null && batch.estimated_remaining_cost > 0 && (
              <span>
                {t('queue.batchOrders.costRemaining', {
                  amount: `${currency} ${batch.estimated_remaining_cost.toFixed(2)}`,
                })}
              </span>
            )}
          </span>
        )}
      </div>

      {strandedCount > 0 && batch.status !== 'cancelled' && (
        <p className="flex items-start gap-1.5 text-xs text-orange-600 dark:text-orange-400 mb-1">
          <AlertTriangle className="w-3 h-3 mt-0.5 flex-shrink-0" />
          <span>{t('queue.batchOrders.strandedNotice', { runs: strandedCount, owed: batch.remaining_count })}</span>
        </p>
      )}

      {batch.has_targets && batch.plates.length > 0 && (
        <div className="mt-3 border-t border-bambu-dark-tertiary pt-3 space-y-1.5">
          {batch.plates.map((plate) => (
            <PlateRow
              key={`${plate.plate_id ?? 'file'}`}
              plate={plate}
              batchStatus={batch.status}
              currency={currency}
              canDispatch={canDispatch}
              isDispatching={isDispatching}
              onDispatch={() => onDispatch(plate.plate_id)}
              t={t}
            />
          ))}
        </div>
      )}
    </Card>
  );
}

function PlateRow({
  plate,
  batchStatus,
  currency,
  canDispatch,
  isDispatching,
  onDispatch,
  t,
}: {
  plate: PrintBatchPlateProgress;
  batchStatus: string;
  currency: string;
  canDispatch: boolean;
  isDispatching: boolean;
  onDispatch: () => void;
  t: (key: string, options?: Record<string, unknown>) => string;
}) {
  const label = plate.plate_name
    || (plate.plate_id != null ? t('queue.plateNumber', { index: plate.plate_id }) : t('queue.batchOrders.wholeFile'));

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
      <Layers className="w-3.5 h-3.5 text-bambu-gray flex-shrink-0" />
      <span className="text-white min-w-0 truncate">{label}</span>
      <span className="text-bambu-gray tabular-nums">
        {t('queue.batchOrders.plateProgress', {
          completed: plate.completed_count,
          target: plate.quantity_target,
        })}
      </span>
      {plate.failed_count > 0 && (
        <span className="text-orange-600 dark:text-orange-400">
          {t('queue.batchOrders.failed', { count: plate.failed_count })}
        </span>
      )}
      {plate.actual_cost != null && (
        <span className="text-bambu-gray tabular-nums">{`${currency} ${plate.actual_cost.toFixed(2)}`}</span>
      )}
      {plate.remaining > 0 && batchStatus !== 'cancelled' && (
        <span className="ml-auto flex items-center gap-2">
          <span className="text-bambu-gray">
            {t('queue.batchOrders.remaining', { count: plate.remaining })}
          </span>
          {!plate.can_dispatch ? (
            <span className="text-orange-600 dark:text-orange-400">
              {t('queue.batchOrders.strandedPlate')}
            </span>
          ) : (
            canDispatch && (
              <button
                type="button"
                onClick={onDispatch}
                disabled={isDispatching}
                className="text-bambu-green hover:underline disabled:opacity-50"
              >
                {t('queue.batchOrders.dispatchPlate')}
              </button>
            )
          )}
        </span>
      )}
    </div>
  );
}

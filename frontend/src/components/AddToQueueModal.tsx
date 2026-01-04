import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Calendar, Clock, X, AlertCircle, Power, Hand } from 'lucide-react';
import { api } from '../api/client';
import type { PrintQueueItemCreate } from '../api/client';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';

interface AddToQueueModalProps {
  archiveId: number;
  archiveName: string;
  onClose: () => void;
}

export function AddToQueueModal({ archiveId, archiveName, onClose }: AddToQueueModalProps) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [printerId, setPrinterId] = useState<number | null>(null);
  const [scheduleType, setScheduleType] = useState<'asap' | 'scheduled' | 'manual'>('asap');
  const [scheduledTime, setScheduledTime] = useState('');
  const [requirePreviousSuccess, setRequirePreviousSuccess] = useState(false);
  const [autoOffAfter, setAutoOffAfter] = useState(false);

  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: () => api.getPrinters(),
  });

  // Set default printer if only one available
  useEffect(() => {
    if (printers?.length === 1 && !printerId) {
      setPrinterId(printers[0].id);
    }
  }, [printers, printerId]);

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const addMutation = useMutation({
    mutationFn: (data: PrintQueueItemCreate) => api.addToQueue(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      showToast('Added to print queue');
      onClose();
    },
    onError: (error: Error) => {
      showToast(error.message || 'Failed to add to queue', 'error');
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!printerId) {
      showToast('Please select a printer', 'error');
      return;
    }

    const data: PrintQueueItemCreate = {
      printer_id: printerId,
      archive_id: archiveId,
      require_previous_success: requirePreviousSuccess,
      auto_off_after: autoOffAfter,
      manual_start: scheduleType === 'manual',
    };

    if (scheduleType === 'scheduled' && scheduledTime) {
      data.scheduled_time = new Date(scheduledTime).toISOString();
    }

    addMutation.mutate(data);
  };

  // Get minimum datetime (now + 1 minute)
  const getMinDateTime = () => {
    const now = new Date();
    now.setMinutes(now.getMinutes() + 1);
    return now.toISOString().slice(0, 16);
  };

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <Card className="w-full max-w-md" onClick={(e) => e.stopPropagation()}>
        <CardContent className="p-0">
          {/* Header */}
          <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
            <div className="flex items-center gap-2">
              <Calendar className="w-5 h-5 text-bambu-green" />
              <h2 className="text-xl font-semibold text-white">Schedule Print</h2>
            </div>
            <button
              onClick={onClose}
              className="text-bambu-gray hover:text-white transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Form */}
          <form onSubmit={handleSubmit} className="p-4 space-y-4">
            {/* Archive name */}
            <div>
              <label className="block text-sm text-bambu-gray mb-1">Print Job</label>
              <p className="text-white font-medium truncate">{archiveName}</p>
            </div>

            {/* Printer selection */}
            <div>
              <label className="block text-sm text-bambu-gray mb-1">Printer</label>
              {printers?.length === 0 ? (
                <div className="flex items-center gap-2 text-red-400 text-sm">
                  <AlertCircle className="w-4 h-4" />
                  No printers configured
                </div>
              ) : (
                <select
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                  value={printerId || ''}
                  onChange={(e) => setPrinterId(e.target.value ? Number(e.target.value) : null)}
                  required
                >
                  <option value="">Select printer...</option>
                  {printers?.map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              )}
            </div>

            {/* Schedule type */}
            <div>
              <label className="block text-sm text-bambu-gray mb-2">When to print</label>
              <div className="flex gap-2">
                <button
                  type="button"
                  className={`flex-1 px-2 py-2 rounded-lg border text-sm flex items-center justify-center gap-1.5 transition-colors ${
                    scheduleType === 'asap'
                      ? 'bg-bambu-green border-bambu-green text-white'
                      : 'bg-bambu-dark border-bambu-dark-tertiary text-bambu-gray hover:text-white'
                  }`}
                  onClick={() => setScheduleType('asap')}
                >
                  <Clock className="w-4 h-4" />
                  ASAP
                </button>
                <button
                  type="button"
                  className={`flex-1 px-2 py-2 rounded-lg border text-sm flex items-center justify-center gap-1.5 transition-colors ${
                    scheduleType === 'scheduled'
                      ? 'bg-bambu-green border-bambu-green text-white'
                      : 'bg-bambu-dark border-bambu-dark-tertiary text-bambu-gray hover:text-white'
                  }`}
                  onClick={() => setScheduleType('scheduled')}
                >
                  <Calendar className="w-4 h-4" />
                  Scheduled
                </button>
                <button
                  type="button"
                  className={`flex-1 px-2 py-2 rounded-lg border text-sm flex items-center justify-center gap-1.5 transition-colors ${
                    scheduleType === 'manual'
                      ? 'bg-bambu-green border-bambu-green text-white'
                      : 'bg-bambu-dark border-bambu-dark-tertiary text-bambu-gray hover:text-white'
                  }`}
                  onClick={() => setScheduleType('manual')}
                >
                  <Hand className="w-4 h-4" />
                  Queue Only
                </button>
              </div>
            </div>

            {/* Scheduled time input */}
            {scheduleType === 'scheduled' && (
              <div>
                <label className="block text-sm text-bambu-gray mb-1">Date & Time</label>
                <input
                  type="datetime-local"
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                  value={scheduledTime}
                  onChange={(e) => setScheduledTime(e.target.value)}
                  min={getMinDateTime()}
                  required
                />
              </div>
            )}

            {/* Require previous success */}
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="requirePrevious"
                checked={requirePreviousSuccess}
                onChange={(e) => setRequirePreviousSuccess(e.target.checked)}
                className="rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
              />
              <label htmlFor="requirePrevious" className="text-sm text-bambu-gray">
                Only start if previous print succeeded
              </label>
            </div>

            {/* Auto power off */}
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="autoOffAfter"
                checked={autoOffAfter}
                onChange={(e) => setAutoOffAfter(e.target.checked)}
                className="rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
              />
              <label htmlFor="autoOffAfter" className="text-sm text-bambu-gray flex items-center gap-1">
                <Power className="w-3.5 h-3.5" />
                Power off printer when done
              </label>
            </div>

            {/* Help text */}
            <p className="text-xs text-bambu-gray">
              {scheduleType === 'asap'
                ? 'Print will start as soon as the printer is idle.'
                : scheduleType === 'scheduled'
                ? 'Print will start at the scheduled time if the printer is idle. If busy, it will wait until the printer becomes available.'
                : 'Print will be staged but won\'t start automatically. Use the Start button to release it to the queue.'}
            </p>

            {/* Actions */}
            <div className="flex gap-3 pt-2">
              <Button type="button" variant="secondary" onClick={onClose} className="flex-1">
                Cancel
              </Button>
              <Button
                type="submit"
                className="flex-1"
                disabled={addMutation.isPending || !printerId || printers?.length === 0}
              >
                {addMutation.isPending ? 'Adding...' : 'Add to Queue'}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

import { useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { X, Printer, Loader2 } from 'lucide-react';
import { api } from '../api/client';
import { Card, CardContent } from './Card';
import { Button } from './Button';

interface ReprintModalProps {
  archiveId: number;
  archiveName: string;
  onClose: () => void;
  onSuccess: () => void;
}

export function ReprintModal({ archiveId, archiveName, onClose, onSuccess }: ReprintModalProps) {
  const [selectedPrinter, setSelectedPrinter] = useState<number | null>(null);

  const { data: printers, isLoading: loadingPrinters } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  const reprintMutation = useMutation({
    mutationFn: () => {
      if (!selectedPrinter) throw new Error('No printer selected');
      return api.reprintArchive(archiveId, selectedPrinter);
    },
    onSuccess: () => {
      onSuccess();
      onClose();
    },
  });

  const activePrinters = printers?.filter((p) => p.is_active) || [];

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-8">
      <Card className="w-full max-w-md">
        <CardContent>
          {/* Header */}
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-white">Re-print</h2>
            <Button variant="ghost" size="sm" onClick={onClose}>
              <X className="w-5 h-5" />
            </Button>
          </div>

          <p className="text-sm text-bambu-gray mb-4">
            Send <span className="text-white">{archiveName}</span> to a printer
          </p>

          {/* Printer selection */}
          {loadingPrinters ? (
            <div className="flex justify-center py-8">
              <Loader2 className="w-6 h-6 text-bambu-green animate-spin" />
            </div>
          ) : activePrinters.length === 0 ? (
            <div className="text-center py-8 text-bambu-gray">
              No active printers available
            </div>
          ) : (
            <div className="space-y-2 mb-6">
              {activePrinters.map((printer) => (
                <button
                  key={printer.id}
                  onClick={() => setSelectedPrinter(printer.id)}
                  className={`w-full flex items-center gap-3 p-3 rounded-lg border transition-colors ${
                    selectedPrinter === printer.id
                      ? 'border-bambu-green bg-bambu-green/10'
                      : 'border-bambu-dark-tertiary bg-bambu-dark hover:border-bambu-gray'
                  }`}
                >
                  <div
                    className={`p-2 rounded-lg ${
                      selectedPrinter === printer.id
                        ? 'bg-bambu-green/20'
                        : 'bg-bambu-dark-tertiary'
                    }`}
                  >
                    <Printer
                      className={`w-5 h-5 ${
                        selectedPrinter === printer.id
                          ? 'text-bambu-green'
                          : 'text-bambu-gray'
                      }`}
                    />
                  </div>
                  <div className="text-left">
                    <p className="text-white font-medium">{printer.name}</p>
                    <p className="text-xs text-bambu-gray">
                      {printer.model || 'Unknown model'} • {printer.ip_address}
                    </p>
                  </div>
                </button>
              ))}
            </div>
          )}

          {/* Error message */}
          {reprintMutation.isError && (
            <div className="mb-4 p-3 bg-red-500/20 border border-red-500/50 rounded-lg text-sm text-red-400">
              {(reprintMutation.error as Error).message || 'Failed to start print'}
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-3">
            <Button variant="secondary" onClick={onClose} className="flex-1">
              Cancel
            </Button>
            <Button
              onClick={() => reprintMutation.mutate()}
              disabled={!selectedPrinter || reprintMutation.isPending}
              className="flex-1"
            >
              {reprintMutation.isPending ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Sending...
                </>
              ) : (
                <>
                  <Printer className="w-4 h-4" />
                  Print
                </>
              )}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

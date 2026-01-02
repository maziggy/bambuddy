import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { X, Loader2, Link2, Check } from 'lucide-react';
import { api } from '../api/client';
import { Button } from './Button';

interface LinkSpoolModalProps {
  isOpen: boolean;
  onClose: () => void;
  trayUuid: string;
  trayInfo?: {
    type: string;
    color: string;
    location: string;
  };
}

export function LinkSpoolModal({ isOpen, onClose, trayUuid, trayInfo }: LinkSpoolModalProps) {
  const queryClient = useQueryClient();
  const [selectedSpoolId, setSelectedSpoolId] = useState<number | null>(null);

  // Fetch unlinked spools
  const { data: unlinkedSpools, isLoading } = useQuery({
    queryKey: ['unlinked-spools'],
    queryFn: api.getUnlinkedSpools,
    enabled: isOpen,
  });

  // Link mutation
  const linkMutation = useMutation({
    mutationFn: (spoolId: number) => api.linkSpool(spoolId, trayUuid),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['unlinked-spools'] });
      queryClient.invalidateQueries({ queryKey: ['spoolman-status'] });
      onClose();
    },
  });

  if (!isOpen) return null;

  const handleLink = () => {
    if (selectedSpoolId) {
      linkMutation.mutate(selectedSpoolId);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative w-full max-w-md mx-4 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
          <div className="flex items-center gap-2">
            <Link2 className="w-5 h-5 text-bambu-green" />
            <h2 className="text-lg font-semibold text-white">Link to Spoolman</h2>
          </div>
          <button
            onClick={onClose}
            className="p-1 text-bambu-gray hover:text-white rounded transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-4 space-y-4">
          {/* Tray info */}
          {trayInfo && (
            <div className="p-3 bg-bambu-dark rounded-lg border border-bambu-dark-tertiary">
              <p className="text-xs text-bambu-gray mb-1">Linking AMS tray:</p>
              <div className="flex items-center gap-2">
                {trayInfo.color && (
                  <span
                    className="w-4 h-4 rounded-full border border-white/20"
                    style={{ backgroundColor: `#${trayInfo.color}` }}
                  />
                )}
                <span className="text-white font-medium">{trayInfo.type}</span>
                <span className="text-bambu-gray">({trayInfo.location})</span>
              </div>
            </div>
          )}

          {/* Spool UUID */}
          <div className="p-3 bg-bambu-dark rounded-lg border border-bambu-dark-tertiary">
            <p className="text-xs text-bambu-gray mb-1">Spool UUID:</p>
            <code className="text-xs text-bambu-green font-mono break-all">{trayUuid}</code>
          </div>

          {/* Spool list */}
          <div>
            <p className="text-sm text-bambu-gray mb-2">
              Select a Spoolman spool to link:
            </p>

            {isLoading ? (
              <div className="flex justify-center py-8">
                <Loader2 className="w-6 h-6 text-bambu-green animate-spin" />
              </div>
            ) : unlinkedSpools && unlinkedSpools.length > 0 ? (
              <div className="max-h-64 overflow-y-auto space-y-2">
                {unlinkedSpools.map((spool) => (
                  <button
                    key={spool.id}
                    onClick={() => setSelectedSpoolId(spool.id)}
                    className={`w-full p-3 rounded-lg border text-left transition-colors ${
                      selectedSpoolId === spool.id
                        ? 'bg-bambu-green/20 border-bambu-green'
                        : 'bg-bambu-dark border-bambu-dark-tertiary hover:border-bambu-gray'
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      {spool.filament_color_hex && (
                        <span
                          className="w-4 h-4 rounded-full border border-white/20 flex-shrink-0"
                          style={{ backgroundColor: `#${spool.filament_color_hex}` }}
                        />
                      )}
                      <div className="flex-1 min-w-0">
                        <p className="text-white font-medium truncate">
                          {spool.filament_name || 'Unknown filament'}
                        </p>
                        <p className="text-xs text-bambu-gray">
                          {spool.filament_material || 'Unknown'}
                          {spool.remaining_weight !== null && ` - ${Math.round(spool.remaining_weight)}g`}
                          {spool.location && ` - ${spool.location}`}
                        </p>
                      </div>
                      {selectedSpoolId === spool.id && (
                        <Check className="w-4 h-4 text-bambu-green flex-shrink-0" />
                      )}
                    </div>
                  </button>
                ))}
              </div>
            ) : (
              <div className="text-center py-8 text-bambu-gray">
                <p>No unlinked spools found in Spoolman.</p>
                <p className="text-xs mt-1">All spools are already linked to AMS trays.</p>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 p-4 border-t border-bambu-dark-tertiary">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={handleLink}
            disabled={!selectedSpoolId || linkMutation.isPending}
          >
            {linkMutation.isPending ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Linking...
              </>
            ) : (
              <>
                <Link2 className="w-4 h-4" />
                Link Spool
              </>
            )}
          </Button>
        </div>

        {/* Error */}
        {linkMutation.isError && (
          <div className="mx-4 mb-4 p-2 bg-red-500/20 border border-red-500/50 rounded text-sm text-red-400">
            {(linkMutation.error as Error).message}
          </div>
        )}
      </div>
    </div>
  );
}

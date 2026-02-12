import { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, Loader2, Search, Link } from 'lucide-react';
import { api } from '../api/client';
import type { InventorySpool } from '../api/client';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';

interface LinkSpoolModalProps {
  isOpen: boolean;
  onClose: () => void;
  tagUid: string;
  trayUuid: string;
  printerId: number;
  amsId: number;
  trayId: number;
}

export function LinkSpoolModal({ isOpen, onClose, tagUid, trayUuid, printerId, amsId, trayId }: LinkSpoolModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [search, setSearch] = useState('');

  const { data: spools, isLoading } = useQuery({
    queryKey: ['inventory-spools'],
    queryFn: () => api.getSpools(false),
    enabled: isOpen,
  });

  // Filter to untagged spools matching search
  const filteredSpools = useMemo(() => {
    if (!spools) return [];
    return spools.filter((s: InventorySpool) => {
      if (s.tag_uid || s.tray_uuid) return false; // Already tagged
      if (!search) return true;
      const q = search.toLowerCase();
      return (
        s.material.toLowerCase().includes(q) ||
        (s.brand && s.brand.toLowerCase().includes(q)) ||
        (s.color_name && s.color_name.toLowerCase().includes(q))
      );
    });
  }, [spools, search]);

  const linkMutation = useMutation({
    mutationFn: (spoolId: number) =>
      api.linkTagToSpool(spoolId, {
        tag_uid: tagUid || undefined,
        tray_uuid: trayUuid || undefined,
        tag_type: trayUuid ? 'bambulab' : 'generic',
        data_origin: 'nfc_link',
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['inventory-spools'] });
      queryClient.invalidateQueries({ queryKey: ['inventory-assignments'] });
      showToast(t('inventory.tagLinked'), 'success');
      onClose();
    },
    onError: (err: Error) => {
      showToast(err.message || t('inventory.tagLinkFailed'), 'error');
    },
  });

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative bg-bambu-dark-secondary rounded-xl shadow-xl w-full max-w-md mx-4 max-h-[80vh] flex flex-col border border-bambu-dark-tertiary">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-white/10">
          <div>
            <h3 className="text-lg font-semibold text-white flex items-center gap-2">
              <Link className="w-5 h-5 text-bambu-green" />
              {t('inventory.linkToSpool')}
            </h3>
            <p className="text-xs text-bambu-gray mt-1">
              AMS {amsId} T{trayId} &middot; Printer #{printerId}
            </p>
          </div>
          <button onClick={onClose} className="p-1 text-bambu-gray hover:text-white rounded transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Search */}
        <div className="p-4 border-b border-white/10">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t('inventory.searchSpools')}
              className="w-full pl-9 pr-3 py-2 bg-bambu-dark rounded-lg border border-white/10 text-white text-sm placeholder:text-bambu-gray focus:outline-none focus:border-bambu-green"
            />
          </div>
          {(tagUid || trayUuid) && (
            <p className="text-xs text-bambu-gray mt-2 font-mono truncate" title={tagUid || trayUuid}>
              Tag: {tagUid || trayUuid}
            </p>
          )}
        </div>

        {/* Spool List */}
        <div className="flex-1 overflow-y-auto p-2 min-h-0">
          {isLoading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="w-6 h-6 animate-spin text-bambu-green" />
            </div>
          ) : filteredSpools.length === 0 ? (
            <p className="text-center text-bambu-gray py-8 text-sm">
              {t('inventory.noSpoolsMatch')}
            </p>
          ) : (
            filteredSpools.map((spool: InventorySpool) => (
              <button
                key={spool.id}
                onClick={() => linkMutation.mutate(spool.id)}
                disabled={linkMutation.isPending}
                className="w-full flex items-center gap-3 p-3 rounded-lg hover:bg-white/5 transition-colors text-left"
              >
                <span
                  className="w-6 h-6 rounded-full border border-white/20 flex-shrink-0"
                  style={{ backgroundColor: spool.rgba ? `#${spool.rgba.substring(0, 6)}` : '#808080' }}
                />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-white font-medium truncate">
                    {spool.brand ? `${spool.brand} ` : ''}{spool.material}
                    {spool.subtype ? ` ${spool.subtype}` : ''}
                  </div>
                  <div className="text-xs text-bambu-gray truncate">
                    {spool.color_name || 'No color'} &middot; #{spool.id}
                  </div>
                </div>
                <span className="text-xs text-bambu-gray">
                  {Math.round(spool.label_weight - spool.weight_used)}g
                </span>
              </button>
            ))
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-white/10 flex justify-end">
          <Button variant="ghost" onClick={onClose}>
            {t('inventory.cancel') || 'Cancel'}
          </Button>
        </div>
      </div>
    </div>
  );
}

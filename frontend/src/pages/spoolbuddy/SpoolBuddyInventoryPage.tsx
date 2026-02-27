import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { api, type InventorySpool } from '../../api/client';
import { SpoolIcon } from '../../components/spoolbuddy/SpoolIcon';

function formatDate(dateStr: string | null): string {
  if (!dateStr) return '-';
  try {
    const d = new Date(dateStr);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  } catch {
    return '-';
  }
}

function SpoolCard({ spool }: { spool: InventorySpool }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);

  const remaining = Math.max(0, spool.label_weight - spool.weight_used);
  const remainingPct = spool.label_weight > 0 ? (remaining / spool.label_weight) * 100 : 0;
  const colorHex = spool.rgba ? `#${spool.rgba.slice(0, 6)}` : '#808080';
  const fillColor = remainingPct > 50 ? '#22c55e' : remainingPct > 15 ? '#f59e0b' : '#ef4444';

  return (
    <button
      className="w-full bg-zinc-800 rounded-lg p-3.5 text-left transition-colors hover:bg-zinc-750 active:bg-zinc-700"
      onClick={() => setExpanded(!expanded)}
    >
      <div className="flex items-center gap-3">
        {/* Spool icon */}
        <SpoolIcon color={colorHex} isEmpty={false} size={36} />

        {/* Info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-base font-medium text-zinc-100 truncate">
              {spool.color_name || spool.material}
            </span>
            {spool.tag_uid && (
              <svg className="w-3 h-3 text-green-500 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A2 2 0 013 12V7a4 4 0 014-4z" />
              </svg>
            )}
          </div>
          <span className="text-sm text-zinc-500 truncate block">
            {spool.brand ? `${spool.brand} \u2022 ` : ''}{spool.material}{spool.subtype ? ` ${spool.subtype}` : ''}
          </span>
        </div>

        {/* Weight */}
        <div className="text-right shrink-0">
          <div className="text-base text-zinc-300">{Math.round(remaining)}g</div>
          <div className="text-sm text-zinc-500">{t('spoolbuddy.spool.remaining', 'Remaining')}</div>
        </div>
      </div>

      {/* Fill bar */}
      <div className="w-full h-1.5 bg-zinc-700 rounded-full overflow-hidden mt-2">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${Math.min(100, remainingPct)}%`, backgroundColor: fillColor }}
        />
      </div>

      {/* Expanded details */}
      {expanded && (
        <div className="mt-3 pt-3 border-t border-zinc-700 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
          <div className="flex justify-between">
            <span className="text-zinc-500">{t('spoolbuddy.spool.labelWeight', 'Label')}</span>
            <span className="text-zinc-400">{spool.label_weight}g</span>
          </div>
          <div className="flex justify-between">
            <span className="text-zinc-500">{t('spoolbuddy.spool.coreWeight', 'Core')}</span>
            <span className="text-zinc-400">{spool.core_weight}g</span>
          </div>
          <div className="flex justify-between">
            <span className="text-zinc-500">{t('spoolbuddy.spool.material', 'Material')}</span>
            <span className="text-zinc-400">{spool.material}{spool.subtype ? ` ${spool.subtype}` : ''}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-zinc-500">{t('spoolbuddy.spool.lastUsed', 'Last used')}</span>
            <span className="text-zinc-400">{formatDate(spool.last_used)}</span>
          </div>
          {spool.tag_uid && (
            <div className="col-span-2 flex justify-between">
              <span className="text-zinc-500">Tag</span>
              <span className="text-zinc-400 font-mono text-xs">{spool.tag_uid}</span>
            </div>
          )}
        </div>
      )}
    </button>
  );
}

export function SpoolBuddyInventoryPage() {
  const { t } = useTranslation();
  const [search, setSearch] = useState('');

  const { data: spools = [], isLoading } = useQuery({
    queryKey: ['inventory-spools'],
    queryFn: () => api.getSpools(false),
  });

  const filtered = useMemo(() => {
    if (!search.trim()) return spools;
    const q = search.toLowerCase();
    return spools.filter((s) =>
      s.material.toLowerCase().includes(q) ||
      s.brand?.toLowerCase().includes(q) ||
      s.color_name?.toLowerCase().includes(q)
    );
  }, [spools, search]);

  return (
    <div className="h-full flex flex-col p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold text-zinc-100">
          {t('spoolbuddy.nav.inventory', 'Inventory')}
        </h1>
        <span className="text-sm text-zinc-500">{spools.length} {t('spoolbuddy.inventory.spools', 'spools')}</span>
      </div>

      {/* Search */}
      <div className="relative mb-4">
        <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-zinc-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
        </svg>
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t('spoolbuddy.inventory.search', 'Search spools...')}
          className="w-full pl-10 pr-4 py-2.5 bg-zinc-800 border border-zinc-700 rounded-lg text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-green-500 min-h-[44px]"
        />
      </div>

      {/* Spool list */}
      <div className="flex-1 min-h-0 overflow-y-auto space-y-2">
        {isLoading ? (
          <div className="flex items-center justify-center h-32">
            <span className="text-zinc-500">{t('common.loading', 'Loading...')}</span>
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-32 text-zinc-500">
            <svg className="w-10 h-10 mb-2 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />
            </svg>
            <p className="text-sm">{search ? t('spoolbuddy.inventory.noResults', 'No matching spools') : t('spoolbuddy.inventory.empty', 'No spools in inventory')}</p>
          </div>
        ) : (
          filtered.map((spool) => <SpoolCard key={spool.id} spool={spool} />)
        )}
      </div>
    </div>
  );
}

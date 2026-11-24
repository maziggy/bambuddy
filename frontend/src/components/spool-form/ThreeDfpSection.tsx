import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ExternalLink, Loader2, Search, ShoppingCart, Unlink } from 'lucide-react';

import { api, ApiError } from '../../api/client';
import type { ThreeDfpFilament, ThreeDfpStore } from '../../api/client';
import { useAuth } from '../../contexts/AuthContext';

/** The filament's page on 3DFP, for a spool linked by someone else whose own
 *  account isn't connected (no API call needed). */
const threedfpFilamentPage = (id: number) => `https://3dfilamentprofiles.com/filament/details/${id}`;

interface ThreeDfpSectionProps {
  /** The 3DFP filament the spool is linked to, or null. */
  filamentId: number | null;
  /** A filament was picked: fill the form from it and link the spool. */
  onApply: (filament: ThreeDfpFilament) => void;
  onUnlink: () => void;
}

function describe(f: ThreeDfpFilament): string {
  return [f.brand_name, f.material, f.material_type, f.color].filter(Boolean).join(' ');
}

function Swatch({ rgb }: { rgb: string | null }) {
  if (!rgb) return null;
  return <span className="inline-block w-3 h-3 rounded-full border border-white/20 flex-shrink-0" style={{ backgroundColor: rgb }} />;
}

/**
 * Look a filament up on 3D Filament Profiles by barcode, id or link (#2981),
 * and show a linked spool's store prices with a Reorder link. Lookups use the
 * viewer's own connected account; a viewer without one still sees which
 * filament the spool is linked to.
 */
export function ThreeDfpSection({ filamentId, onApply, onUnlink }: ThreeDfpSectionProps) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const { hasPermission } = useAuth();
  const canLookup = hasPermission('threedfp:auth');

  const [query, setQuery] = useState('');
  const [results, setResults] = useState<ThreeDfpFilament[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [applyingId, setApplyingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data: status } = useQuery({
    queryKey: ['threedfpStatus'],
    queryFn: api.threedfpStatus,
    enabled: canLookup,
    staleTime: 60_000,
  });
  const connected = canLookup && !!status?.connected;

  // Cached for an hour here and for a day on the server: a free account has
  // 100 requests a month.
  const { data: linked, isLoading: linkedLoading } = useQuery({
    queryKey: ['threedfpFilament', filamentId],
    queryFn: () => api.threedfpGetFilament(filamentId!),
    enabled: connected && filamentId !== null,
    staleTime: 60 * 60 * 1000,
    retry: false,
  });

  const explain = (err: unknown): string => {
    if (err instanceof ApiError) {
      if (err.status === 409) {
        queryClient.invalidateQueries({ queryKey: ['threedfpStatus'] });
        return t('inventory.threedfp.errors.disconnected');
      }
      if (err.status === 402) return t('inventory.threedfp.errors.noAccess');
      if (err.status === 429) return t('inventory.threedfp.errors.rateLimited');
      if (err.status === 400) return t('inventory.threedfp.errors.badQuery');
    }
    return t('inventory.threedfp.errors.failed');
  };

  const apply = async (f: ThreeDfpFilament) => {
    setError(null);
    setApplyingId(f.id);
    try {
      // A barcode answers summaries; the full record has weights and stores.
      const full = f.stores.length || f.nominal_weight != null ? f : await api.threedfpGetFilament(f.id);
      queryClient.setQueryData(['threedfpFilament', full.id], full);
      onApply(full);
      setResults(null);
      setQuery('');
    } catch (err) {
      setError(explain(err));
    } finally {
      setApplyingId(null);
    }
  };

  const search = async () => {
    const q = query.trim();
    if (!q) return;
    setSearching(true);
    setError(null);
    setResults(null);
    try {
      const { results: found } = await api.threedfpLookup(q);
      if (found.length === 1) await apply(found[0]);
      else setResults(found);
    } catch (err) {
      setError(explain(err));
    } finally {
      setSearching(false);
    }
  };

  const price = (store: ThreeDfpStore): string | null => {
    if (store.price == null) return null;
    try {
      return store.currency
        ? new Intl.NumberFormat(i18n.language, { style: 'currency', currency: store.currency }).format(store.price)
        : String(store.price);
    } catch {
      return `${store.price} ${store.currency ?? ''}`.trim();
    }
  };

  const availability = (value: string | null): string | null => {
    if (value === 'in_stock') return t('inventory.threedfp.inStock');
    if (value === 'out_of_stock') return t('inventory.threedfp.outOfStock');
    return null;
  };

  if (!canLookup && filamentId === null) return null;

  return (
    <div className="rounded-lg border border-bambu-dark-tertiary p-3 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-bambu-gray uppercase tracking-wide">
          {t('inventory.threedfp.title')}
        </h3>
        {filamentId !== null && (
          <button
            type="button"
            onClick={onUnlink}
            className="text-xs text-bambu-gray hover:text-white inline-flex items-center gap-1"
          >
            <Unlink className="w-3 h-3" />
            {t('inventory.threedfp.unlink')}
          </button>
        )}
      </div>

      {filamentId !== null && (
        <div className="space-y-2">
          {linked ? (
            <>
              <div className="flex items-center gap-2 text-sm text-white">
                <Swatch rgb={linked.rgb} />
                <span>{describe(linked)}</span>
                {linked.discontinued && (
                  <span className="text-xs px-1.5 py-0.5 rounded bg-yellow-500/20 text-yellow-700 dark:text-yellow-400">
                    {t('inventory.threedfp.discontinued')}
                  </span>
                )}
              </div>
              {linked.stores.length === 0 ? (
                <p className="text-xs text-bambu-gray">{t('inventory.threedfp.noStores')}</p>
              ) : (
                <ul className="space-y-1">
                  {linked.stores.map((store) => (
                    <li key={`${store.region}-${store.url}`} className="flex items-center justify-between gap-2 text-sm">
                      <span className="text-bambu-gray">
                        <span className="uppercase text-white mr-2">{store.region}</span>
                        {price(store)}
                        {availability(store.availability) && ` · ${availability(store.availability)}`}
                      </span>
                      <a
                        href={store.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded bg-bambu-green/20 text-bambu-green hover:bg-bambu-green/30"
                      >
                        <ShoppingCart className="w-3 h-3" />
                        {t('inventory.threedfp.reorder')}
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </>
          ) : connected && linkedLoading ? (
            <Loader2 className="w-4 h-4 text-bambu-green animate-spin" />
          ) : (
            <p className="text-sm text-bambu-gray">{t('inventory.threedfp.linkedTo', { id: filamentId })}</p>
          )}
          <a
            href={linked?.url ?? threedfpFilamentPage(filamentId)}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-bambu-green hover:underline inline-flex items-center gap-1"
          >
            {t('inventory.threedfp.viewOn')}
            <ExternalLink className="w-3 h-3" />
          </a>
        </div>
      )}

      {canLookup && !connected && (
        <p className="text-xs text-bambu-gray">{t('inventory.threedfp.connectHint')}</p>
      )}

      {connected && (
        <div className="space-y-2">
          <div className="flex gap-2">
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                // A USB barcode scanner types the digits and presses Enter.
                if (e.key === 'Enter') {
                  e.preventDefault();
                  void search();
                }
              }}
              placeholder={t('inventory.threedfp.placeholder')}
              aria-label={t('inventory.threedfp.placeholder')}
              className="flex-1 min-w-0 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none"
            />
            <button
              type="button"
              onClick={() => void search()}
              disabled={searching || !query.trim()}
              className="px-3 py-2 rounded-lg bg-bambu-green text-white text-sm inline-flex items-center gap-1 disabled:opacity-50"
            >
              {searching ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
              {t('inventory.threedfp.lookup')}
            </button>
          </div>
          {results !== null && results.length === 0 && (
            <p className="text-xs text-bambu-gray">{t('inventory.threedfp.noResults')}</p>
          )}
          {results !== null && results.length > 1 && (
            <div className="space-y-1">
              <p className="text-xs text-bambu-gray">{t('inventory.threedfp.pick')}</p>
              {results.map((f) => (
                <button
                  key={f.id}
                  type="button"
                  onClick={() => void apply(f)}
                  disabled={applyingId !== null}
                  className="w-full flex items-center gap-2 px-2 py-1.5 rounded text-left text-sm text-white hover:bg-bambu-dark-tertiary disabled:opacity-50"
                >
                  <Swatch rgb={f.rgb} />
                  <span className="flex-1">{describe(f)}</span>
                  {applyingId === f.id && <Loader2 className="w-3 h-3 animate-spin" />}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {error && <p className="text-xs text-red-700 dark:text-red-400">{error}</p>}
    </div>
  );
}

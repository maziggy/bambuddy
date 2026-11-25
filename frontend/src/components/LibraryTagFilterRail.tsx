import { useLayoutEffect, useRef, useState } from 'react';
import { Search, Tag as TagIcon, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { LibraryTag } from '../api/client';

/** From this many tags on, the rail offers a search box. */
export const TAG_SEARCH_MIN_TAGS = 12;

interface LibraryTagFilterRailProps {
  /** The whole catalog, in the order to show it. */
  tags: LibraryTag[];
  selectedIds: number[];
  onToggle: (tagId: number) => void;
  onClear: () => void;
}

// One line high whatever the name, so the two-row clamp below holds two
// whole rows on a narrow screen too; the full name is in the title.
const CHIP =
  'inline-flex items-center gap-1 max-w-full whitespace-nowrap px-2 py-0.5 rounded-full text-xs border transition-colors';

/**
 * The File Manager's tag filter (#1268).
 *
 * It used to list every catalog tag as a chip, which with 160 tags wrapped to
 * a dozen rows and left the file list a strip at the bottom (#3411). Selected
 * tags still always show, first; the rest are clamped to two rows, with a
 * toggle to show them all in a box of bounded height that scrolls on its own.
 * Larger catalogs get a search box over the unselected tags.
 *
 * Whether the two rows overflow is measured, not counted, so the toggle
 * appears exactly when tags are hidden, at any window width.
 */
export function LibraryTagFilterRail({ tags, selectedIds, onToggle, onClear }: LibraryTagFilterRailProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const [query, setQuery] = useState('');
  const [overflowing, setOverflowing] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  const selected = selectedIds.map(id => tags.find(tg => tg.id === id)).filter((tg): tg is LibraryTag => !!tg);
  const showSearch = tags.length >= TAG_SEARCH_MIN_TAGS;
  // A query typed before the catalog shrank below the threshold must not
  // keep filtering with no box left to clear it in.
  const needle = showSearch ? query.trim().toLowerCase() : '';
  const unselected = tags.filter(
    tg => !selectedIds.includes(tg.id) && (!needle || tg.name.toLowerCase().includes(needle)),
  );
  // Names too: a rename changes the chips' widths without changing the ids.
  const unselectedKey = unselected.map(tg => `${tg.id}:${tg.name}`).join('\n');

  useLayoutEffect(() => {
    const element = listRef.current;
    if (!element || expanded) return;
    // An overflow-hidden box keeps its scroll offset: after "Show fewer" the
    // two rows would show wherever the expanded list was scrolled to.
    element.scrollTop = 0;
    const measure = () => setOverflowing(element.scrollHeight > element.clientHeight + 1);
    measure();
    // A web font arriving late changes every chip's width, and the clamped
    // box's own size, which is all the observer sees, stays the same.
    document.fonts?.ready.then(measure).catch(() => {});
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure);
      return () => window.removeEventListener('resize', measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [expanded, unselectedKey]);

  return (
    <div className="mb-3 flex flex-col gap-2 p-2 sm:p-3 bg-bambu-dark-secondary rounded-lg border border-bambu-dark-tertiary">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-bambu-gray font-medium shrink-0">{t('fileManager.tags.filterLabel')}</span>
        {selected.map(tg => (
          <button
            key={tg.id}
            type="button"
            onClick={() => onToggle(tg.id)}
            aria-pressed="true"
            className={`${CHIP} bg-bambu-green/20 text-bambu-green border-bambu-green/40 hover:bg-bambu-green/30`}
            title={tg.name}
          >
            <TagIcon className="w-3 h-3 shrink-0" />
            <span className="truncate">{tg.name}</span>
            <X className="w-3 h-3 shrink-0" />
          </button>
        ))}
        {showSearch && (
          <div className="relative w-full sm:w-48">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-bambu-gray" />
            <input
              type="search"
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder={t('fileManager.tags.searchTags')}
              aria-label={t('fileManager.tags.searchTags')}
              className="w-full pl-7 pr-2 py-1 bg-bambu-dark border border-bambu-dark-tertiary rounded text-xs text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
            />
          </div>
        )}
        {selected.length > 0 && (
          <button type="button" onClick={onClear} className="ml-auto text-xs text-bambu-gray hover:text-white shrink-0">
            {t('fileManager.tags.clearAll')}
          </button>
        )}
      </div>
      {unselected.length > 0 ? (
        <div
          ref={listRef}
          data-testid="tag-filter-list"
          onFocus={e => {
            // Tabbing into a chip the clamp hides would scroll the clipped
            // rows under it out of view: show them all instead.
            const list = listRef.current;
            const chip = e.target as HTMLElement;
            if (!expanded && list && chip.offsetTop + chip.offsetHeight > list.clientHeight) setExpanded(true);
          }}
          className={`relative flex flex-wrap items-center gap-2 ${
            expanded ? 'max-h-[40vh] overflow-y-auto' : 'max-h-[3.25rem] overflow-hidden'
          }`}
        >
          {unselected.map(tg => (
            <button
              key={tg.id}
              type="button"
              onClick={() => onToggle(tg.id)}
              aria-pressed="false"
              className={`${CHIP} bg-bambu-dark text-bambu-gray border-bambu-dark-tertiary hover:text-white hover:border-bambu-green/40`}
              title={tg.name}
            >
              <TagIcon className="w-3 h-3 shrink-0" />
              <span className="truncate">{tg.name}</span>
            </button>
          ))}
        </div>
      ) : (
        needle && <p className="text-xs text-bambu-gray">{t('fileManager.tags.noTagMatches')}</p>
      )}
      {(expanded || overflowing) && unselected.length > 0 && (
        <button
          type="button"
          onClick={() => setExpanded(e => !e)}
          aria-expanded={expanded}
          className="self-start text-xs text-bambu-green hover:underline"
        >
          {expanded ? t('fileManager.tags.showFewerTags') : t('fileManager.tags.showAllTags', { count: unselected.length })}
        </button>
      )}
    </div>
  );
}

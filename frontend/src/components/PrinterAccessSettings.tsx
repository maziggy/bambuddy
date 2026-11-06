import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Loader2,
  MapPin,
  Pencil,
  Printer as PrinterIcon,
  Save,
  Search,
  Users,
  X,
} from 'lucide-react';
import { api } from '../api/client';
import type { Group, Printer } from '../api/client';
import { Button } from './Button';
import { Card } from './Card';
import { Toggle } from './Toggle';
import { useToast } from '../contexts/ToastContext';

/**
 * Which printers each group may use (#1727).
 *
 * A limited group reaches the printers picked for it plus every printer whose
 * location it was given, including printers added there later. Edits collect
 * as per-group drafts, from either view, and are saved together.
 */

interface Draft {
  restrict: boolean;
  printerIds: number[];
  locations: string[];
}

type View = 'groups' | 'printers';
type GroupFilter = 'all' | 'limited' | 'open';
type AccessFilter = 'all' | 'with' | 'without';

const NO_LOCATION = '__none__';
// Past this many printers, location sections start collapsed
const AUTO_COLLAPSE_OVER = 60;

const inputClass =
  'px-3 py-2 text-sm bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green';

const isAdministrators = (group: Group) => group.is_system && group.name === 'Administrators';

const draftOf = (group: Group): Draft => ({
  restrict: group.restrict_printers,
  printerIds: [...group.printer_ids].sort((a, b) => a - b),
  locations: [...(group.locations ?? [])].sort(),
});

const sameDraft = (a: Draft, b: Draft) =>
  a.restrict === b.restrict &&
  a.printerIds.length === b.printerIds.length &&
  a.printerIds.every((id, i) => id === b.printerIds[i]) &&
  a.locations.length === b.locations.length &&
  a.locations.every((loc, i) => loc === b.locations[i]);

const locationKey = (printer: Printer) => printer.location || NO_LOCATION;

/** How a draft reaches a printer: through its location, picked, or not at all. */
function reachOf(draft: Draft, printer: Printer): 'location' | 'picked' | null {
  if (printer.location && draft.locations.includes(printer.location)) return 'location';
  if (draft.printerIds.includes(printer.id)) return 'picked';
  return null;
}

export function PrinterAccessSettings() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [searchParams, setSearchParams] = useSearchParams();

  const view: View = searchParams.get('view') === 'printers' ? 'printers' : 'groups';
  const selectedGroupId = Number(searchParams.get('group')) || null;
  const focusPrinterId = Number(searchParams.get('printer')) || null;

  const setParams = (changes: Record<string, string | null>) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        for (const [key, value] of Object.entries(changes)) {
          if (value === null) next.delete(key);
          else next.set(key, value);
        }
        return next;
      },
      { replace: true }
    );
  };

  const [drafts, setDrafts] = useState<Record<number, Draft>>({});
  const [groupSearch, setGroupSearch] = useState('');
  const [groupFilter, setGroupFilter] = useState<GroupFilter>('all');
  const [printerSearch, setPrinterSearch] = useState('');
  const [locationFilter, setLocationFilter] = useState('all');
  const [modelFilter, setModelFilter] = useState('all');
  const [accessFilter, setAccessFilter] = useState<AccessFilter>('all');
  const [sectionOpen, setSectionOpen] = useState<Record<string, boolean>>({});

  const { data: groups, isLoading: groupsLoading } = useQuery({
    queryKey: ['groups'],
    queryFn: () => api.getGroups(),
  });
  const { data: printers, isLoading: printersLoading } = useQuery({
    queryKey: ['printers'],
    queryFn: () => api.getPrinters(),
  });
  const { data: users } = useQuery({
    queryKey: ['users'],
    queryFn: () => api.getUsers(),
  });

  const draftFor = (group: Group): Draft => drafts[group.id] ?? draftOf(group);

  const updateDraft = (group: Group, change: (draft: Draft) => Draft) => {
    setDrafts((prev) => {
      const next = change(prev[group.id] ?? draftOf(group));
      const normalized: Draft = {
        restrict: next.restrict,
        printerIds: [...new Set(next.printerIds)].sort((a, b) => a - b),
        locations: [...new Set(next.locations)].sort(),
      };
      const rest = { ...prev };
      if (sameDraft(normalized, draftOf(group))) delete rest[group.id];
      else rest[group.id] = normalized;
      return rest;
    });
  };

  const dirtyGroups = useMemo(
    () => (groups ?? []).filter((g) => drafts[g.id] !== undefined),
    [groups, drafts]
  );
  const isDirty = dirtyGroups.length > 0;

  useEffect(() => {
    if (!isDirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [isDirty]);

  const saveMutation = useMutation({
    mutationFn: async () => {
      // One group after the other, so a refusal leaves the rest as drafts
      for (const group of dirtyGroups) {
        const draft = drafts[group.id];
        await api.updateGroup(group.id, {
          restrict_printers: draft.restrict,
          printer_ids: draft.printerIds,
          locations: draft.locations,
        });
        setDrafts((prev) => {
          const rest = { ...prev };
          delete rest[group.id];
          return rest;
        });
      }
    },
    onSuccess: () => showToast(t('printerAccess.saved')),
    onError: (error: Error) => showToast(error.message, 'error'),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['groups'] });
      queryClient.invalidateQueries({ queryKey: ['group'] });
    },
  });

  const allPrinters = useMemo(
    () => [...(printers ?? [])].sort((a, b) => a.name.localeCompare(b.name)),
    [printers]
  );

  const locations = useMemo(() => {
    const names = new Set<string>();
    for (const p of allPrinters) if (p.location) names.add(p.location);
    return [...names].sort((a, b) => a.localeCompare(b));
  }, [allPrinters]);
  const hasUnlocated = allPrinters.some((p) => !p.location);

  const models = useMemo(() => {
    const names = new Set<string>();
    for (const p of allPrinters) if (p.model) names.add(p.model);
    return [...names].sort();
  }, [allPrinters]);

  const printersByLocation = useMemo(() => {
    const map = new Map<string, Printer[]>();
    for (const p of allPrinters) {
      const key = locationKey(p);
      map.set(key, [...(map.get(key) ?? []), p]);
    }
    return map;
  }, [allPrinters]);

  // Printers left after search, location and model filters (not the access filter)
  const baseFiltered = useMemo(() => {
    const q = printerSearch.trim().toLowerCase();
    return allPrinters.filter((p) => {
      if (locationFilter !== 'all' && locationKey(p) !== locationFilter) return false;
      if (modelFilter !== 'all' && p.model !== modelFilter) return false;
      if (!q) return true;
      return (
        p.name.toLowerCase().includes(q) ||
        (p.model ?? '').toLowerCase().includes(q) ||
        p.serial_number.toLowerCase().includes(q) ||
        (p.location ?? '').toLowerCase().includes(q)
      );
    });
  }, [allPrinters, printerSearch, locationFilter, modelFilter]);

  const sortedGroups = useMemo(() => [...(groups ?? [])].sort((a, b) => a.name.localeCompare(b.name)), [groups]);

  const groupQuery = groupSearch.trim().toLowerCase();
  const visibleGroups = sortedGroups.filter((g) => {
    if (groupQuery && !g.name.toLowerCase().includes(groupQuery)) return false;
    const limited = !isAdministrators(g) && draftFor(g).restrict;
    if (groupFilter === 'limited') return limited;
    if (groupFilter === 'open') return !limited;
    return true;
  });

  const limitedGroups = sortedGroups.filter((g) => !isAdministrators(g) && draftFor(g).restrict);

  const reachCount = (draft: Draft) => allPrinters.filter((p) => reachOf(draft, p) !== null).length;

  if (groupsLoading || printersLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
      </div>
    );
  }

  const selectedGroup = sortedGroups.find((g) => g.id === selectedGroupId) ?? null;

  const filterBar = (withAccessFilter: boolean) => (
    <div className="flex flex-wrap items-center gap-2">
      <div className="relative flex-1 min-w-[12rem]">
        <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-bambu-gray" />
        <input
          type="text"
          value={printerSearch}
          onChange={(e) => setPrinterSearch(e.target.value)}
          placeholder={t('printerAccess.searchPrinters')}
          aria-label={t('printerAccess.searchPrinters')}
          className={`${inputClass} w-full pl-9`}
        />
      </div>
      <select
        value={locationFilter}
        onChange={(e) => setLocationFilter(e.target.value)}
        aria-label={t('printerAccess.allLocations')}
        className={inputClass}
      >
        <option value="all">{t('printerAccess.allLocations')}</option>
        {locations.map((loc) => (
          <option key={loc} value={loc}>
            {loc}
          </option>
        ))}
        {hasUnlocated && <option value={NO_LOCATION}>{t('printerAccess.noLocation')}</option>}
      </select>
      {models.length > 1 && (
        <select
          value={modelFilter}
          onChange={(e) => setModelFilter(e.target.value)}
          aria-label={t('printerAccess.allModels')}
          className={inputClass}
        >
          <option value="all">{t('printerAccess.allModels')}</option>
          {models.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      )}
      {withAccessFilter && (
        <select
          value={accessFilter}
          onChange={(e) => setAccessFilter(e.target.value as AccessFilter)}
          aria-label={t('printerAccess.accessFilter')}
          className={inputClass}
        >
          <option value="all">{t('printerAccess.accessAll')}</option>
          <option value="with">{t('printerAccess.accessWith')}</option>
          <option value="without">{t('printerAccess.accessWithout')}</option>
        </select>
      )}
    </div>
  );

  const renderGroupList = () => (
    <Card className="lg:w-80 shrink-0 flex flex-col lg:max-h-[calc(100vh-14rem)]">
      <div className="p-3 space-y-2 border-b border-bambu-dark-tertiary">
        <div className="relative">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-bambu-gray" />
          <input
            type="text"
            value={groupSearch}
            onChange={(e) => setGroupSearch(e.target.value)}
            placeholder={t('printerAccess.searchGroups')}
            aria-label={t('printerAccess.searchGroups')}
            className={`${inputClass} w-full pl-9`}
          />
        </div>
        <div className="flex gap-1">
          {(['all', 'limited', 'open'] as const).map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setGroupFilter(f)}
              className={`flex-1 px-2 py-1 text-xs rounded-md transition-colors ${
                groupFilter === f
                  ? 'bg-bambu-green/20 text-bambu-green'
                  : 'text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary'
              }`}
            >
              {t(`printerAccess.groupFilter.${f}`)}
            </button>
          ))}
        </div>
      </div>
      <div className="overflow-y-auto p-1">
        {visibleGroups.length === 0 ? (
          <p className="text-sm text-bambu-gray p-3">{t('printerAccess.noGroupsMatch')}</p>
        ) : (
          visibleGroups.map((g) => {
            const draft = draftFor(g);
            const admins = isAdministrators(g);
            const summary = admins
              ? t('printerAccess.alwaysAll')
              : draft.restrict
              ? t('printerAccess.reachShort', { count: reachCount(draft), total: allPrinters.length })
              : t('printerAccess.notLimited');
            return (
              <button
                key={g.id}
                type="button"
                onClick={() => setParams({ group: String(g.id) })}
                className={`w-full text-left px-3 py-2 rounded-lg transition-colors ${
                  g.id === selectedGroupId ? 'bg-bambu-dark-tertiary' : 'hover:bg-bambu-dark-tertiary/60'
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className="text-sm text-white truncate flex-1">{g.name}</span>
                  {drafts[g.id] && (
                    <span className="w-2 h-2 rounded-full bg-yellow-400 shrink-0" title={t('printerAccess.unsaved')} />
                  )}
                </div>
                <div className="flex items-center gap-2 text-xs text-bambu-gray mt-0.5">
                  <Users className="w-3 h-3 shrink-0" />
                  <span>{g.user_count}</span>
                  <span>·</span>
                  <span className={!admins && draft.restrict ? 'text-bambu-green' : ''}>{summary}</span>
                </div>
              </button>
            );
          })
        )}
      </div>
    </Card>
  );

  const renderGroupEditor = () => {
    if (!selectedGroup) {
      return (
        <Card className="flex-1 p-8 text-center text-sm text-bambu-gray">{t('printerAccess.pickGroup')}</Card>
      );
    }
    const group = selectedGroup;
    const draft = draftFor(group);
    const reached = reachCount(draft);

    const shown = baseFiltered.filter((p) => {
      const reach = reachOf(draft, p);
      if (accessFilter === 'with') return reach !== null;
      if (accessFilter === 'without') return reach === null;
      return true;
    });
    const shownByLocation = new Map<string, Printer[]>();
    for (const p of shown) {
      const key = locationKey(p);
      shownByLocation.set(key, [...(shownByLocation.get(key) ?? []), p]);
    }
    const sectionKeys = [...shownByLocation.keys()].sort((a, b) =>
      a === NO_LOCATION ? 1 : b === NO_LOCATION ? -1 : a.localeCompare(b)
    );
    // Locations given to the group that no printer carries any more (renamed, emptied)
    const staleLocations = draft.locations.filter((loc) => !printersByLocation.has(loc));
    const autoCollapsed = shown.length > AUTO_COLLAPSE_OVER && !printerSearch.trim();
    const isOpen = (key: string) => sectionOpen[key] ?? !autoCollapsed;

    const toggleLocation = (loc: string, on: boolean) =>
      updateDraft(group, (d) => ({
        ...d,
        locations: on ? [...d.locations, loc] : d.locations.filter((l) => l !== loc),
      }));
    const togglePrinter = (id: number, on: boolean) =>
      updateDraft(group, (d) => ({
        ...d,
        printerIds: on ? [...d.printerIds, id] : d.printerIds.filter((p) => p !== id),
      }));
    const pickShown = (on: boolean) => {
      const ids = shown.filter((p) => reachOf(draft, p) !== 'location').map((p) => p.id);
      updateDraft(group, (d) => ({
        ...d,
        printerIds: on ? [...d.printerIds, ...ids] : d.printerIds.filter((id) => !ids.includes(id)),
      }));
    };

    return (
      <Card className="flex-1 min-w-0">
        <div className="p-4 space-y-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 className="text-white font-medium truncate">{group.name}</h3>
              <p className="text-xs text-bambu-gray mt-0.5">
                {t('printerAccess.memberCount', { count: group.user_count })}
              </p>
            </div>
            <Link
              to={`/groups/${group.id}/edit`}
              className="flex items-center gap-1.5 text-xs text-bambu-gray hover:text-white shrink-0"
            >
              <Pencil className="w-3.5 h-3.5" />
              {t('printerAccess.editGroup')}
            </Link>
          </div>

          {isAdministrators(group) ? (
            <p className="text-sm text-bambu-gray">{t('printerAccess.adminsSeeAll')}</p>
          ) : (
            <>
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-sm text-white">{t('printerAccess.limit')}</p>
                  <p className="text-xs text-bambu-gray mt-1">{t('printerAccess.limitHint')}</p>
                </div>
                <Toggle
                  checked={draft.restrict}
                  onChange={(on) => updateDraft(group, (d) => ({ ...d, restrict: on }))}
                />
              </div>

              {draft.restrict && (
                <>
                  <div className="flex flex-wrap items-center gap-3">
                    <span className="text-sm text-bambu-gray">
                      {t('printerAccess.reach', { count: reached, total: allPrinters.length })}
                    </span>
                    {reached === 0 && (
                      <span className="flex items-center gap-1.5 text-xs text-yellow-700 dark:text-yellow-400">
                        <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                        {t('printerAccess.noneGranted')}
                      </span>
                    )}
                  </div>

                  {allPrinters.length === 0 ? (
                    <p className="text-sm text-bambu-gray">{t('printerAccess.noPrinters')}</p>
                  ) : (
                    <>
                      {filterBar(true)}
                      <div className="flex items-center gap-2">
                        <Button size="sm" variant="ghost" onClick={() => pickShown(true)} disabled={shown.length === 0}>
                          {t('printerAccess.tickShown')}
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => pickShown(false)} disabled={shown.length === 0}>
                          {t('printerAccess.untickShown')}
                        </Button>
                      </div>

                      {staleLocations.map((loc) => (
                        <div
                          key={`stale-${loc}`}
                          className="flex items-center justify-between gap-3 px-3 py-2 rounded-lg border border-dashed border-bambu-dark-tertiary"
                        >
                          <span className="flex items-center gap-2 text-sm text-bambu-gray min-w-0">
                            <MapPin className="w-4 h-4 shrink-0" />
                            <span className="truncate">{loc}</span>
                            <span className="text-xs">({t('printerAccess.emptyLocation')})</span>
                          </span>
                          <label className="flex items-center gap-2 text-xs text-bambu-gray cursor-pointer shrink-0">
                            <input
                              type="checkbox"
                              checked
                              onChange={() => toggleLocation(loc, false)}
                              aria-label={t('printerAccess.wholeLocationHint', { location: loc })}
                              className="w-4 h-4 rounded border-bambu-gray text-bambu-green focus:ring-bambu-green focus:ring-offset-0 bg-bambu-dark-secondary"
                            />
                            {t('printerAccess.wholeLocation')}
                          </label>
                        </div>
                      ))}

                      {sectionKeys.length === 0 ? (
                        <p className="text-sm text-bambu-gray py-4 text-center">{t('printerAccess.noPrintersMatch')}</p>
                      ) : (
                        <div className="space-y-2">
                          {sectionKeys.map((key) => {
                            const rows = shownByLocation.get(key) ?? [];
                            const all = printersByLocation.get(key) ?? [];
                            const inLocation = all.filter((p) => reachOf(draft, p) !== null).length;
                            const isLocation = key !== NO_LOCATION;
                            const granted = isLocation && draft.locations.includes(key);
                            const open = isOpen(key);
                            return (
                              <div key={key} className="rounded-lg border border-bambu-dark-tertiary">
                                <div className="flex items-center gap-3 px-3 py-2">
                                  <button
                                    type="button"
                                    onClick={() => setSectionOpen((prev) => ({ ...prev, [key]: !open }))}
                                    className="flex items-center gap-2 flex-1 min-w-0 text-left"
                                    aria-expanded={open}
                                  >
                                    {open ? (
                                      <ChevronDown className="w-4 h-4 text-bambu-gray shrink-0" />
                                    ) : (
                                      <ChevronRight className="w-4 h-4 text-bambu-gray shrink-0" />
                                    )}
                                    <MapPin className="w-4 h-4 text-bambu-gray shrink-0" />
                                    <span className="text-sm text-white truncate">
                                      {isLocation ? key : t('printerAccess.noLocation')}
                                    </span>
                                    <span className="text-xs text-bambu-gray tabular-nums shrink-0">
                                      {inLocation}/{all.length}
                                    </span>
                                  </button>
                                  {isLocation && (
                                    <label
                                      className="flex items-center gap-2 text-xs text-bambu-gray cursor-pointer shrink-0"
                                      title={t('printerAccess.wholeLocationHint', { location: key })}
                                    >
                                      <input
                                        type="checkbox"
                                        checked={granted}
                                        onChange={(e) => toggleLocation(key, e.target.checked)}
                                        aria-label={t('printerAccess.wholeLocationHint', { location: key })}
                                        className="w-4 h-4 rounded border-bambu-gray text-bambu-green focus:ring-bambu-green focus:ring-offset-0 bg-bambu-dark-secondary"
                                      />
                                      {t('printerAccess.wholeLocation')}
                                    </label>
                                  )}
                                </div>
                                {open && (
                                  <div className="grid grid-cols-1 md:grid-cols-2 gap-1 px-2 pb-2">
                                    {rows.map((p) => {
                                      const reach = reachOf(draft, p);
                                      return (
                                        <label
                                          key={p.id}
                                          className={`flex items-center gap-3 px-2 py-1.5 rounded ${
                                            reach === 'location' ? 'cursor-default' : 'cursor-pointer hover:bg-bambu-dark-tertiary/60'
                                          }`}
                                        >
                                          <input
                                            type="checkbox"
                                            checked={reach !== null}
                                            disabled={reach === 'location'}
                                            onChange={(e) => togglePrinter(p.id, e.target.checked)}
                                            aria-label={p.name}
                                            className="w-4 h-4 shrink-0 rounded border-bambu-gray text-bambu-green focus:ring-bambu-green focus:ring-offset-0 bg-bambu-dark-secondary disabled:opacity-60"
                                          />
                                          <span className="min-w-0 flex-1">
                                            <span className="block text-sm text-white truncate">{p.name}</span>
                                            <span className="block text-xs text-bambu-gray truncate">
                                              {[p.model, p.serial_number].filter(Boolean).join(' · ')}
                                            </span>
                                          </span>
                                          {reach === 'location' && (
                                            <span className="text-xs text-bambu-green shrink-0">
                                              {t('printerAccess.viaLocation')}
                                            </span>
                                          )}
                                        </label>
                                      );
                                    })}
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </>
                  )}
                </>
              )}
            </>
          )}
        </div>
      </Card>
    );
  };

  const renderByPrinter = () => {
    const limitedIds = new Set(limitedGroups.map((g) => g.id));
    // Who sees every printer whatever the groups below say
    const openUsers = (users ?? []).filter(
      (u) => u.is_active && !u.is_admin && !u.groups.some((g) => limitedIds.has(g.id))
    );
    const rows = focusPrinterId ? allPrinters.filter((p) => p.id === focusPrinterId) : baseFiltered;

    return (
      <div className="space-y-3">
        <Card>
          <div className="p-4 space-y-1 text-sm">
            <p className="text-bambu-gray">{t('printerAccess.alsoVisible')}</p>
            {users && (
              <p className={openUsers.length > 0 ? 'text-yellow-700 dark:text-yellow-400' : 'text-bambu-gray'}>
                {openUsers.length > 0
                  ? t('printerAccess.openUsers', {
                      names:
                        openUsers
                          .slice(0, 10)
                          .map((u) => u.username)
                          .join(', ') + (openUsers.length > 10 ? ` +${openUsers.length - 10}` : ''),
                    })
                  : t('printerAccess.noOpenUsers')}
              </p>
            )}
          </div>
        </Card>

        {limitedGroups.length === 0 && (
          <p className="text-sm text-bambu-gray">{t('printerAccess.noLimitedGroups')}</p>
        )}

        {focusPrinterId ? (
          <div className="flex items-center gap-2 text-sm text-bambu-gray">
            <span>{t('printerAccess.showingOne')}</span>
            <Button size="sm" variant="ghost" onClick={() => setParams({ printer: null })}>
              {t('printerAccess.showAll')}
            </Button>
          </div>
        ) : (
          filterBar(false)
        )}

        {rows.length === 0 ? (
          <p className="text-sm text-bambu-gray py-4 text-center">
            {allPrinters.length === 0 ? t('printerAccess.noPrinters') : t('printerAccess.noPrintersMatch')}
          </p>
        ) : (
          <Card>
            <div className="divide-y divide-bambu-dark-tertiary">
              {rows.map((p) => {
                const reaching = limitedGroups
                  .map((g) => ({ group: g, reach: reachOf(draftFor(g), p) }))
                  .filter((r) => r.reach !== null);
                const addable = limitedGroups.filter((g) => reachOf(draftFor(g), p) === null);
                return (
                  <div key={p.id} className="flex flex-col md:flex-row md:items-center gap-2 md:gap-4 px-4 py-3">
                    <div className="md:w-64 shrink-0 min-w-0">
                      <div className="flex items-center gap-2">
                        <PrinterIcon className="w-4 h-4 text-bambu-gray shrink-0" />
                        <span className="text-sm text-white truncate">{p.name}</span>
                      </div>
                      <div className="text-xs text-bambu-gray truncate pl-6">
                        {[p.model, p.location || t('printerAccess.noLocation')].filter(Boolean).join(' · ')}
                      </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5 flex-1 min-w-0">
                      {reaching.map(({ group: g, reach }) =>
                        reach === 'location' ? (
                          <button
                            key={g.id}
                            type="button"
                            onClick={() => setParams({ view: null, group: String(g.id), printer: null })}
                            title={t('printerAccess.viaLocationTitle', { location: p.location })}
                            className="flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-bambu-green/10 text-bambu-green border border-bambu-green/30"
                          >
                            <MapPin className="w-3 h-3" />
                            {g.name}
                          </button>
                        ) : (
                          <span
                            key={g.id}
                            className="flex items-center gap-1 pl-2 pr-1 py-0.5 rounded-full text-xs bg-bambu-green/20 text-bambu-green"
                          >
                            {g.name}
                            <button
                              type="button"
                              onClick={() =>
                                updateDraft(g, (d) => ({ ...d, printerIds: d.printerIds.filter((id) => id !== p.id) }))
                              }
                              aria-label={t('printerAccess.removeGroup', { group: g.name })}
                              className="p-0.5 rounded-full hover:bg-bambu-green/30"
                            >
                              <X className="w-3 h-3" />
                            </button>
                          </span>
                        )
                      )}
                      {reaching.length === 0 && limitedGroups.length > 0 && (
                        <span className="text-xs text-bambu-gray">{t('printerAccess.noLimitedGroupReaches')}</span>
                      )}
                    </div>
                    {addable.length > 0 && (
                      <select
                        value=""
                        onChange={(e) => {
                          const g = limitedGroups.find((x) => x.id === Number(e.target.value));
                          if (g) updateDraft(g, (d) => ({ ...d, printerIds: [...d.printerIds, p.id] }));
                        }}
                        aria-label={t('printerAccess.addGroup')}
                        className={`${inputClass} md:w-48 shrink-0`}
                      >
                        <option value="">{t('printerAccess.addGroup')}</option>
                        {addable.map((g) => (
                          <option key={g.id} value={g.id}>
                            {g.name}
                          </option>
                        ))}
                      </select>
                    )}
                  </div>
                );
              })}
            </div>
          </Card>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-4 max-w-6xl" id="card-printer-access">
      <p className="text-sm text-bambu-gray">{t('printerAccess.intro')}</p>

      <div className="flex gap-1 p-1 rounded-lg bg-bambu-dark-secondary border border-bambu-dark-tertiary w-fit">
        {(['groups', 'printers'] as const).map((v) => (
          <button
            key={v}
            type="button"
            onClick={() => setParams({ view: v === 'groups' ? null : 'printers' })}
            className={`px-3 py-1.5 text-sm rounded-md transition-colors ${
              view === v ? 'bg-bambu-green/20 text-bambu-green' : 'text-bambu-gray hover:text-white'
            }`}
          >
            {v === 'groups' ? t('printerAccess.byGroup') : t('printerAccess.byPrinter')}
          </button>
        ))}
      </div>

      {view === 'groups' ? (
        <div className="flex flex-col lg:flex-row gap-4 items-start">
          {renderGroupList()}
          {renderGroupEditor()}
        </div>
      ) : (
        renderByPrinter()
      )}

      {isDirty && (
        <div className="sticky bottom-0 z-20 flex flex-wrap items-center justify-between gap-3 px-4 py-3 rounded-xl bg-bambu-dark-secondary border border-bambu-dark-tertiary shadow-lg">
          <span className="text-sm text-white">
            {t('printerAccess.changedGroups', { names: dirtyGroups.map((g) => g.name).join(', ') })}
          </span>
          <div className="flex items-center gap-2">
            <Button variant="secondary" onClick={() => setDrafts({})} disabled={saveMutation.isPending}>
              {t('printerAccess.discard')}
            </Button>
            <Button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
              {saveMutation.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Save className="w-4 h-4" />
              )}
              {t('common.save')}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

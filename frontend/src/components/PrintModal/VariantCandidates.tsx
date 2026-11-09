import { useEffect, useMemo } from 'react';
import { useQueries } from '@tanstack/react-query';
import { ArrowDown, ArrowUp, Layers, Printer as PrinterIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { api } from '../../api/client';

/** One sliced file of the list: an alternative of a cross-model job (#671), or one of the separate jobs (#3462). */
export interface VariantCandidate {
  id: number;
  filename: string;
  sliced_for_model: string | null;
}

interface VariantCandidatesProps {
  candidates: VariantCandidate[];
  onReorder: (next: VariantCandidate[]) => void;
  /** file id -> chosen plate, for the multi-plate candidates only. */
  plateByFile: Record<number, number | null>;
  onPlateChange: (fileId: number, plateId: number | null) => void;
  /** Told whether every file's plate list has answered and every multi-plate
   *  file has a plate chosen; until then a submit would send no plate. */
  onPlatesReadyChange?: (ready: boolean) => void;
  /** Show the set without offering to change it — the edit-queue-item case,
   *  where reordering would need a variant-level API that doesn't exist. */
  readOnly?: boolean;
  /** Replaces the help line under the heading when read-only. */
  readOnlyNote?: string;
  /** Replaces the heading (default: the cross-model alternatives title). */
  title?: string;
  /** Replaces the help line (default: the cross-model alternatives help).
   *  An empty string drops the line, for when something above the list
   *  already explains it (the mode toggle, #3462). */
  help?: string;
}

/**
 * The ordered list of several sliced files, in one of two uses: the candidates
 * of a cross-model print (#671), or the queue order of separate jobs (#3462).
 *
 * Only two things are configured per file: its order, and — when the file
 * holds more than one plate — which plate to run. Everything else on the modal
 * (print options, schedule, and for alternatives the filament overrides) stays
 * shared. For alternatives that is already how model-based assignment works:
 * the printer is unknown at queue time, so there is nothing per-machine to
 * configure. Separate jobs do know the printer, but the modal's filament panels
 * describe the first file only, so they are hidden and no job carries them.
 * The AMS mapping in particular is deliberately absent in both uses — the
 * scheduler computes it against the printer and the file each item runs,
 * exactly as it does for a single-model job.
 *
 * For alternatives, order is the answer to "which would you rather have when
 * both are free", so it is explicit rather than left to whichever printer the
 * matcher saw first. For separate jobs it is the order the items join the
 * queue. Move buttons instead of drag: the list is two or three rows, and
 * buttons work from the keyboard without a drag-and-drop dependency.
 */
export function VariantCandidates({
  candidates,
  onReorder,
  plateByFile,
  onPlateChange,
  onPlatesReadyChange,
  readOnly = false,
  readOnlyNote,
  title,
  help,
}: VariantCandidatesProps) {
  const { t } = useTranslation();

  const plateQueries = useQueries({
    // Read-only mode shows a job that is already queued — its plates were
    // chosen when it was created, so there is nothing to fetch or offer.
    queries: readOnly
      ? []
      : candidates.map((c) => ({
          queryKey: ['library-file-plates', c.id],
          queryFn: () => api.getLibraryFilePlates(c.id),
          staleTime: 60_000,
        })),
  });

  const platesByFile = useMemo(() => {
    const out: Record<number, { index: number; name: string | null }[]> = {};
    candidates.forEach((c, i) => {
      const data = plateQueries[i]?.data;
      if (data?.is_multi_plate && data.plates.length > 1) {
        out[c.id] = data.plates.map((p) => ({ index: p.index, name: p.name }));
      }
    });
    return out;
  }, [candidates, plateQueries]);

  // The dropdown shows the first plate when none is chosen; make that the
  // choice too, or the job is queued with no plate (all plates' filaments and
  // the whole file's print time) while the dialog says "Plate 1".
  useEffect(() => {
    if (readOnly) return;
    for (const [fileId, plates] of Object.entries(platesByFile)) {
      const id = Number(fileId);
      if (plateByFile[id] == null && plates.length > 0) onPlateChange(id, plates[0].index);
    }
  }, [platesByFile, plateByFile, onPlateChange, readOnly]);

  // A list still loading has no plate to send yet (#3471 review): a quick
  // Print would queue the file with plate_id null, the whole file's time and
  // cost, and plate 1 printing. A list that failed to load counts as answered:
  // there is no plate to offer, and the file goes without one, as before.
  const platesPending = plateQueries.some((q) => q.isPending);
  const platesChosen = Object.keys(platesByFile).every((fileId) => plateByFile[Number(fileId)] != null);
  const platesReady = !readOnly && !platesPending && platesChosen;
  useEffect(() => {
    onPlatesReadyChange?.(platesReady);
  }, [platesReady, onPlatesReadyChange]);

  const defaultHelp = help ?? t('printModal.variants.help');
  const helpText = readOnly ? (readOnlyNote ?? defaultHelp) : defaultHelp;

  const move = (from: number, to: number) => {
    if (to < 0 || to >= candidates.length) return;
    const next = [...candidates];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    onReorder(next);
  };

  return (
    <div className="mb-4">
      <div className="flex items-center gap-2 mb-2">
        <PrinterIcon className="w-4 h-4 text-bambu-gray" />
        <span className="text-sm text-bambu-gray">{title ?? t('printModal.variants.title')}</span>
      </div>
      {helpText !== '' && <p className="text-xs text-bambu-gray mb-2">{helpText}</p>}

      <div className="space-y-2">
        {candidates.map((candidate, index) => {
          const plates = platesByFile[candidate.id];
          // Shown exactly as the 3MF declares it. The backend normalizes when it
          // resolves the candidate; echoing its own words here avoids a second,
          // possibly disagreeing, normalizer in the browser.
          const model = candidate.sliced_for_model;
          return (
            <div
              key={candidate.id}
              className="flex flex-wrap items-center gap-2 rounded border border-bambu-dark-tertiary p-2"
            >
              <span className="text-xs font-mono text-bambu-gray w-5 shrink-0">{index + 1}.</span>
              <span className="px-2 py-0.5 rounded-full bg-bambu-green/10 text-bambu-green text-xs shrink-0">
                {model || t('printModal.variants.unknownModel')}
              </span>
              <span className="text-sm text-white truncate min-w-0 flex-1" title={candidate.filename}>
                {candidate.filename}
              </span>

              {plates && (
                <label className="flex items-center gap-1 text-xs text-bambu-gray shrink-0">
                  <Layers className="w-3 h-3" />
                  <select
                    className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded px-1 py-0.5 text-xs text-white"
                    value={plateByFile[candidate.id] ?? plates[0].index}
                    onChange={(e) => onPlateChange(candidate.id, Number(e.target.value))}
                    aria-label={t('printModal.variants.plateFor', { filename: candidate.filename })}
                  >
                    {plates.map((p) => (
                      <option key={p.index} value={p.index}>
                        {p.name || t('printModal.plateN', { n: p.index })}
                      </option>
                    ))}
                  </select>
                </label>
              )}

              {!readOnly && (
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    type="button"
                    onClick={() => move(index, index - 1)}
                    disabled={index === 0}
                    className="p-1 rounded text-bambu-gray hover:text-white disabled:opacity-30"
                    aria-label={t('printModal.variants.moveUp')}
                  >
                    <ArrowUp className="w-3.5 h-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => move(index, index + 1)}
                    disabled={index === candidates.length - 1}
                    className="p-1 rounded text-bambu-gray hover:text-white disabled:opacity-30"
                    aria-label={t('printModal.variants.moveDown')}
                  >
                    <ArrowDown className="w-3.5 h-3.5" />
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

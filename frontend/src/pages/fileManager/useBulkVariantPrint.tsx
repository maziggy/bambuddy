import { useCallback, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { api, type LibraryFileListItem } from '../../api/client';
import { isSlicedLibraryFile } from '../../utils/libraryFiles';
import { groupRowsOf, type GroupRow } from './variantGroups';
import { planBulkPrint, promotePartialGroups, type BulkPrintPlan } from './variantSelection';

// What the print dialog shows when it is for a group or a single version
// (#3628) rather than for the selection.
interface PrintOverride {
  ids: number[];
  variantFiles: { id: number; filename: string; sliced_for_model: string | null }[] | undefined;
}

type PrintQueue = { groups: GroupRow[]; loose: LibraryFileListItem[] };

// Printing version groups from the File Manager (#3628, #3462): one group, one
// version, or a bulk Print whose fully selected groups are one job each and
// whose other files are printed as before. The page owns `printFile` (the
// dialog's trigger); this hook owns what decides how the dialog is filled and
// what follows a finished job.
export function useBulkVariantPrint({
  selectedFiles,
  setSelectedFiles,
  selectableFiles,
  selectedSlicedFiles,
  setPrintFile,
}: {
  selectedFiles: number[];
  setSelectedFiles: React.Dispatch<React.SetStateAction<number[]>>;
  selectableFiles: ReadonlyMap<number, LibraryFileListItem>;
  selectedSlicedFiles: LibraryFileListItem[];
  setPrintFile: (file: LibraryFileListItem | null) => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [printOverride, setPrintOverride] = useState<PrintOverride | null>(null);
  // Group jobs still to open after the one on screen, then the loose files.
  const [printQueue, setPrintQueue] = useState<PrintQueue | null>(null);
  // Set when onSuccess has just opened the next job, so the close that follows
  // it does not tear the new one down.
  const advanced = useRef(false);
  const [partialPlan, setPartialPlan] = useState<BulkPrintPlan | null>(null);

  // Groups come from every file a tick can point at, not from the rows on
  // screen, so a group ticked in an earlier column counts too.
  const groupRows = useMemo(() => groupRowsOf(selectableFiles.values()), [selectableFiles]);
  const plan = useMemo(
    () => planBulkPrint(selectedFiles, groupRows, selectableFiles),
    [selectedFiles, groupRows, selectableFiles],
  );

  // One version on its own, without offering the rest of its group.
  const printVersion = useCallback(
    (file: LibraryFileListItem) => {
      setPrintOverride({ ids: [file.id], variantFiles: undefined });
      setPrintFile(file);
    },
    [setPrintFile],
  );

  // The whole group as one job with printer alternatives. Each version's model
  // is the one the server dispatches to (its durable override, else what the
  // file declares), which the list does not carry but the group does; the
  // file's own declared model stands in when that cannot be fetched.
  const printVariantGroup = useCallback(
    async (row: GroupRow) => {
      const sliced = row.members.filter(isSlicedLibraryFile);
      if (sliced.length === 0) return;
      const targets = new Map<number, string | null>();
      try {
        const group = await queryClient.fetchQuery({
          queryKey: ['variant-group', row.id],
          queryFn: () => api.getVariantGroup(row.id),
          staleTime: 0,
        });
        for (const m of group.members) targets.set(m.library_file_id, m.target_model);
      } catch {
        // Fall back to each file's declared model.
      }
      setPrintOverride({
        ids: sliced.map((f) => f.id),
        variantFiles: sliced.map((f) => ({
          id: f.id,
          filename: f.filename,
          sliced_for_model: targets.get(f.id) ?? f.sliced_for_model,
        })),
      });
      setPrintFile(sliced[0]);
    },
    [queryClient, setPrintFile],
  );

  // The files outside any full group: a lone grouped version prints on its
  // own, anything else is the selection as the dialog always took it.
  const openLoose = (files: LibraryFileListItem[]) => {
    if (files.length === 1 && files[0].variant_group_id != null) printVersion(files[0]);
    else setPrintFile(files[0]);
  };

  const start = (next: BulkPrintPlan) => {
    if (next.fullGroups.length > 0) {
      const [first, ...rest] = next.fullGroups;
      setPrintQueue({ groups: rest, loose: next.looseFiles });
      void printVariantGroup(first);
      return;
    }
    setPrintQueue(null);
    openLoose(next.looseFiles.length > 0 ? next.looseFiles : selectedSlicedFiles);
  };

  const handleBulkPrint = () => {
    if (plan.partialGroups.length > 0) setPartialPlan(plan);
    else start(plan);
  };

  const confirmPartial = () => {
    if (!partialPlan) return;
    const promoted = promotePartialGroups(partialPlan);
    setPartialPlan(null);
    start(promoted);
  };

  // "Print selection only": the fully selected groups stay one job each, and
  // the partly selected ones print just the versions that are ticked.
  const printSelectionOnly = () => {
    if (!partialPlan) return;
    const current = partialPlan;
    setPartialPlan(null);
    start(current);
  };

  // The dialog closed. True when that only handed over to the next job.
  const consumeAdvance = () => {
    if (!advanced.current) return false;
    advanced.current = false;
    return true;
  };

  const reset = () => {
    setPrintOverride(null);
    setPrintQueue(null);
  };

  // A job was queued. True when it was a group or a version, which leaves
  // only what was printed out of the selection and goes on with the next job
  // of a bulk Print; false when it was the plain selection.
  const completeJob = (): boolean => {
    const finished = printOverride;
    setPrintOverride(null);
    if (!finished) return false;
    setSelectedFiles((prev) => prev.filter((id) => !finished.ids.includes(id)));
    const queue = printQueue;
    if (queue && queue.groups.length > 0) {
      const [next, ...rest] = queue.groups;
      setPrintQueue({ ...queue, groups: rest });
      void printVariantGroup(next);
      advanced.current = true;
    } else if (queue) {
      setPrintQueue(null);
      if (queue.loose.length > 0) {
        openLoose(queue.loose);
        advanced.current = true;
      }
    }
    return true;
  };

  // The Print button's text: one job says Print, several count what they are.
  const groups = plan.fullGroups.length;
  const loose = plan.looseFiles.length;
  let printLabel = t('common.print');
  if (groups > 0 && loose > 0) {
    printLabel = t('fileManager.variants.printGroupsAndFiles', {
      groups: t('fileManager.variants.groupCount', { count: groups }),
      files: t('fileManager.variants.fileCount', { count: loose }),
    });
  } else if (groups > 1) {
    printLabel = t('fileManager.variants.printJobs', { count: groups });
  } else if (groups === 0 && selectedSlicedFiles.length > 1) {
    printLabel = t('fileManager.printFiles', { count: selectedSlicedFiles.length });
  }

  return {
    printOverride,
    partialPlan,
    dismissPartial: () => setPartialPlan(null),
    printLabel,
    printVariantGroup,
    printVersion,
    handleBulkPrint,
    confirmPartial,
    printSelectionOnly,
    consumeAdvance,
    reset,
    completeJob,
  };
}

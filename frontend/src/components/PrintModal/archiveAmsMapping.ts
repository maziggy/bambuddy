/**
 * Reading the archive's saved slicer AMS-slot pick back out of `extra_data`.
 *
 * A virtual printer with "Save AMS mapping" on stores the slicer's own
 * live-resolved tray choice on the archive as
 * `extra_data.slicer_ams_mapping = { mapping, printer_id }` (written by
 * `ArchiveService.archive_print`). The tray IDs in `mapping` are global tray
 * IDs, which only mean something against the AMS layout of the one printer
 * they were resolved against — slot 3 on another printer can hold a completely
 * different spool. `printer_id` records which printer that was.
 *
 * Lives here rather than inline in the modal so the printer-scoping rule can
 * be tested on its own: it is the only thing standing between a saved mapping
 * and the wrong physical spool.
 */

/** Shape of `extra_data.slicer_ams_mapping`. Every field optional — this is
 *  free-form JSON off the wire, and older archives predate the key entirely. */
export interface SavedSlicerAmsMapping {
  mapping?: number[];
  printer_id?: number;
}

/**
 * The saved mapping, but only when it is safe to apply to `printerId`.
 *
 * Returns `undefined` — meaning "no saved mapping in scope, behave as before" —
 * when the archive has none, when the stored value is malformed, when no
 * printer is selected yet, or when the selected printer is not the one the
 * mapping was resolved against.
 */
export function resolveArchiveSlicerAmsMapping(
  extraData: Record<string, unknown> | null | undefined,
  printerId: number | null | undefined,
): number[] | undefined {
  // No printer selected means there is nothing to compare against. Bailing
  // here also stops `undefined === undefined` from reading as a match below.
  if (printerId == null) return undefined;

  const saved = extraData?.slicer_ams_mapping as SavedSlicerAmsMapping | undefined;
  if (!saved || typeof saved !== 'object') return undefined;
  if (saved.printer_id !== printerId) return undefined;
  if (!Array.isArray(saved.mapping) || saved.mapping.length === 0) return undefined;

  return saved.mapping;
}

/**
 * The slicer's saved pick for the print being set up, if it applies to `printerId`.
 *
 * A virtual printer's queue job prints a File Manager file and has no archive
 * until it prints, so while editing one its own kept pick (`slicer_ams_mapping`,
 * for the job's printer) is the one. Otherwise the archive's, as
 * `resolveArchiveSlicerAmsMapping`; a library file without a job has none.
 */
export function resolveSlicerAmsMapping({
  queueItem,
  isLibraryFile,
  archiveExtraData,
  printerId,
}: {
  queueItem?: { printer_id: number | null; slicer_ams_mapping?: number[] | null } | null;
  isLibraryFile: boolean;
  archiveExtraData: Record<string, unknown> | null | undefined;
  printerId: number | null | undefined;
}): number[] | undefined {
  if (queueItem?.slicer_ams_mapping?.length && printerId != null && queueItem.printer_id === printerId) {
    return queueItem.slicer_ams_mapping;
  }
  return isLibraryFile ? undefined : resolveArchiveSlicerAmsMapping(archiveExtraData, printerId);
}

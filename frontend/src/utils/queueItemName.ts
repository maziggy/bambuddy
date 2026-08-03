/**
 * Display name for a queue item, wherever one is shown.
 *
 * Every surface used to inline the same fallback chain, which produced
 * `File #null` for a cross-model item (#671): those deliberately hold neither
 * `archive_id` nor `library_file_id` until dispatch resolves a candidate, so
 * that the ON DELETE CASCADE on `library_file_id` can't destroy the whole job
 * when one alternative is deleted. Nothing to point at is the design working;
 * the label just had nowhere to look.
 */

interface NameableQueueItem {
  archive_name?: string | null;
  library_file_name?: string | null;
  archive_id?: number | null;
  library_file_id?: number | null;
  variants?: Array<{ filename: string }>;
}

/**
 * @param item      the queue item to name
 * @param moreLabel formats the "+N more" suffix for a cross-model item; pass
 *                  the caller's `t` binding so the count stays translated.
 *                  Omitted in compact surfaces that only have room for a name.
 */
export function queueItemDisplayName(
  item: NameableQueueItem,
  moreLabel?: (count: number) => string,
): string {
  if (item.archive_name) return item.archive_name;
  if (item.library_file_name) return item.library_file_name;

  // Cross-model item: name it after the candidate the user put first — the one
  // the scheduler will try first — and say how many others are behind it.
  const variants = item.variants ?? [];
  if (variants.length > 0) {
    const first = variants[0].filename;
    const others = variants.length - 1;
    if (others > 0 && moreLabel) return `${first} ${moreLabel(others)}`;
    return first;
  }

  return `File #${item.archive_id ?? item.library_file_id}`;
}

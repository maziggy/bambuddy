import { api } from '../api/client';

/**
 * Thumbnail for a queue item, wherever one is shown: the queued plate's when
 * the item names one, else the file's (plate 1). The timeline and history rows
 * used the file's for every plate, so two plates of one file looked alike.
 */
interface ThumbnailQueueItem {
  archive_id?: number | null;
  archive_thumbnail?: string | null;
  library_file_id?: number | null;
  library_file_thumbnail?: string | null;
  plate_id?: number | null;
}

export function queueItemThumbnailUrl(item: ThumbnailQueueItem): string | null {
  if (item.archive_thumbnail && item.archive_id != null) {
    return item.plate_id != null
      ? api.getArchivePlateThumbnail(item.archive_id, item.plate_id)
      : api.getArchiveThumbnail(item.archive_id);
  }
  if (item.library_file_thumbnail && item.library_file_id != null) {
    return item.plate_id != null
      ? api.getLibraryFilePlateThumbnail(item.library_file_id, item.plate_id)
      : api.getLibraryFileThumbnailUrl(item.library_file_id);
  }
  return null;
}

/**
 * Shared React Query key for the library-tag catalog (#1268).
 *
 * Lives in its own module so the consumers — LibraryTagsModal,
 * BulkTagsPickerModal, FileManagerPage — can invalidate together without
 * importing component files from each other (which breaks Vite Fast Refresh
 * when a single file exports both a constant and a component).
 */
export const libraryTagsQueryKey = ['library-tags'] as const;

import { isApiSliceableFilename, isSliceableFilename } from './slicer';

/**
 * Is a library file sliced — does it carry printer-executable G-code?
 *
 * The name alone is not evidence (#2993). A plate exported from Studio, or a
 * print dispatched through the cloud, is a fully sliced 3MF called `Foo.3mf`,
 * and deciding from the extension filed those as source-only projects with no
 * Print button — while the Archives card, which looks inside the zip, showed
 * the same file's green GCODE badge. The backend now classifies on content, so
 * `file_type` answers where the name cannot.
 *
 * Either signal is enough, which mirrors `classify_file_type` on the backend:
 * it returns `gcode.3mf` for a `.gcode.3mf` name without opening the file at
 * all, and only consults the zip when the name has not already settled it. A
 * row stored before the backfill therefore keeps its Print button on the
 * strength of its name.
 */
export function isSlicedLibraryFile(file: {
  filename: string;
  file_type?: string | null;
}): boolean {
  const fileType = (file.file_type || '').toLowerCase();
  if (fileType === 'gcode' || fileType === 'gcode.3mf') return true;
  const lower = (file.filename || '').toLowerCase();
  return lower.endsWith('.gcode') || lower.endsWith('.gcode.3mf');
}

/**
 * Is a library file something a slicer can take as *input*?
 *
 * `isSliceableFilename` already refuses a `.gcode` / `.gcode.3mf` name — the
 * point being that a sliced file is an output, not an input. That refusal was
 * as name-bound as the Print gate it sits beside (#2993), so once a sliced
 * `Foo.3mf` correctly gained a Print button it would have been offered a
 * Slice one as well: re-slicing its own G-code.
 */
export function isSliceableLibraryFile(
  file: { filename: string; file_type?: string | null },
  useSlicerApi: boolean,
): boolean {
  if (isSlicedLibraryFile(file)) return false;
  return useSlicerApi ? isApiSliceableFilename(file.filename) : isSliceableFilename(file.filename);
}

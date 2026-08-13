/**
 * Utility for opening files in slicer applications
 *
 * Protocol handler URL formats (from BambuStudio/OrcaSlicer source code):
 *
 * Bambu Studio has TWO separate URL handlers:
 *   1. post_init() [Windows/Linux CLI args]: bambustudio://open?file=<URL>
 *      - Checks: starts_with("bambustudio://open")
 *      - Calls url_decode(), then split_str(url, "file=")
 *   2. MacOpenURL() [macOS Apple Events]: bambustudioopen://<encoded-URL>
 *      - Checks: starts_with("bambustudioopen://")
 *      - Strips prefix, then url_decode()
 *
 * OrcaSlicer Downloader accepts both formats via regex:
 *   - (orcaslicer|bambustudio|...)://open?file=<URL>
 *   - bambustudioopen://<URL>
 *
 * Key insight: every form needs encodeURIComponent on the file URL, because
 * the slicer calls url_decode() on the received query (post_init calls
 * url_decode then split_str; MacOpenURL strips the prefix then url_decode;
 * OrcaSlicer's Downloader regex-extracts then url_decode). Without encoding,
 * any already-percent-encoded character in the download URL (most commonly
 * %20 in filenames with spaces) decodes to a literal space and the slicer's
 * subsequent HTTP fetch fails with a 0-byte body or 404. See issue #1059.
 */

export type SlicerType = 'bambu_studio' | 'orcaslicer';

type Platform = 'windows' | 'macos' | 'linux' | 'unknown';

/**
 * Resolve the desktop "Open in Slicer" target. Prefers an explicit
 * `open_in_slicer` override (#1329), then falls back to the API slicer's
 * `preferred_slicer`, then Bambu Studio. This is ONLY the URI-handoff target;
 * the in-app SliceModal keeps using `preferred_slicer` for the sidecar.
 */
export function resolveDesktopSlicer(
  openInSlicer?: SlicerType | null,
  preferredSlicer?: SlicerType,
): SlicerType {
  return openInSlicer ?? preferredSlicer ?? 'bambu_studio';
}

/**
 * File types a slicer can be handed — both by the desktop URI handler and by
 * the in-app sidecar. Source geometry only: a sliced file is an output, and
 * neither slicer has anything to do with one.
 *
 * Lives here rather than beside either caller because both the File Manager
 * (which has a filename) and the 3D preview (which has a `LibraryFile.file_type`)
 * decide the same thing about the same file. They used to hold separate lists,
 * and the two disagreed — a card menu offered a desktop handoff for an STL
 * whose own 3D preview showed "Open in Slicer" greyed out.
 */
export const SLICEABLE_FILE_TYPES = ['3mf', 'stl', 'step', 'stp'] as const;

/**
 * The subset the *sidecar* can slice.
 *
 * The desktop slicers open a STEP happily; their command-line interfaces do
 * not. OrcaSlicer 2.4.2 and Bambu Studio 02.07.01.62 both answer one with
 * "Unknown file format. Input file must have .stl, .obj, .amf(.xml) extension."
 * So a STEP still gets an "Open in Slicer" handoff, and no longer gets a
 * "Slice" button that could only ever fail.
 */
export const API_SLICEABLE_FILE_TYPES = ['3mf', 'stl'] as const;

/**
 * Does a `LibraryFile.file_type` name a sliceable source file?
 *
 * The backend stores compound extensions whole — a sliced 3MF classifies as
 * `gcode.3mf`, not `3mf` (`classify_file_type` in `api/routes/library.py`) — so
 * membership alone is enough to exclude sliced output here.
 */
export function isSliceableFileType(fileType?: string | null): boolean {
  const normalized = (fileType || '').toLowerCase();
  return (SLICEABLE_FILE_TYPES as readonly string[]).includes(normalized);
}

/**
 * Does a filename name a sliceable source file?
 *
 * Checked against the name rather than a stored type, so the compound
 * extensions have to be ruled out explicitly: `.gcode.3mf` ends with `.3mf`.
 */
export function isSliceableFilename(filename: string): boolean {
  const lower = filename.toLowerCase();
  if (lower.endsWith('.gcode') || lower.endsWith('.gcode.3mf')) return false;
  return SLICEABLE_FILE_TYPES.some((ext) => lower.endsWith(`.${ext}`));
}

/**
 * Does a filename name something the slicer *sidecar* can slice?
 *
 * Narrower than `isSliceableFilename` by exactly STEP — see
 * `API_SLICEABLE_FILE_TYPES`. Use this wherever the action posts to
 * `/library/files/{id}/slice`; use the wider one for the desktop handoff.
 */
export function isApiSliceableFilename(filename: string): boolean {
  const lower = filename.toLowerCase();
  if (lower.endsWith('.gcode') || lower.endsWith('.gcode.3mf')) return false;
  return API_SLICEABLE_FILE_TYPES.some((ext) => lower.endsWith(`.${ext}`));
}

/**
 * Detect the user's operating system
 */
export function detectPlatform(): Platform {
  const userAgent = navigator.userAgent.toLowerCase();
  const platform = navigator.platform?.toLowerCase() || '';

  if (userAgent.includes('win') || platform.includes('win')) {
    return 'windows';
  }
  if (userAgent.includes('mac') || platform.includes('mac')) {
    return 'macos';
  }
  if (userAgent.includes('linux') || platform.includes('linux')) {
    return 'linux';
  }
  return 'unknown';
}

/**
 * Open a URL in the specified slicer application.
 * @param downloadUrl - The URL to the file to open
 * @param slicer - Which slicer to use (defaults to bambu_studio)
 */
export function openInSlicer(downloadUrl: string, slicer: SlicerType = 'bambu_studio'): void {
  let url: string;

  const encoded = encodeURIComponent(downloadUrl);
  if (slicer === 'orcaslicer') {
    url = `orcaslicer://open?file=${encoded}`;
  } else {
    const platform = detectPlatform();
    if (platform === 'macos') {
      // macOS only: bambustudioopen scheme via MacOpenURL() callback.
      url = `bambustudioopen://${encoded}`;
    } else {
      // Windows/Linux: bambustudio://open?file= via post_init() CLI args.
      // IMPORTANT: On Linux, BS only handles "bambustudio://open" prefix —
      // it does NOT process "bambustudioopen://" (that's macOS-only).
      url = `bambustudio://open?file=${encoded}`;
    }
  }

  // Use a temporary <a> element to trigger the protocol handler.
  // This avoids navigating away from the page (unlike window.location.href).
  const link = document.createElement('a');
  link.href = url;
  link.style.display = 'none';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

/**
 * Build a full download URL for a file
 * @param path - The API path (e.g., from api.getArchiveForSlicer())
 */
export function buildDownloadUrl(path: string): string {
  return `${window.location.origin}${path}`;
}

/**
 * Convenience function to open an archive in the slicer
 * @param path - The API path to the archive
 * @param slicer - Which slicer to use (defaults to bambu_studio)
 */
export function openArchiveInSlicer(path: string, slicer: SlicerType = 'bambu_studio'): void {
  const downloadUrl = buildDownloadUrl(path);
  openInSlicer(downloadUrl, slicer);
}

/**
 * Does a `LibraryFile.file_type` name something the sidecar can slice?
 *
 * The `isSliceableFileType` counterpart, narrowed to the sidecar's formats.
 */
export function isApiSliceableFileType(fileType?: string | null): boolean {
  const normalized = (fileType || '').toLowerCase();
  return (API_SLICEABLE_FILE_TYPES as readonly string[]).includes(normalized);
}

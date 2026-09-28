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
 * What each desktop slicer's protocol handler will actually load.
 *
 * These are not the formats the two applications can open — both take an STL
 * from File > Import perfectly well. They are what survives the *URL handoff*,
 * which is a narrower thing, and the two slicers disagree about it (#3029).
 *
 * Bambu Studio routes every `bambustudio://open?file=` and `bambustudioopen://`
 * URL into `Plater::import_model_id`, which refuses any filename that is not
 * `.3mf` before it makes the HTTP request at all — "Download failed, unknown
 * file format." Handing it an STL could only ever fail, and the message names
 * the format rather than the handoff, so the failure reads as a broken file.
 *
 * OrcaSlicer sends only MakerWorld links and `bambustudioopen://` down that
 * same 3MF-only path (`Downloader::start_download`). Everything else — which is
 * what we emit for it, `orcaslicer://open?file=` against our own host — goes to
 * its generic downloader, which has no extension check at all. So STL and STEP
 * work there.
 *
 * Source geometry only either way: a sliced file is an output, and neither
 * slicer has anything to do with one.
 *
 * This lives here rather than beside any caller because the File Manager (which
 * has a filename) and the 3D preview (which has a `LibraryFile.file_type`)
 * decide the same thing about the same file. They used to hold separate lists,
 * and the two disagreed — a card menu offered a desktop handoff for an STL
 * whose own 3D preview showed "Open in Slicer" greyed out.
 */
export const DESKTOP_SLICEABLE_FILE_TYPES: Record<SlicerType, readonly string[]> = {
  bambu_studio: ['3mf'],
  orcaslicer: ['3mf', 'stl', 'step', 'stp'],
};

/**
 * The formats `slicer` accepts over the handoff.
 *
 * Unknown values fall back to Bambu Studio's list rather than throwing, because
 * that is what the handoff itself does: `openInSlicer` treats anything that is
 * not exactly `orcaslicer` as Bambu Studio. `settings.open_in_slicer` comes off
 * the API unvalidated, so the two have to agree on that.
 */
function desktopFormats(slicer: SlicerType): readonly string[] {
  return DESKTOP_SLICEABLE_FILE_TYPES[slicer] ?? DESKTOP_SLICEABLE_FILE_TYPES.bambu_studio;
}

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
 * Can `slicer` be handed this `LibraryFile.file_type` over the protocol handler?
 *
 * The backend stores compound extensions whole — a sliced 3MF classifies as
 * `gcode.3mf`, not `3mf` (`classify_file_type` in `api/routes/library.py`) — so
 * membership alone is enough to exclude sliced output here.
 *
 * The slicer is a required argument on purpose: the answer genuinely differs
 * between the two, and a default would quietly reintroduce the STL handoff that
 * Bambu Studio cannot honour.
 */
export function isSliceableFileType(fileType: string | null | undefined, slicer: SlicerType): boolean {
  const normalized = (fileType || '').toLowerCase();
  return desktopFormats(slicer).includes(normalized);
}

/**
 * Can `slicer` be handed this filename over the protocol handler?
 *
 * Checked against the name rather than a stored type, so the compound
 * extensions have to be ruled out explicitly: `.gcode.3mf` ends with `.3mf`.
 */
export function isSliceableFilename(filename: string, slicer: SlicerType): boolean {
  const lower = filename.toLowerCase();
  if (lower.endsWith('.gcode') || lower.endsWith('.gcode.3mf')) return false;
  return desktopFormats(slicer).some((ext) => lower.endsWith(`.${ext}`));
}

/**
 * Does a filename name something the slicer *sidecar* can slice?
 *
 * Narrower than OrcaSlicer's handoff list by exactly STEP — see
 * `API_SLICEABLE_FILE_TYPES`. Use this wherever the action posts to
 * `/library/files/{id}/slice`; use `isSliceableFilename` for the desktop
 * handoff, which needs to know which slicer it is handing to.
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

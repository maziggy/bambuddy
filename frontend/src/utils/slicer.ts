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
 * Key insight: Using ?file= query format, the browser's URL parser preserves
 * http:// in the query string without any encoding. Only the macOS-specific
 * bambustudioopen:// format needs encodeURIComponent (BS calls url_decode).
 */

export type SlicerType = 'bambu_studio' | 'orcaslicer';

type Platform = 'windows' | 'macos' | 'linux' | 'unknown';

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

  if (slicer === 'orcaslicer') {
    // OrcaSlicer: ?file= query format — http:// preserved in query string
    url = `orcaslicer://open?file=${downloadUrl}`;
  } else {
    const platform = detectPlatform();
    if (platform === 'macos') {
      // macOS only: bambustudioopen scheme via MacOpenURL() callback.
      // Must encode because bare http:// in authority gets mangled by browser.
      // BS calls url_decode() after stripping "bambustudioopen://" prefix.
      url = `bambustudioopen://${encodeURIComponent(downloadUrl)}`;
    } else {
      // Windows/Linux: bambustudio://open?file= via post_init() CLI args.
      // The ?file= query format preserves http:// without encoding.
      // IMPORTANT: On Linux, BS only handles "bambustudio://open" prefix —
      // it does NOT process "bambustudioopen://" (that's macOS-only).
      url = `bambustudio://open?file=${downloadUrl}`;
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

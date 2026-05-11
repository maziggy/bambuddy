import { useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { useTranslation } from 'react-i18next';

export function GCodeViewerPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { t } = useTranslation();

  // Safety guard: if this React app is itself inside an iframe (e.g. the
  // StaticFiles mount isn't registered and serve_spa returned us here),
  // don't render another iframe — that would create an infinite loop.
  if (window !== window.top) {
    return (
      <div style={{ padding: 32, color: '#f88' }}>
        GCode viewer static files not found. Check that the{' '}
        <code>gcode_viewer/</code> directory exists and restart uvicorn.
      </div>
    );
  }

  const cameFromArchive = searchParams.has('archive');
  const cameFromLibrary = searchParams.has('library_file');
  const fallbackPath = cameFromArchive ? '/archives' : cameFromLibrary ? '/files' : '/';
  const backLabel = cameFromArchive
    ? t('gcodeViewer.backToArchives')
    : cameFromLibrary
    ? t('gcodeViewer.backToFiles')
    : t('gcodeViewer.back');

  const handleBack = () => {
    // Prefer browser history so we land where the user actually was (preserving
    // scroll position, filters, etc.). Fall back to a sensible default route
    // when the viewer was opened from a fresh tab / shared link.
    if (window.history.length > 1) {
      navigate(-1);
    } else {
      navigate(fallbackPath);
    }
  };

  // Forward the outer page's query string (e.g. ?archive=82) to the iframe so
  // the adapter inside can pick up the archive to load. The iframe itself must
  // keep the trailing slash on /gcode-viewer/ so it hits the raw-viewer route;
  // the outer SPA URL uses no trailing slash so a reload falls through to the
  // SPA catch-all and keeps the Bambuddy layout shell.
  const iframeSrc = `/gcode-viewer/${window.location.search}`;

  return (
    // h-14 (3.5 rem) is the fixed header height defined in Layout.tsx.
    // Subtracting it prevents a double scrollbar inside the layout shell.
    <div style={{ height: 'calc(100vh - 3.5rem)', display: 'flex', flexDirection: 'column' }}>
      <div className="flex items-center gap-2 px-4 py-2 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900">
        <button
          type="button"
          onClick={handleBack}
          className="inline-flex items-center gap-1.5 text-sm text-gray-700 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          {backLabel}
        </button>
      </div>
      <iframe
        src={iframeSrc}
        title="GCode Viewer"
        style={{
          display: 'block',
          width: '100%',
          flex: 1,
          border: 'none',
        }}
      />
    </div>
  );
}

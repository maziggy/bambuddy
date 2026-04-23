export function GCodeViewerPage() {
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

  // Forward the outer page's query string (e.g. ?archive=82) to the iframe so
  // the adapter inside can pick up the archive to load. The iframe itself must
  // keep the trailing slash on /gcode-viewer/ so it hits the raw-viewer route;
  // the outer SPA URL uses no trailing slash so a reload falls through to the
  // SPA catch-all and keeps the Bambuddy layout shell.
  const iframeSrc = `/gcode-viewer/${window.location.search}`;

  return (
    // h-14 (3.5 rem) is the fixed header height defined in Layout.tsx.
    // Subtracting it prevents a double scrollbar inside the layout shell.
    <iframe
      src={iframeSrc}
      title="GCode Viewer"
      style={{
        display: 'block',
        width: '100%',
        height: 'calc(100vh - 3.5rem)',
        border: 'none',
      }}
    />
  );
}

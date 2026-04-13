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

  return (
    <iframe
      src="/gcode-viewer/"
      title="GCode Viewer"
      style={{
        display: 'block',
        width: '100%',
        height: '100vh',
        border: 'none',
      }}
    />
  );
}

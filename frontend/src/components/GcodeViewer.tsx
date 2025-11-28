import { useEffect, useRef, useState } from 'react';
import { WebGLPreview, init } from 'gcode-preview';
import { Loader2, Layers, ChevronLeft, ChevronRight, FileWarning } from 'lucide-react';

interface BuildVolume {
  x: number;
  y: number;
  z: number;
}

interface GcodeViewerProps {
  gcodeUrl: string;
  buildVolume?: BuildVolume;
  className?: string;
}

export function GcodeViewer({ gcodeUrl, buildVolume = { x: 256, y: 256, z: 256 }, className = '' }: GcodeViewerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const previewRef = useRef<WebGLPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notSliced, setNotSliced] = useState(false);
  const [currentLayer, setCurrentLayer] = useState(0);
  const [totalLayers, setTotalLayers] = useState(0);

  useEffect(() => {
    if (!canvasRef.current) return;

    const canvas = canvasRef.current;

    // Initialize the preview
    const preview = init({
      canvas,
      buildVolume: buildVolume,
      backgroundColor: 0x1a1a1a,
      travelColor: 0x444444,
      extrusionColor: 0x00ae42,
      topLayerColor: 0x00ff5a,
      lastSegmentColor: 0xffffff,
      lineWidth: 2,
      renderTravel: false,
      renderExtrusion: true,
    });

    previewRef.current = preview;

    // Fetch and parse G-code
    setLoading(true);
    setError(null);
    setNotSliced(false);

    fetch(gcodeUrl)
      .then(async response => {
        if (!response.ok) {
          if (response.status === 404) {
            const data = await response.json().catch(() => ({}));
            if (data.detail?.includes('sliced')) {
              setNotSliced(true);
              throw new Error('not_sliced');
            }
          }
          throw new Error('Failed to load G-code');
        }
        return response.text();
      })
      .then(gcode => {
        // Parse G-code
        preview.processGCode(gcode);

        // Get layer count
        const layers = preview.layers?.length || 0;
        setTotalLayers(layers);
        setCurrentLayer(layers);

        // Render all layers initially
        preview.render();
        setLoading(false);
      })
      .catch(err => {
        setError(err.message);
        setLoading(false);
      });

    // Handle resize
    const handleResize = () => {
      if (canvas.parentElement) {
        const rect = canvas.parentElement.getBoundingClientRect();
        canvas.width = rect.width;
        canvas.height = rect.height;
        preview.resize();
      }
    };

    handleResize();
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      preview.dispose();
    };
  }, [gcodeUrl, buildVolume]);

  const handleLayerChange = (layer: number) => {
    if (!previewRef.current) return;
    const newLayer = Math.max(1, Math.min(layer, totalLayers));
    setCurrentLayer(newLayer);
    // Clear and re-render up to the specified layer
    previewRef.current.render();
  };

  const handleSliderChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    handleLayerChange(parseInt(e.target.value, 10));
  };

  return (
    <div className={`relative flex flex-col h-full ${className}`}>
      {/* Canvas container */}
      <div className="flex-1 relative bg-bambu-dark rounded-lg overflow-hidden">
        <canvas
          ref={canvasRef}
          className="w-full h-full"
        />

        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-bambu-dark/80">
            <div className="text-center">
              <Loader2 className="w-8 h-8 animate-spin text-bambu-green mx-auto mb-2" />
              <p className="text-bambu-gray text-sm">Loading G-code...</p>
            </div>
          </div>
        )}

        {notSliced && (
          <div className="absolute inset-0 flex items-center justify-center bg-bambu-dark/80">
            <div className="text-center max-w-sm px-4">
              <FileWarning className="w-12 h-12 text-bambu-gray mx-auto mb-3" />
              <p className="text-white font-medium mb-2">G-code not available</p>
              <p className="text-bambu-gray text-sm">
                This file hasn't been sliced yet. G-code preview is only available
                after slicing the model in Bambu Studio or Orca Slicer.
              </p>
            </div>
          </div>
        )}

        {error && !notSliced && (
          <div className="absolute inset-0 flex items-center justify-center bg-bambu-dark/80">
            <div className="text-center text-red-400">
              <p className="text-sm">{error}</p>
            </div>
          </div>
        )}
      </div>

      {/* Layer controls */}
      {!loading && !error && !notSliced && totalLayers > 0 && (
        <div className="mt-4 px-2">
          <div className="flex items-center gap-3">
            <Layers className="w-4 h-4 text-bambu-gray flex-shrink-0" />

            <button
              onClick={() => handleLayerChange(currentLayer - 1)}
              disabled={currentLayer <= 1}
              className="p-1 rounded hover:bg-bambu-dark-tertiary disabled:opacity-30 disabled:cursor-not-allowed"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>

            <input
              type="range"
              min={1}
              max={totalLayers}
              value={currentLayer}
              onChange={handleSliderChange}
              className="flex-1 h-2 bg-bambu-dark-tertiary rounded-lg appearance-none cursor-pointer accent-bambu-green"
            />

            <button
              onClick={() => handleLayerChange(currentLayer + 1)}
              disabled={currentLayer >= totalLayers}
              className="p-1 rounded hover:bg-bambu-dark-tertiary disabled:opacity-30 disabled:cursor-not-allowed"
            >
              <ChevronRight className="w-4 h-4" />
            </button>

            <span className="text-sm text-bambu-gray min-w-[80px] text-right">
              {currentLayer} / {totalLayers}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

import { useState, useEffect } from 'react';
import { X, ExternalLink, Box, Code2, Loader2 } from 'lucide-react';
import { ModelViewer } from './ModelViewer';
import { GcodeViewer } from './GcodeViewer';
import { Button } from './Button';
import { api } from '../api/client';
import { openInSlicer } from '../utils/slicer';

type ViewTab = '3d' | 'gcode';

interface ModelViewerModalProps {
  archiveId: number;
  title: string;
  onClose: () => void;
}

interface Capabilities {
  has_model: boolean;
  has_gcode: boolean;
  has_source: boolean;
  build_volume: { x: number; y: number; z: number };
  filament_colors: string[];
}

export function ModelViewerModal({ archiveId, title, onClose }: ModelViewerModalProps) {
  const [activeTab, setActiveTab] = useState<ViewTab | null>(null);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [loading, setLoading] = useState(true);

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  useEffect(() => {
    api.getArchiveCapabilities(archiveId)
      .then(caps => {
        setCapabilities(caps);
        // Auto-select the first available tab
        if (caps.has_model) {
          setActiveTab('3d');
        } else if (caps.has_gcode) {
          setActiveTab('gcode');
        }
        setLoading(false);
      })
      .catch(() => {
        // Fallback to 3D model tab if capabilities check fails
        setCapabilities({ has_model: true, has_gcode: false, has_source: false, build_volume: { x: 256, y: 256, z: 256 }, filament_colors: [] });
        setActiveTab('3d');
        setLoading(false);
      });
  }, [archiveId]);

  const handleOpenInSlicer = () => {
    // URL must include .3mf filename for Bambu Studio to recognize the format
    const filename = title || 'model';
    const downloadUrl = `${window.location.origin}${api.getArchiveForSlicer(archiveId, filename)}`;
    openInSlicer(downloadUrl);
  };

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-8"
      onClick={onClose}
    >
      <div
        className="bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary w-full max-w-4xl h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-bambu-dark-tertiary">
          <h2 className="text-lg font-semibold text-white truncate flex-1 mr-4">{title}</h2>
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="sm" onClick={handleOpenInSlicer}>
              <ExternalLink className="w-4 h-4" />
              Open in Slicer
            </Button>
            <Button variant="ghost" size="sm" onClick={onClose}>
              <X className="w-5 h-5" />
            </Button>
          </div>
        </div>

        {/* Tabs - only show if we have capabilities */}
        {capabilities && (
          <div className="flex border-b border-bambu-dark-tertiary">
            <button
              onClick={() => capabilities.has_model && setActiveTab('3d')}
              disabled={!capabilities.has_model}
              className={`flex items-center gap-2 px-6 py-3 text-sm font-medium transition-colors ${
                activeTab === '3d'
                  ? 'text-bambu-green border-b-2 border-bambu-green'
                  : capabilities.has_model
                    ? 'text-bambu-gray hover:text-white'
                    : 'text-bambu-gray/30 cursor-not-allowed'
              }`}
            >
              <Box className="w-4 h-4" />
              3D Model
              {!capabilities.has_model && <span className="text-xs">(not available)</span>}
            </button>
            <button
              onClick={() => capabilities.has_gcode && setActiveTab('gcode')}
              disabled={!capabilities.has_gcode}
              className={`flex items-center gap-2 px-6 py-3 text-sm font-medium transition-colors ${
                activeTab === 'gcode'
                  ? 'text-bambu-green border-b-2 border-bambu-green'
                  : capabilities.has_gcode
                    ? 'text-bambu-gray hover:text-white'
                    : 'text-bambu-gray/30 cursor-not-allowed'
              }`}
            >
              <Code2 className="w-4 h-4" />
              G-code Preview
              {!capabilities.has_gcode && <span className="text-xs">(not sliced)</span>}
            </button>
          </div>
        )}

        {/* Viewer */}
        <div className="flex-1 overflow-hidden p-4">
          {loading ? (
            <div className="w-full h-full flex items-center justify-center">
              <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
            </div>
          ) : activeTab === '3d' && capabilities ? (
            <ModelViewer
              url={capabilities.has_source
                ? api.getSource3mfDownloadUrl(archiveId)
                : api.getArchiveDownload(archiveId)}
              buildVolume={capabilities.build_volume}
              filamentColors={capabilities.filament_colors}
              className="w-full h-full"
            />
          ) : activeTab === 'gcode' && capabilities ? (
            <GcodeViewer
              gcodeUrl={api.getArchiveGcode(archiveId)}
              filamentColors={capabilities.filament_colors}
              className="w-full h-full"
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center text-bambu-gray">
              No preview available for this file
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

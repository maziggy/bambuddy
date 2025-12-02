import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import type { PrinterStatus } from '../../api/client';
import { ChevronUp, ChevronDown } from 'lucide-react';

interface ExtruderControlsProps {
  printerId: number;
  status: PrinterStatus | null | undefined;
  nozzleCount: number;
}

export function ExtruderControls({ status, nozzleCount }: ExtruderControlsProps) {
  const isConnected = status?.connected ?? false;
  const isPrinting = status?.state === 'RUNNING' || status?.state === 'PAUSE';
  const isDualNozzle = nozzleCount > 1;

  const [selectedNozzle, setSelectedNozzle] = useState<'left' | 'right'>('left');

  // TODO: Add extrude/retract API calls when available
  const extrudeMutation = useMutation({
    mutationFn: async ({ distance }: { distance: number }) => {
      // Placeholder - implement when API is ready
      console.log(`Extrude ${distance}mm on ${selectedNozzle} nozzle`);
    },
  });

  const handleExtrude = (distance: number) => {
    extrudeMutation.mutate({ distance });
  };

  const isDisabled = !isConnected || isPrinting || extrudeMutation.isPending;

  return (
    <div className="flex flex-col items-center gap-1.5 justify-center">
      {/* Left/Right Toggle - only for dual nozzle */}
      {isDualNozzle && (
        <div className="flex rounded-md overflow-hidden border border-bambu-dark-tertiary mb-1 flex-shrink-0">
          <button
            onClick={() => setSelectedNozzle('left')}
            className={`px-3 py-1.5 text-sm border-r border-bambu-dark-tertiary transition-colors ${
              selectedNozzle === 'left'
                ? 'bg-bambu-green text-white'
                : 'bg-bambu-dark-secondary text-bambu-gray hover:bg-bambu-dark-tertiary'
            }`}
          >
            Left
          </button>
          <button
            onClick={() => setSelectedNozzle('right')}
            className={`px-3 py-1.5 text-sm transition-colors ${
              selectedNozzle === 'right'
                ? 'bg-bambu-green text-white'
                : 'bg-bambu-dark-secondary text-bambu-gray hover:bg-bambu-dark-tertiary'
            }`}
          >
            Right
          </button>
        </div>
      )}

      {/* Extrude Up Button */}
      <button
        onClick={() => handleExtrude(5)}
        disabled={isDisabled}
        className="w-9 h-[30px] rounded-md bg-bambu-dark-secondary hover:bg-bambu-dark-tertiary border border-bambu-dark-tertiary flex items-center justify-center text-bambu-gray disabled:opacity-50 disabled:cursor-not-allowed"
        title="Extrude 5mm"
      >
        <ChevronUp className="w-4 h-4" />
      </button>

      {/* Extruder Image */}
      <div className="h-[120px] flex items-center justify-center">
        <img
          src={isDualNozzle ? "/icons/dual-extruder.png" : "/icons/single-extruder1.png"}
          alt={isDualNozzle ? "Dual Extruder" : "Single Extruder"}
          className="h-full object-contain"
          onError={(e) => {
            // Fallback if image doesn't load
            (e.target as HTMLImageElement).style.display = 'none';
          }}
        />
      </div>

      {/* Retract Down Button */}
      <button
        onClick={() => handleExtrude(-5)}
        disabled={isDisabled}
        className="w-9 h-[30px] rounded-md bg-bambu-dark-secondary hover:bg-bambu-dark-tertiary border border-bambu-dark-tertiary flex items-center justify-center text-bambu-gray disabled:opacity-50 disabled:cursor-not-allowed"
        title="Retract 5mm"
      >
        <ChevronDown className="w-4 h-4" />
      </button>

      {/* Label */}
      <span className="text-xs text-bambu-gray mt-0.5">Extruder</span>
    </div>
  );
}

import { Printer as PrinterIcon, Loader2, AlertCircle, Check } from 'lucide-react';
import type { PrinterSelectorProps } from './types';

/**
 * Printer selection component with multiple modes:
 * - Grid mode (default): Shows printers as selectable cards (single or multi-select)
 * - Dropdown mode: Shows printers in a select dropdown (used when allowUnassigned is true)
 */
export function PrinterSelector({
  printers,
  selectedPrinterId,
  selectedPrinterIds = [],
  onSelect,
  onMultiSelect,
  isLoading = false,
  allowUnassigned = false,
  allowMultiple = false,
}: PrinterSelectorProps) {
  const activePrinters = printers.filter((p) => p.is_active);

  if (isLoading) {
    return (
      <div className="flex justify-center py-8">
        <Loader2 className="w-6 h-6 text-bambu-green animate-spin" />
      </div>
    );
  }

  // Use dropdown mode for edit scenarios (allows unassigning printer)
  if (allowUnassigned) {
    return (
      <div>
        <label className="block text-sm text-bambu-gray mb-1">Printer</label>
        {printers.length === 0 ? (
          <div className="flex items-center gap-2 text-red-400 text-sm">
            <AlertCircle className="w-4 h-4" />
            No printers configured
          </div>
        ) : (
          <>
            <select
              className={`w-full px-3 py-2 bg-bambu-dark border rounded-lg text-white focus:border-bambu-green focus:outline-none ${
                selectedPrinterId === null ? 'border-orange-400' : 'border-bambu-dark-tertiary'
              }`}
              value={selectedPrinterId ?? ''}
              onChange={(e) => onSelect(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">-- Select a printer --</option>
              {printers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            {selectedPrinterId === null && (
              <p className="text-xs text-orange-400 mt-1 flex items-center gap-1">
                <AlertCircle className="w-3 h-3" />
                Assign a printer to enable printing
              </p>
            )}
          </>
        )}
      </div>
    );
  }

  // Grid mode for reprint/add-to-queue (only active printers)
  if (activePrinters.length === 0) {
    return (
      <div className="text-center py-8 text-bambu-gray">No active printers available</div>
    );
  }

  const handlePrinterClick = (printerId: number) => {
    if (allowMultiple && onMultiSelect) {
      // Multi-select mode: toggle printer in selection
      if (selectedPrinterIds.includes(printerId)) {
        onMultiSelect(selectedPrinterIds.filter((id) => id !== printerId));
      } else {
        onMultiSelect([...selectedPrinterIds, printerId]);
      }
    } else {
      // Single-select mode
      onSelect(printerId);
    }
  };

  const handleSelectAll = () => {
    if (onMultiSelect) {
      onMultiSelect(activePrinters.map((p) => p.id));
    }
  };

  const handleDeselectAll = () => {
    if (onMultiSelect) {
      onMultiSelect([]);
    }
  };

  const isSelected = (printerId: number) => {
    if (allowMultiple) {
      return selectedPrinterIds.includes(printerId);
    }
    return selectedPrinterId === printerId;
  };

  const selectedCount = allowMultiple ? selectedPrinterIds.length : (selectedPrinterId ? 1 : 0);

  return (
    <div className="space-y-2 mb-6">
      {/* Multi-select header */}
      {allowMultiple && activePrinters.length > 1 && (
        <div className="flex items-center justify-between text-xs text-bambu-gray mb-2">
          <span>
            {selectedCount === 0
              ? 'Select printers'
              : `${selectedCount} printer${selectedCount !== 1 ? 's' : ''} selected`}
          </span>
          <div className="flex gap-2">
            {selectedCount < activePrinters.length && (
              <button
                type="button"
                onClick={handleSelectAll}
                className="text-bambu-green hover:text-bambu-green/80 transition-colors"
              >
                Select all
              </button>
            )}
            {selectedCount > 0 && (
              <button
                type="button"
                onClick={handleDeselectAll}
                className="text-bambu-gray hover:text-white transition-colors"
              >
                Clear
              </button>
            )}
          </div>
        </div>
      )}

      {activePrinters.map((printer) => (
        <button
          key={printer.id}
          type="button"
          onClick={() => handlePrinterClick(printer.id)}
          className={`w-full flex items-center gap-3 p-3 rounded-lg border transition-colors ${
            isSelected(printer.id)
              ? 'border-bambu-green bg-bambu-green/10'
              : 'border-bambu-dark-tertiary bg-bambu-dark hover:border-bambu-gray'
          }`}
        >
          <div
            className={`p-2 rounded-lg ${
              isSelected(printer.id) ? 'bg-bambu-green/20' : 'bg-bambu-dark-tertiary'
            }`}
          >
            <PrinterIcon
              className={`w-5 h-5 ${
                isSelected(printer.id) ? 'text-bambu-green' : 'text-bambu-gray'
              }`}
            />
          </div>
          <div className="text-left flex-1">
            <p className="text-white font-medium">{printer.name}</p>
            <p className="text-xs text-bambu-gray">
              {printer.model || 'Unknown model'} • {printer.ip_address}
            </p>
          </div>
          {/* Checkbox indicator for multi-select */}
          {allowMultiple && (
            <div
              className={`w-5 h-5 rounded border-2 flex items-center justify-center transition-colors ${
                isSelected(printer.id)
                  ? 'bg-bambu-green border-bambu-green'
                  : 'border-bambu-gray/50'
              }`}
            >
              {isSelected(printer.id) && <Check className="w-3 h-3 text-white" />}
            </div>
          )}
        </button>
      ))}
    </div>
  );
}

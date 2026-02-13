import { ChevronDown, ChevronRight, Sparkles } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { CalibrationProfile, PAProfileSectionProps } from './types';
import { isMatchingCalibration } from './utils';

export function PAProfileSection({
  formData,
  printersWithCalibrations,
  selectedProfiles,
  setSelectedProfiles,
  expandedPrinters,
  setExpandedPrinters,
}: PAProfileSectionProps) {
  const { t } = useTranslation();

  const togglePrinterExpanded = (printerId: string) => {
    setExpandedPrinters((prev) => {
      const next = new Set(prev);
      if (next.has(printerId)) next.delete(printerId);
      else next.add(printerId);
      return next;
    });
  };

  const toggleProfileSelected = (printerId: string, caliIdx: number, extruderId?: number | null) => {
    const key = `${printerId}:${caliIdx}:${extruderId ?? 'null'}`;
    const printerNozzleKey = `${printerId}:${extruderId ?? 'null'}`;

    setSelectedProfiles((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        // Remove existing profile for same printer/nozzle
        for (const existingKey of Array.from(next)) {
          const parts = existingKey.split(':');
          const existingPrinterNozzle = `${parts[0]}:${parts[2]}`;
          if (existingPrinterNozzle === printerNozzleKey) {
            next.delete(existingKey);
          }
        }
        next.add(key);
      }
      return next;
    });
  };

  // Auto-select best matching profiles
  const autoSelectProfiles = () => {
    const newSelection = new Set<string>();

    for (const { printer, calibrations } of printersWithCalibrations) {
      if (!printer.connected) continue;

      const matchingCals = calibrations.filter(cal =>
        isMatchingCalibration(cal, formData),
      );

      // Group by extruder
      const byExtruder = new Map<string, CalibrationProfile[]>();
      for (const cal of matchingCals) {
        const extKey = `${cal.extruder_id ?? 'null'}`;
        if (!byExtruder.has(extKey)) byExtruder.set(extKey, []);
        byExtruder.get(extKey)!.push(cal);
      }

      // Select best (highest K) for each extruder
      for (const [extKey, cals] of byExtruder) {
        if (cals.length > 0) {
          const sorted = [...cals].sort((a, b) => b.k_value - a.k_value);
          const best = sorted[0];
          newSelection.add(`${printer.id}:${best.cali_idx}:${extKey}`);
        }
      }
    }

    setSelectedProfiles(newSelection);
  };

  if (!formData.material) {
    return (
      <div className="p-6 bg-bambu-dark rounded-lg text-center">
        <p className="text-bambu-gray">
          {t('inventory.selectMaterialFirst')}
        </p>
      </div>
    );
  }

  if (printersWithCalibrations.length === 0) {
    return (
      <div className="p-6 bg-bambu-dark rounded-lg text-center">
        <p className="text-bambu-gray">
          {t('inventory.noPrintersConfigured')}
        </p>
      </div>
    );
  }

  // Count total matching profiles
  const totalMatching = printersWithCalibrations.reduce((sum, { printer, calibrations }) => {
    if (!printer.connected) return sum;
    return sum + calibrations.filter(cal => isMatchingCalibration(cal, formData)).length;
  }, 0);

  const renderProfile = (printer: { id: number }, cal: CalibrationProfile) => {
    const key = `${printer.id}:${cal.cali_idx}:${cal.extruder_id ?? 'null'}`;
    const isSelected = selectedProfiles.has(key);
    return (
      <label
        key={`${cal.cali_idx}-${cal.extruder_id}`}
        className={`flex items-center gap-3 p-3 rounded-lg cursor-pointer transition-all border ${
          isSelected
            ? 'bg-bambu-green/10 border-bambu-green/30'
            : 'bg-bambu-dark border-transparent hover:bg-bambu-dark/80'
        }`}
      >
        <input
          type="checkbox"
          checked={isSelected}
          onChange={() => toggleProfileSelected(String(printer.id), cal.cali_idx, cal.extruder_id)}
          className="w-4 h-4 rounded border-bambu-dark-tertiary text-bambu-green focus:ring-bambu-green"
        />
        <div className="flex-1 min-w-0">
          <span className={`text-sm font-medium ${isSelected ? 'text-bambu-green' : 'text-white'}`}>
            {cal.name || cal.filament_id}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-xs font-mono px-2 py-0.5 rounded bg-bambu-dark text-bambu-gray">
            K={cal.k_value.toFixed(3)}
          </span>
        </div>
      </label>
    );
  };

  return (
    <div className="space-y-4">
      {/* Header with auto-select */}
      <div className="flex items-center justify-between">
        <p className="text-xs text-bambu-gray">
          {t('inventory.matchingFilter')}: {formData.brand || t('inventory.anyBrand')} / {formData.material} / {formData.subtype || t('inventory.anyVariant')}
        </p>
        {totalMatching > 0 && (
          <button
            type="button"
            onClick={autoSelectProfiles}
            className="flex items-center gap-1.5 px-2 py-1 text-xs bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-bambu-gray hover:text-white hover:border-bambu-green transition-colors"
          >
            <Sparkles className="w-3.5 h-3.5" />
            {t('inventory.autoSelect')} ({totalMatching})
          </button>
        )}
      </div>

      {/* Printer sections */}
      <div className="space-y-3">
        {printersWithCalibrations.map(({ printer, calibrations }) => {
          const isExpanded = expandedPrinters.has(String(printer.id));
          const matchingCals = calibrations.filter(cal => isMatchingCalibration(cal, formData));
          const matchingCount = matchingCals.length;

          // Multi-nozzle grouping
          const isMultiNozzle = matchingCals.some(cal =>
            cal.extruder_id !== undefined && cal.extruder_id !== null && cal.extruder_id > 0,
          );
          const leftNozzleCals = matchingCals.filter(cal => cal.extruder_id === 1);
          const rightNozzleCals = matchingCals.filter(cal =>
            cal.extruder_id === 0 || cal.extruder_id === undefined || cal.extruder_id === null,
          );

          return (
            <div
              key={printer.id}
              className="border border-bambu-dark-tertiary rounded-lg overflow-hidden"
            >
              {/* Printer Header */}
              <button
                type="button"
                onClick={() => togglePrinterExpanded(String(printer.id))}
                className="w-full px-4 py-3 flex items-center justify-between bg-bambu-dark-secondary hover:bg-bambu-dark-tertiary transition-colors"
              >
                <div className="flex items-center gap-3">
                  {isExpanded ? (
                    <ChevronDown className="w-4 h-4 text-bambu-gray" />
                  ) : (
                    <ChevronRight className="w-4 h-4 text-bambu-gray" />
                  )}
                  <span className="font-medium text-white">
                    {printer.name}
                  </span>
                  {matchingCount > 0 ? (
                    <span className="text-xs px-2 py-0.5 rounded-full bg-bambu-green/20 text-bambu-green">
                      {matchingCount} {matchingCount !== 1 ? t('inventory.matches') : t('inventory.match')}
                    </span>
                  ) : (
                    <span className="text-xs px-2 py-0.5 rounded-full bg-bambu-dark-tertiary text-bambu-gray">
                      {t('inventory.noMatches')}
                    </span>
                  )}
                </div>
                <span className={`text-xs px-2 py-1 rounded-full ${
                  printer.connected
                    ? 'bg-green-500/20 text-green-500'
                    : 'bg-bambu-gray/20 text-bambu-gray'
                }`}>
                  {printer.connected ? t('inventory.connected') : t('inventory.offline')}
                </span>
              </button>

              {/* Calibration Profiles */}
              {isExpanded && (
                <div className="px-4 py-3 space-y-3 bg-bambu-dark border-t border-bambu-dark-tertiary">
                  {!printer.connected ? (
                    <p className="text-sm text-bambu-gray italic py-2">
                      {t('inventory.printerOffline')}
                    </p>
                  ) : matchingCount === 0 ? (
                    <p className="text-sm text-bambu-gray italic py-2">
                      {t('inventory.noKProfilesMatch')}
                    </p>
                  ) : isMultiNozzle ? (
                    <>
                      {leftNozzleCals.length > 0 && (
                        <div className="space-y-2">
                          <p className="text-xs font-medium text-bambu-gray uppercase tracking-wide">
                            {t('inventory.leftNozzle')}
                          </p>
                          <div className="space-y-2">
                            {leftNozzleCals.map(cal => renderProfile(printer, cal))}
                          </div>
                        </div>
                      )}
                      {rightNozzleCals.length > 0 && (
                        <div className="space-y-2">
                          <p className="text-xs font-medium text-bambu-gray uppercase tracking-wide">
                            {t('inventory.rightNozzle')}
                          </p>
                          <div className="space-y-2">
                            {rightNozzleCals.map(cal => renderProfile(printer, cal))}
                          </div>
                        </div>
                      )}
                    </>
                  ) : (
                    <div className="space-y-2">
                      {matchingCals.map(cal => renderProfile(printer, cal))}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Summary */}
      {selectedProfiles.size > 0 && (
        <div className="p-3 bg-bambu-green/10 border border-bambu-green/30 rounded-lg">
          <p className="text-sm text-white">
            <span className="font-semibold">{selectedProfiles.size}</span> {t('inventory.profilesSelected')}
          </p>
        </div>
      )}
    </div>
  );
}

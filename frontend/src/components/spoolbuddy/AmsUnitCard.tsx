import type { AMSUnit, AMSTray } from '../../api/client';

function trayColorToCSS(color: string | null): string {
  if (!color) return '#808080';
  return `#${color.slice(0, 6)}`;
}

function isTrayEmpty(tray: AMSTray): boolean {
  return !tray.tray_type || tray.tray_type === '';
}

function getAmsName(id: number): string {
  if (id <= 3) return `AMS ${String.fromCharCode(65 + id)}`;
  if (id >= 128 && id <= 135) return `AMS HT ${String.fromCharCode(65 + id - 128)}`;
  return `AMS ${id}`;
}

function formatHumidity(value: number | null): string {
  if (value === null || value === undefined) return '-';
  if (value > 5) return `${value}%`;
  return `Level ${value}`;
}

interface SpoolSlotProps {
  tray: AMSTray;
  slotIndex: number;
  isActive: boolean;
}

function SpoolSlot({ tray, slotIndex, isActive }: SpoolSlotProps) {
  const isEmpty = isTrayEmpty(tray);
  const color = trayColorToCSS(tray.tray_color);

  return (
    <div className={`relative flex flex-col items-center p-2 rounded-lg transition-all ${isActive ? 'ring-2 ring-bambu-green' : ''}`}>
      {/* Spool visualization */}
      <div className="relative w-14 h-14 mb-1">
        {isEmpty ? (
          <div className="w-full h-full rounded-full border-2 border-dashed border-gray-500 flex items-center justify-center">
            <div className="w-3 h-3 rounded-full bg-gray-600" />
          </div>
        ) : (
          <svg viewBox="0 0 56 56" className="w-full h-full">
            <circle cx="28" cy="28" r="26" fill={color} />
            <circle cx="28" cy="28" r="20" fill={color} style={{ filter: 'brightness(0.85)' }} />
            <ellipse cx="20" cy="20" rx="6" ry="4" fill="white" opacity="0.3" />
            <circle cx="28" cy="28" r="8" fill="#2d2d2d" />
            <circle cx="28" cy="28" r="5" fill="#1a1a1a" />
          </svg>
        )}
        {isActive && (
          <div className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-2 h-2 bg-bambu-green rounded-full" />
        )}
      </div>

      {/* Material type */}
      <span className="text-xs text-white/70 truncate max-w-full">
        {isEmpty ? 'Empty' : tray.tray_type || 'Unknown'}
      </span>

      {/* Fill level bar */}
      {!isEmpty && tray.remain !== null && tray.remain !== undefined && tray.remain >= 0 && (
        <div className="w-full h-1 bg-bambu-dark-tertiary rounded-full overflow-hidden mt-1">
          <div
            className="h-full rounded-full transition-all"
            style={{
              width: `${tray.remain}%`,
              backgroundColor: tray.remain > 50 ? '#22c55e' : tray.remain > 20 ? '#f59e0b' : '#ef4444',
            }}
          />
        </div>
      )}

      {/* Slot number */}
      <span className="absolute top-1 right-1 text-[10px] text-white/30">{slotIndex + 1}</span>
    </div>
  );
}

interface AmsUnitCardProps {
  unit: AMSUnit;
  activeSlot: number | null;
}

export function AmsUnitCard({ unit, activeSlot }: AmsUnitCardProps) {
  const trays = unit.tray || [];
  const isHt = unit.is_ams_ht;
  const slotCount = isHt ? 1 : 4;

  return (
    <div className="bg-bambu-dark-secondary rounded-lg p-3">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <span className="text-white font-medium">{getAmsName(unit.id)}</span>
        {unit.humidity !== null && unit.humidity !== undefined && (
          <div className="flex items-center gap-1 text-xs text-white/50">
            <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
              <path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z" />
            </svg>
            <span>{formatHumidity(unit.humidity)}</span>
          </div>
        )}
      </div>

      {/* Slots grid */}
      <div className={`grid ${isHt ? 'grid-cols-1 max-w-[100px] mx-auto' : 'grid-cols-4'} gap-2`}>
        {Array.from({ length: slotCount }).map((_, i) => {
          const tray = trays[i] || {
            id: i,
            tray_color: null,
            tray_type: '',
            tray_sub_brands: null,
            tray_id_name: null,
            tray_info_idx: null,
            remain: -1,
            k: null,
            cali_idx: null,
            tag_uid: null,
            tray_uuid: null,
            nozzle_temp_min: null,
            nozzle_temp_max: null,
          };
          return (
            <SpoolSlot
              key={i}
              tray={tray}
              slotIndex={i}
              isActive={activeSlot === i}
            />
          );
        })}
      </div>
    </div>
  );
}

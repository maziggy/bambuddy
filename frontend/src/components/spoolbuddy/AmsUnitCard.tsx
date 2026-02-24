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

// --- SVG Icons (matching PrintersPage Bambu Lab style) ---

function WaterDropEmpty({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 36 54" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M17.8131 0.00538C18.4463 -0.15091 20.3648 3.14642 20.8264 3.84781C25.4187 10.816 35.3089 26.9368 35.9383 34.8694C37.4182 53.5822 11.882 61.3357 2.53721 45.3789C-1.73471 38.0791 0.016 32.2049 3.178 25.0232C6.99221 16.3662 12.6411 7.90372 17.8131 0.00538ZM18.3738 7.24807L17.5881 7.48441C14.4452 12.9431 10.917 18.2341 8.19369 23.9368C4.6808 31.29 1.18317 38.5479 7.69403 45.5657C17.3058 55.9228 34.9847 46.8808 31.4604 32.8681C29.2558 24.0969 22.4207 15.2913 18.3776 7.24807H18.3738Z" fill="#C3C2C1"/>
    </svg>
  );
}

function WaterDropHalf({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 35 53" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M17.3165 0.0038C17.932 -0.14959 19.7971 3.08645 20.2458 3.77481C24.7103 10.6135 34.3251 26.4346 34.937 34.2198C36.3757 52.5848 11.5505 60.1942 2.46584 44.534C-1.68714 37.3735 0.0148 31.6085 3.08879 24.5603C6.79681 16.0605 12.2884 7.75907 17.3165 0.0038ZM17.8615 7.11561L17.0977 7.34755C14.0423 12.7048 10.6124 17.8974 7.96483 23.4941C4.54975 30.7107 1.14949 37.8337 7.47908 44.721C16.8233 54.8856 34.01 46.0117 30.5838 32.2595C28.4405 23.6512 21.7957 15.0093 17.8652 7.11561H17.8615Z" fill="#C3C2C1"/>
      <path d="M5.03547 30.112C9.64453 30.4936 11.632 35.7985 16.4154 35.791C19.6339 35.7873 20.2161 33.2283 22.3853 31.6197C31.6776 24.7286 33.5835 37.4894 27.9881 44.4254C18.1878 56.5653 -1.16063 44.6013 5.03917 30.1158L5.03547 30.112Z" fill="#1F8FEB"/>
    </svg>
  );
}

function WaterDropFull({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 36 54" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M17.9625 4.48059L4.77216 26.3154L2.08228 40.2175L10.0224 50.8414H23.1594L33.3246 42.1693V30.2455L17.9625 4.48059Z" fill="#1F8FEB"/>
      <path d="M17.7948 0.00538C18.4273 -0.15091 20.3438 3.14642 20.8048 3.84781C25.3921 10.816 35.2715 26.9368 35.9001 34.8694C37.3784 53.5822 11.8702 61.3357 2.53562 45.3789C-1.73163 38.0829 0.0134 32.2087 3.1757 25.027C6.98574 16.3662 12.6284 7.90372 17.7948 0.00538ZM18.3549 7.24807L17.57 7.48441C14.4306 12.9431 10.9063 18.2341 8.1859 23.9368C4.67686 31.29 1.18305 38.5479 7.68679 45.5657C17.2881 55.9228 34.9476 46.8808 31.4271 32.8681C29.2249 24.0969 22.3974 15.2913 18.3587 7.24807H18.3549Z" fill="#C3C2C1"/>
    </svg>
  );
}

function ThermometerEmpty({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 12 20" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M6 0.5C4.6 0.5 3.5 1.6 3.5 3V12.1C2.6 12.8 2 13.9 2 15C2 17.2 3.8 19 6 19C8.2 19 10 17.2 10 15C10 13.9 9.4 12.8 8.5 12.1V3C8.5 1.6 7.4 0.5 6 0.5Z" stroke="#C3C2C1" strokeWidth="1" fill="none"/>
      <circle cx="6" cy="15" r="2.5" stroke="#C3C2C1" strokeWidth="1" fill="none"/>
    </svg>
  );
}

function ThermometerHalf({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 12 20" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="4.5" y="8" width="3" height="4.5" fill="#d4a017" rx="0.5"/>
      <circle cx="6" cy="15" r="2" fill="#d4a017"/>
      <path d="M6 0.5C4.6 0.5 3.5 1.6 3.5 3V12.1C2.6 12.8 2 13.9 2 15C2 17.2 3.8 19 6 19C8.2 19 10 17.2 10 15C10 13.9 9.4 12.8 8.5 12.1V3C8.5 1.6 7.4 0.5 6 0.5Z" stroke="#C3C2C1" strokeWidth="1" fill="none"/>
    </svg>
  );
}

function ThermometerFull({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 12 20" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="4.5" y="3" width="3" height="9.5" fill="#c62828" rx="0.5"/>
      <circle cx="6" cy="15" r="2" fill="#c62828"/>
      <path d="M6 0.5C4.6 0.5 3.5 1.6 3.5 3V12.1C2.6 12.8 2 13.9 2 15C2 17.2 3.8 19 6 19C8.2 19 10 17.2 10 15C10 13.9 9.4 12.8 8.5 12.1V3C8.5 1.6 7.4 0.5 6 0.5Z" stroke="#C3C2C1" strokeWidth="1" fill="none"/>
    </svg>
  );
}

// --- Threshold-colored indicators ---

function HumidityIndicator({ humidity, goodThreshold = 40, fairThreshold = 60 }: { humidity: number; goodThreshold?: number; fairThreshold?: number }) {
  let textColor: string;
  let DropComponent: React.FC<{ className?: string }>;

  if (humidity <= goodThreshold) {
    textColor = '#22a352';
    DropComponent = WaterDropEmpty;
  } else if (humidity <= fairThreshold) {
    textColor = '#d4a017';
    DropComponent = WaterDropHalf;
  } else {
    textColor = '#c62828';
    DropComponent = WaterDropFull;
  }

  return (
    <div className="flex items-center gap-0.5">
      <DropComponent className="w-2.5 h-3" />
      <span className="font-medium tabular-nums text-[10px]" style={{ color: textColor }}>{humidity}%</span>
    </div>
  );
}

function TemperatureIndicator({ temp, goodThreshold = 28, fairThreshold = 35 }: { temp: number; goodThreshold?: number; fairThreshold?: number }) {
  let textColor: string;
  let ThermoComponent: React.FC<{ className?: string }>;

  if (temp <= goodThreshold) {
    textColor = '#22a352';
    ThermoComponent = ThermometerEmpty;
  } else if (temp <= fairThreshold) {
    textColor = '#d4a017';
    ThermoComponent = ThermometerHalf;
  } else {
    textColor = '#c62828';
    ThermoComponent = ThermometerFull;
  }

  return (
    <div className="flex items-center gap-0.5">
      <ThermoComponent className="w-2.5 h-3" />
      <span className="font-medium tabular-nums text-[10px]" style={{ color: textColor }}>{temp}°C</span>
    </div>
  );
}

// --- Nozzle badge ---

function NozzleBadge({ side }: { side: 'L' | 'R' }) {
  return (
    <span
      className="inline-flex items-center justify-center w-3.5 h-3.5 text-[8px] font-bold rounded"
      style={{ backgroundColor: '#1a4d2e', color: '#00ae42' }}
    >
      {side}
    </span>
  );
}

// --- Components ---

interface SpoolSlotProps {
  tray: AMSTray;
  slotIndex: number;
  isActive: boolean;
  onClick?: () => void;
}

function SpoolSlot({ tray, slotIndex, isActive, onClick }: SpoolSlotProps) {
  const isEmpty = isTrayEmpty(tray);
  const color = trayColorToCSS(tray.tray_color);

  return (
    <div
      className={`relative flex flex-col items-center p-2 rounded-lg transition-all ${isActive ? 'ring-2 ring-bambu-green' : ''} ${onClick ? 'cursor-pointer hover:bg-white/5' : ''}`}
      onClick={onClick}
    >
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

export interface AmsThresholds {
  humidityGood: number;
  humidityFair: number;
  tempGood: number;
  tempFair: number;
}

interface AmsUnitCardProps {
  unit: AMSUnit;
  activeSlot: number | null;
  onConfigureSlot?: (amsId: number, trayId: number, tray: AMSTray | null) => void;
  isDualNozzle?: boolean;
  nozzleSide?: 'L' | 'R' | null;
  thresholds?: AmsThresholds;
}

export function AmsUnitCard({ unit, activeSlot, onConfigureSlot, isDualNozzle, nozzleSide, thresholds }: AmsUnitCardProps) {
  const trays = unit.tray || [];
  const isHt = unit.is_ams_ht;
  const slotCount = isHt ? 1 : 4;

  return (
    <div className="bg-bambu-dark-secondary rounded-lg p-3">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5">
          <span className="text-white font-medium text-sm">{getAmsName(unit.id)}</span>
          {isDualNozzle && nozzleSide && (
            <NozzleBadge side={nozzleSide} />
          )}
        </div>
        <div className="flex items-center gap-2">
          {unit.temp != null && (
            <TemperatureIndicator
              temp={unit.temp}
              goodThreshold={thresholds?.tempGood}
              fairThreshold={thresholds?.tempFair}
            />
          )}
          {unit.humidity != null && (
            <HumidityIndicator
              humidity={unit.humidity}
              goodThreshold={thresholds?.humidityGood}
              fairThreshold={thresholds?.humidityFair}
            />
          )}
        </div>
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
              onClick={onConfigureSlot ? () => onConfigureSlot(unit.id, i, isTrayEmpty(tray) ? null : tray) : undefined}
            />
          );
        })}
      </div>
    </div>
  );
}

// Exported for use in SpoolBuddyAmsPage compact cards
export { HumidityIndicator, TemperatureIndicator, NozzleBadge };

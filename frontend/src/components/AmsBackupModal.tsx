/**
 * #1762 — AMS Filament Backup status modal.
 *
 * Opens from the AmsBackupBadge click. Shows the global toggle and a
 * BambuStudio-style ring graphic per backup pair — each ring represents
 * the rotation order the firmware will follow when the active slot runs
 * out.
 *
 * On dual-extruder printers, each ring carries a small "R" / "L" badge
 * because the firmware can't cross extruders even with the global backup
 * bit set.
 *
 * Theme-aware via CSS variables, matching AMSHistoryModal — adapts to
 * every background variant the user has picked.
 */
import { useEffect } from 'react';
import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Toggle } from './Toggle';
import {
  computeBackupGroups,
  normalizeColor,
  type AmsUnitLike,
  type BackupGroup,
} from '../utils/amsHelpers';

interface AmsBackupModalProps {
  isOpen: boolean;
  state: boolean | null;
  amsUnits: AmsUnitLike[] | undefined;
  amsExtruderMap: Record<string, number> | undefined;
  isDualNozzle: boolean;
  canToggle: boolean;
  pending: boolean;
  onToggle: (next: boolean) => void;
  onClose: () => void;
}

/**
 * Compact slot label like "A·3" / "HT·1" — the ring is small, every char
 * counts toward readability.
 */
function formatSlotLabel(amsId: number, slotIdx: number, totalTraysOnUnit: number): string {
  const isHt = totalTraysOnUnit === 1 || amsId >= 128;
  const normalizedId = amsId >= 128 ? amsId - 128 : amsId;
  const letter = String.fromCharCode(65 + normalizedId);
  return isHt ? `HT·${slotIdx + 1}` : `${letter}·${slotIdx + 1}`;
}

/** Pick a readable text colour for a given filament hex. */
function pickContrastTextColor(rgbaHex: string | null | undefined): string {
  const s = (rgbaHex || '').replace('#', '').slice(0, 6);
  if (s.length !== 6) return '#FFFFFF';
  const r = parseInt(s.slice(0, 2), 16);
  const g = parseInt(s.slice(2, 4), 16);
  const b = parseInt(s.slice(4, 6), 16);
  if ([r, g, b].some(Number.isNaN)) return '#FFFFFF';
  const luma = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  return luma > 0.55 ? '#1A1A1A' : '#FFFFFF';
}

function BackupRing({
  group,
  trayCountByAms,
  innerBg,
  textPrimary,
  textSecondary,
  showExtruderBadge,
  extruderLabel,
}: {
  group: BackupGroup;
  trayCountByAms: Map<number, number>;
  innerBg: string;
  textPrimary: string;
  textSecondary: string;
  showExtruderBadge: boolean;
  extruderLabel: string;
}) {
  const filamentHex = normalizeColor(group.trayColor || undefined);
  const ringTextColor = pickContrastTextColor(group.trayColor);
  // The pill background that sits behind each slot label — keeps text legible
  // regardless of the filament fill colour.
  const labelPillBg = ringTextColor === '#FFFFFF' ? 'rgba(0,0,0,0.45)' : 'rgba(255,255,255,0.7)';
  const n = group.members.length;

  // Geometry: -100..100 viewport. Outer ring 92, inner cutout 56.
  // Slot labels sit on the colour band at radius 76.
  const labelRadius = 76;

  return (
    <div className="relative flex flex-col items-center">
      {showExtruderBadge && (
        <span
          className="absolute -top-1 -left-1 z-10 w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold shadow"
          style={{
            backgroundColor: textPrimary,
            color: innerBg,
          }}
          aria-label={extruderLabel}
          title={extruderLabel}
        >
          {extruderLabel}
        </span>
      )}
      <svg viewBox="-100 -100 200 200" className="w-44 h-44">
        {/* Subtle outer ring — gives a crisp edge on light AND dark themes. */}
        <circle cx="0" cy="0" r="95" fill="none" stroke={textSecondary} strokeOpacity="0.25" strokeWidth="1" />
        {/* Colour band */}
        <circle cx="0" cy="0" r="92" fill={filamentHex} />
        {/* Inner cutout */}
        <circle cx="0" cy="0" r="56" fill={innerBg} />
        {/* Inner ring border for definition between centre and colour band */}
        <circle cx="0" cy="0" r="56" fill="none" stroke={textSecondary} strokeOpacity="0.3" strokeWidth="1" />
        {/* Centre: material name */}
        <text
          x="0"
          y="-4"
          textAnchor="middle"
          dominantBaseline="middle"
          fontSize="14"
          fontWeight="700"
          fill={textPrimary}
        >
          {group.displayName || '—'}
        </text>
        {/* Centre: rotation count */}
        <text
          x="0"
          y="16"
          textAnchor="middle"
          dominantBaseline="middle"
          fontSize="11"
          fontWeight="500"
          fill={textSecondary}
        >
          {`${n}× ↻`}
        </text>
        {/* Slot labels around the ring, each on a pill for legibility */}
        {group.members.map((m, i) => {
          const angleDeg = (i * 360) / n - 90;
          const rad = (angleDeg * Math.PI) / 180;
          const x = labelRadius * Math.cos(rad);
          const y = labelRadius * Math.sin(rad);
          const label = formatSlotLabel(m.amsId, m.slotIdx, trayCountByAms.get(m.amsId) ?? 4);
          // Approximate pill width based on char count (each digit ≈ 6.5 px @ 12 px font).
          const pillWidth = Math.max(22, label.length * 7 + 8);
          return (
            <g key={`${m.amsId}-${m.slotIdx}`}>
              <rect
                x={x - pillWidth / 2}
                y={y - 9}
                width={pillWidth}
                height={18}
                rx={9}
                ry={9}
                fill={labelPillBg}
              />
              <text
                x={x}
                y={y}
                textAnchor="middle"
                dominantBaseline="middle"
                fontSize="12"
                fontWeight="700"
                fill={ringTextColor}
              >
                {label}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export function AmsBackupModal({
  isOpen,
  state,
  amsUnits,
  amsExtruderMap,
  isDualNozzle,
  canToggle,
  pending,
  onToggle,
  onClose,
}: AmsBackupModalProps) {
  const { t } = useTranslation();

  // Close on Escape key while the modal is open. Captures at the window
  // level so it works even when focus isn't inside the modal subtree
  // (e.g. after the Toggle is clicked).
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  // Theme-aware tokens, matching AMSHistoryModal.
  const modalBg = 'var(--bg-secondary)';
  const sectionBg = 'var(--bg-primary)';
  const borderColor = 'var(--border-color)';
  const textPrimary = 'var(--text-primary)';
  const textSecondary = 'var(--text-secondary)';

  // Effective dual-nozzle detection: only split per extruder if the map
  // actually carries 2 distinct values across the AMS units we have data
  // for. Empty / single-value maps collapse to a single section to avoid
  // misleading badges.
  const effectiveDualNozzle = (() => {
    if (!isDualNozzle) return false;
    if (!amsExtruderMap) return false;
    const distinctValues = new Set<number>();
    for (const ams of amsUnits || []) {
      const raw = amsExtruderMap[String(ams.id)];
      if (raw === undefined) continue;
      distinctValues.add(Number(raw));
      if (distinctValues.size > 1) return true;
    }
    return false;
  })();

  const groups = computeBackupGroups(amsUnits, amsExtruderMap, effectiveDualNozzle);
  const trayCountByAms = new Map<number, number>(
    (amsUnits || []).map((u) => [u.id, u.tray.length]),
  );

  // Only pairs are rendered — lone slots are deliberately suppressed.
  const pairs = groups.filter((g) => g.members.length >= 2);

  const isOn = state === true;
  const isUnknown = state === null;

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      onClick={onClose}
      data-testid="ams-backup-modal"
    >
      <div
        className="rounded-xl w-full max-w-2xl max-h-[90vh] overflow-hidden shadow-xl flex flex-col"
        style={{ backgroundColor: modalBg }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="ams-backup-modal-title"
      >
        <div
          className="flex items-center justify-between px-5 py-3 border-b"
          style={{ borderColor }}
        >
          <h2
            id="ams-backup-modal-title"
            className="text-base font-semibold"
            style={{ color: textPrimary }}
          >
            {t('printers.amsBackup.modalTitle')}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded-md transition-colors hover:bg-black/10"
            style={{ color: textSecondary }}
            aria-label={t('common.close')}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div
          className="flex items-center justify-between px-5 py-3 border-b"
          style={{ borderColor, backgroundColor: sectionBg }}
        >
          <div className="min-w-0 mr-3">
            <div className="text-sm font-medium" style={{ color: textPrimary }}>
              {isUnknown
                ? t('printers.amsBackup.stateUnknown')
                : isOn
                  ? t('printers.amsBackup.stateOn')
                  : t('printers.amsBackup.stateOff')}
            </div>
            <p className="text-xs mt-0.5" style={{ color: textSecondary }}>
              {t('printers.amsBackup.modalHelp')}
            </p>
          </div>
          <Toggle
            checked={isOn}
            onChange={onToggle}
            disabled={!canToggle || isUnknown || pending}
          />
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-6">
          {pairs.length === 0 ? (
            <p
              className="text-sm text-center py-8"
              style={{ color: textSecondary }}
            >
              {t('printers.amsBackup.modalNoPairs')}
            </p>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-6 justify-items-center">
              {pairs.map((g) => (
                <BackupRing
                  key={g.key}
                  group={g}
                  trayCountByAms={trayCountByAms}
                  innerBg={modalBg}
                  textPrimary={textPrimary}
                  textSecondary={textSecondary}
                  showExtruderBadge={effectiveDualNozzle}
                  extruderLabel={
                    g.extruder === 0
                      ? t('printers.amsBackup.extruderRightShort')
                      : t('printers.amsBackup.extruderLeftShort')
                  }
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * FilamentSlotCircle renders a small color circle with the 1-based slot
 * number centered inside, matching the style used on AMS cards in PrintersPage.
 *
 * Props:
 *   trayColor  - 6-char hex color string WITHOUT leading '#' (e.g. "FF0000").
 *                Pass undefined / empty string when the slot is empty.
 *   trayType   - Filament material string (e.g. "PLA").  Used to decide the
 *                fallback background when there is no color but a type is known.
 *   isEmpty    - Whether the slot contains no filament.
 *   emptyKind  - Optional refinement of the empty state used to render the
 *                slot border (#1322 follow-up): "physical" for firmware-
 *                confirmed no spool (state 9/10), "reset" for slots where
 *                the user cleared the assignment but the firmware hasn't
 *                positively confirmed emptiness. Ignored when isEmpty is false.
 *   slotNumber - 1-based slot number to display inside the circle.
 */

interface FilamentSlotCircleProps {
  trayColor?: string | null;
  trayType?: string | null;
  isEmpty: boolean;
  emptyKind?: 'physical' | 'reset' | null;
  slotNumber: number;
}

function isLightFilamentColor(hex: string): boolean {
  if (!hex || hex.length < 6) return false;
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.6;
}

export function FilamentSlotCircle({ trayColor, trayType, isEmpty, emptyKind, slotNumber }: FilamentSlotCircleProps) {
  // Reset slots get a quieter border than physical-empty so they read as
  // "cleared but possibly still has a spool the firmware hasn't confirmed
  // gone" rather than "definitely no spool".
  const emptyBorderColor = emptyKind === 'reset' ? '#3d3d3d' : '#666';
  return (
    <div
      className="w-3.5 h-3.5 rounded-full mx-auto mb-0.5 border-2 flex items-center justify-center"
      style={{
        backgroundColor: trayColor ? `#${trayColor}` : (trayType ? '#333' : 'transparent'),
        borderColor: isEmpty ? emptyBorderColor : 'rgba(255,255,255,0.1)',
        borderStyle: isEmpty ? 'dashed' : 'solid',
      }}
    >
      <span
        className="text-[6px] font-bold leading-none select-none"
        style={{ color: trayColor && isLightFilamentColor(trayColor) ? '#000' : '#fff' }}
      >
        {slotNumber}
      </span>
    </div>
  );
}

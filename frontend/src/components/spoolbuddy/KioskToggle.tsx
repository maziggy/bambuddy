/**
 * Kiosk-sized toggle switch (52×30 px) for SpoolBuddy touch screens.
 *
 * Shared by the barcode feature's surfaces (ScannerTab, BarcodeAddModal's
 * refill toggle) so the switch markup isn't duplicated. Deliberately larger
 * than the app-wide `components/Toggle.tsx` — kiosk touch targets need the
 * extra size.
 */
interface KioskToggleProps {
  checked: boolean;
  disabled?: boolean;
  onToggle: () => void;
}

export function KioskToggle({ checked, disabled, onToggle }: KioskToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={onToggle}
      className={`relative w-[52px] h-[30px] rounded-full shrink-0 transition-colors disabled:opacity-40 ${
        checked ? 'bg-green-600' : 'bg-zinc-600'
      }`}
    >
      <span
        className={`absolute top-[3px] w-6 h-6 rounded-full bg-white transition-all ${
          checked ? 'right-[3px]' : 'left-[3px]'
        }`}
      />
    </button>
  );
}

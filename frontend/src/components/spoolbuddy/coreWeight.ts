// Storage key for default core weight (same key the kiosk settings page writes).
const DEFAULT_CORE_WEIGHT_KEY = 'spoolbuddy-default-core-weight';

/**
 * The kiosk-wide default empty-spool core weight (grams).
 *
 * The same logic is currently inlined in SpoolInfoCard / TagDetectedModal /
 * InventorySpoolInfoCard (predates this feature); they can migrate here in a
 * follow-up without touching this feature's code.
 */
export function getDefaultCoreWeight(): number {
  try {
    const stored = localStorage.getItem(DEFAULT_CORE_WEIGHT_KEY);
    if (stored) {
      const w = parseInt(stored, 10);
      if (w >= 0 && w <= 500) return w;
    }
  } catch {
    // ignore
  }
  return 250; // Default 250g (typical Bambu spool core)
}

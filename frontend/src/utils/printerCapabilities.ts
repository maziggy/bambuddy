import type { Printer, PrinterStatus } from '../api/client';

/** Uppercase normalized model id (display name or internal MQTT code). */
export function normalizePrinterModel(model: string | null | undefined): string {
  if (!model) return '';
  return model.trim().toUpperCase().replace(/\s+/g, '').replace(/-/g, '');
}

const CHAMBER_TEMP_MODELS = new Set([
  'X1', 'X1C', 'X1E', 'X2D', 'P2S',
  'H2C', 'H2D', 'H2DPRO', 'H2S',
  'BL-P001', 'C13', 'N6', 'O1D', 'O1C', 'O1C2', 'O1S', 'O1E', 'O2D', 'N7',
]);

/** Models with a controllable chamber / exhaust fan (M106 P3). A1 family excluded. */
const CHAMBER_FAN_MODELS = new Set([
  ...CHAMBER_TEMP_MODELS,
  // P1S has a chamber filter fan but no chamber temp sensor
  'P1S', 'C12',
]);

const DUAL_NOZZLE_MODELS = new Set([
  'H2D', 'H2DPRO', 'H2C', 'X2D',
  'O1D', 'O1E', 'O2D', 'O1C', 'O1C2', 'N6',
]);

const AIRDUCT_MODELS = new Set([
  'P2S', 'X2D', 'H2D', 'H2DPRO', 'H2C', 'H2S',
  'N7', 'N6', 'O1D', 'O1C', 'O1C2', 'O1S', 'O1E', 'O2D',
]);

const A1_MODELS = new Set(['A1', 'A1MINI', 'N1', 'N2S', 'A11', 'A12', 'A04']);

export function isA1Family(model: string | null | undefined): boolean {
  const m = normalizePrinterModel(model);
  if (!m) return false;
  if (A1_MODELS.has(m)) return true;
  return m.startsWith('A1');
}

export function supportsChamberTemp(model: string | null | undefined): boolean {
  const m = normalizePrinterModel(model);
  if (!m || isA1Family(model)) return false;
  if (CHAMBER_TEMP_MODELS.has(m)) return true;
  if (m.includes('X1') && !m.includes('X1E')) return m.includes('X1C') || m === 'X1';
  if (m.includes('H2')) return true;
  if (m === 'P2S' || m === 'N7') return true;
  if (m.includes('X2')) return true;
  return false;
}

export function supportsChamberFan(model: string | null | undefined): boolean {
  const m = normalizePrinterModel(model);
  if (!m || isA1Family(model)) return false;
  if (CHAMBER_FAN_MODELS.has(m)) return true;
  if (supportsChamberTemp(model)) return true;
  if (m === 'P1S' || m === 'C12') return true;
  return false;
}

export function isDualNozzleModel(model: string | null | undefined): boolean {
  const m = normalizePrinterModel(model);
  if (!m) return false;
  if (isA1Family(model)) return false;
  return DUAL_NOZZLE_MODELS.has(m);
}

export function supportsAirductMode(model: string | null | undefined): boolean {
  const m = normalizePrinterModel(model);
  if (!m) return false;
  if (AIRDUCT_MODELS.has(m)) return true;
  return m.includes('H2') || m === 'P2S' || m.includes('X2D');
}

export interface PrinterControlCapabilities {
  showChamberTemp: boolean;
  showDualNozzle: boolean;
  showPartFan: boolean;
  showAuxFan: boolean;
  showChamberFan: boolean;
  showAirduct: boolean;
  showChamberLight: boolean;
}

export function getPrinterControlCapabilities(
  printer: Printer,
  status: PrinterStatus | undefined
): PrinterControlCapabilities {
  const model = printer.model;
  const connected = status?.connected ?? false;

  return {
    showChamberTemp: supportsChamberTemp(model),
    showDualNozzle: isDualNozzleModel(model),
    showPartFan: connected,
    // Auxiliary fan is read-only in the UI; only part + chamber are user-controllable.
    showAuxFan: false,
    showChamberFan: connected && supportsChamberFan(model),
    showAirduct: supportsAirductMode(model),
    showChamberLight: true,
  };
}

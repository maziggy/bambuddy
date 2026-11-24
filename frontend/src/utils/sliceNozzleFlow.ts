/**
 * The nozzle flow a slice is cut for: Standard or High Flow (#3136).
 *
 * Sliced with profiles, the slicer cuts every extruder for a Standard nozzle
 * unless it is told otherwise, and a printer with a High Flow nozzle then
 * warns that the file does not match it. The Slice dialog offers the choice
 * and takes its default from the first online printer of the profile's model,
 * by name, whose nozzles report their flow at the profile's nozzle size: the
 * same rule the backend uses when a request leaves the flow out.
 *
 * The model lists mirror `backend/app/utils/printer_models.py`
 * (`SINGLE_NOZZLE_FLOW_MODELS`, `DUAL_NOZZLE_MODELS`, `NOZZLE_RACK_MODELS`).
 */
import type { LoadedSpoolPrinter } from '../api/client';

export const NOZZLE_FLOWS = ['Standard', 'High Flow'] as const;
export type NozzleFlowName = (typeof NOZZLE_FLOWS)[number];

const normalise = (model: string) => model.toUpperCase().replace(/[^A-Z0-9]/g, '');

// Sold with one nozzle flow only, so there is nothing to choose.
const SINGLE_FLOW_MODELS: ReadonlySet<string> = new Set([
  'A1', 'A1MINI', 'A2L', 'A1M', 'N1', 'N2S', 'N9', 'A04', 'A11', 'A12',
]);
const DUAL_NOZZLE_MODELS: ReadonlySet<string> = new Set([
  'H2D', 'H2DPRO', 'H2C', 'X2D', 'O1D', 'O1E', 'O2D', 'O1C', 'O1C2', 'N6',
]);
// Changes hotends during a print, so no one nozzle's flow is the job's.
const NOZZLE_RACK_MODELS: ReadonlySet<string> = new Set(['H2C', 'O1C', 'O1C2']);

/** Whether a model is sold with both Standard and High Flow nozzles. */
export function hasNozzleFlowChoice(model: string | null | undefined): boolean {
  return !!model && !SINGLE_FLOW_MODELS.has(normalise(model));
}

/** How many extruders the slicer gives the model. */
export function extruderCount(model: string | null | undefined): number {
  return model && DUAL_NOZZLE_MODELS.has(normalise(model)) ? 2 : 1;
}

/** The nozzle size in a profile name ("Bambu Lab P2S 0.8 nozzle"), or null. */
export function profileNozzleDiameter(name: string | null | undefined): string | null {
  const match = /(\d+(?:\.\d+)?)\s*nozzle/i.exec(name ?? '');
  return match ? match[1] : null;
}

function sameSize(a: string | null | undefined, b: string): boolean {
  const x = Number.parseFloat(a ?? '');
  return Number.isFinite(x) && Math.abs(x - Number.parseFloat(b)) < 1e-6;
}

/**
 * The flows the first printer that can tell reports, one per slicer extruder,
 * with the printer's name; null when none can.
 *
 * `printers` are the online printers of the profile's model, in the order the
 * backend lists them (by name). Slicer extruder 1 of a Bambu dual-nozzle
 * printer is its left nozzle, MQTT extruder id 1; extruder 2 is id 0.
 */
export function defaultNozzleFlows(
  printers: LoadedSpoolPrinter[],
  model: string | null | undefined,
  diameter: string | null,
): { flows: NozzleFlowName[]; printerName: string } | null {
  if (!hasNozzleFlowChoice(model) || (model && NOZZLE_RACK_MODELS.has(normalise(model)))) return null;
  const mqttIds = extruderCount(model) === 2 ? [1, 0] : [0];
  for (const printer of printers) {
    const flows: NozzleFlowName[] = [];
    for (const id of mqttIds) {
      const nozzle = (printer.nozzles ?? []).find((n) => n.extruder_id === id);
      if (!nozzle || !nozzle.flow || !NOZZLE_FLOWS.includes(nozzle.flow as NozzleFlowName)) break;
      if (diameter != null && !sameSize(nozzle.diameter, diameter)) break;
      flows.push(nozzle.flow as NozzleFlowName);
    }
    if (flows.length === mqttIds.length) return { flows, printerName: printer.name };
  }
  return null;
}

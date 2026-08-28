/**
 * Nozzle flow type: High Flow vs Standard.
 *
 * A printer files each calibration profile under a nozzle id of the form
 * `HH00-0.4` (high flow) or `HS00-0.4` (standard), so on a machine that sells
 * both, the flow is part of a K profile's identity — the same filament reads a
 * different K value through each.
 *
 * Two spellings have to reduce to the same answer, which is why this compares
 * two characters rather than four:
 *
 *   - a calibration entry says `HH00-0.4`
 *   - the fitted nozzle reports its type as `HH01`
 *
 * Both measured on an H2D; the trailing digits are a hardware variant that the
 * calibration table normalises to `00`.
 *
 * And it can legitimately be absent. An X1C answers `extrusion_cali_get` with
 * `nozzle_id: ''` on every profile — measured, all eight — even though the
 * machine really does take either nozzle. So "no flow" means *unknown*, never
 * Standard: inventing a value here and then filtering on it would drop every
 * X1C profile the moment a high-flow nozzle was fitted. (BambuStudio's own
 * parser defaults a missing id to Standard for *display*, which is fine for a
 * label and wrong for a lookup key.)
 */

export type NozzleFlow = 'HH' | 'HS';

/** The flow code in a nozzle id (`HH00-0.4`) or a nozzle type (`HH01`). */
export function normaliseFlow(raw: string | null | undefined): NozzleFlow | null {
  const code = (raw ?? '').trim().toUpperCase().slice(0, 2);
  return code === 'HH' || code === 'HS' ? code : null;
}

/** Short label for a flow code, or null when there is nothing to say. */
export function flowLabel(flow: NozzleFlow | null): string | null {
  if (!flow) return null;
  return flow === 'HH' ? 'HF' : 'S';
}

/**
 * Whether a stored K profile's flow applies to the nozzle now fitted.
 *
 * Unknown on either side matches: every profile stored before flow was
 * recorded has none, as does every profile from a printer whose table omits it.
 * Mirrors `SlotNozzle.flow_matches` on the backend, which is what actually
 * decides at assign time — this is the same rule for the picker's benefit.
 */
export function flowApplies(
  storedFlow: string | null | undefined,
  fittedFlow: string | null | undefined,
): boolean {
  const stored = normaliseFlow(storedFlow);
  const fitted = normaliseFlow(fittedFlow);
  if (!stored || !fitted) return true;
  return stored === fitted;
}

/**
 * Which part of the printer the Z axis actually moves.
 *
 * This is a question about the machine standing in front of the user, not
 * about G-code. `G1 Z+` opens the nozzle-bed gap on every Bambu model — that
 * is what the axis means — so nothing on the wire depends on the answer, and
 * `POST /printers/{id}/bed-jog` takes a signed gap that means one physical
 * thing everywhere (see its docstring for the #1334 history).
 *
 * The answer is needed anyway, because a jog button carries an arrow, and an
 * arrow promises the user a direction of *motion*. On an X1 / P1 / H2 the
 * plate itself rides the Z axis, so "up" is the plate climbing toward the
 * nozzle and the gap closing. On the A1 family and the A2L the plate only
 * moves in Y; Z carries the toolhead, so "up" is the toolhead lifting off the
 * plate and the gap opening. Same arrow, opposite gap.
 *
 * The model list mirrors the A-series entries in the backend registries
 * (`LINEAR_RAIL_MODELS` / `SINGLE_NOZZLE_FLOW_MODELS` in
 * `backend/app/utils/printer_models.py`, and `PRINTER_MODEL_ID_MAP` for the
 * codes). A new bed-slinger has to be added here too.
 */

/**
 * Models whose Z axis carries the toolhead, normalised (upper-case, letters
 * and digits only) so display names, internal MQTT/SSDP codes and Bambu's
 * terser cloud renames all land on the same entry.
 *
 * Kept as an explicit list rather than a prefix match: "A2L" and "A1" share a
 * letter with nothing else today, but "A" would sweep in whatever the next
 * A-series machine turns out to be, and the wrong answer here points an arrow
 * at someone's plate.
 */
const BED_SLINGER_MODELS: ReadonlySet<string> = new Set([
  // Display names
  'A1',
  'A1MINI',
  'A2L',
  // Bambu cloud short code for the A1 Mini (#1649)
  'A1M',
  // Internal MQTT / SSDP codes
  'N1', // A1 Mini
  'N2S', // A1
  'N9', // A2L
  'A04', // A1 Mini (alternate)
  'A11', // A1
  'A12', // A1 Mini
]);

/** True when this printer's Z axis moves the toolhead rather than the plate. */
export function isBedSlinger(model: string | null | undefined): boolean {
  if (!model) return false;
  return BED_SLINGER_MODELS.has(model.toUpperCase().replace(/[^A-Z0-9]/g, ''));
}

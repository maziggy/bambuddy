import type { FilamentPolicy, FilamentRule } from '../api/client';
import { canonicalFilamentType } from './amsHelpers';

// Mirrors backend/app/core/filament_scope.py (#3522): the server enforces the
// same rules, so the print dialog only shows ahead of time what it would do.

const norm = (value?: string | null) => (value ?? '').trim().toUpperCase();

/** Whether a filament with this type and slicer filament id may be used. */
export function filamentAllowed(
  policies: FilamentPolicy[] | null | undefined,
  type?: string | null,
  filamentId?: string | null,
): boolean {
  if (policies == null) return true;
  const t = norm(type);
  const id = norm(filamentId);
  if (!t && !id) return true;
  return policies.some((policy) => {
    const hit = policy.rules.some((rule) => {
      const wanted = norm(rule.value);
      if (!wanted) return false;
      // A material rule names every type the tray matcher treats as that
      // material (PA-CF, PA12-CF and PAHT-CF are one), as the server does.
      return rule.match === 'material' ? canonicalFilamentType(t) === canonicalFilamentType(wanted) : id === wanted;
    });
    return policy.mode === 'allow' ? hit : !hit;
  });
}

/** `loaded` with `blocked` set on each tray the policies don't allow; the same array when unrestricted. */
export function markBlocked<T extends { type?: string; trayInfoIdx?: string }>(
  loaded: T[],
  policies: FilamentPolicy[] | null | undefined,
): (T & { blocked?: boolean })[] {
  if (policies == null) return loaded;
  return loaded.map((f) => (filamentAllowed(policies, f.type, f.trayInfoIdx) ? f : { ...f, blocked: true }));
}

/**
 * How the print dialog names a filament (#3522): `TYPE (ID)`, `TYPE` or `ID`.
 * Mirrors `describe_filament` on the server, so the dialog and the 403 agree.
 */
export function filamentName(type?: string | null, filamentId?: string | null): string {
  const kind = (type ?? '').trim();
  const id = (filamentId ?? '').trim();
  if (kind && id) return `${kind} (${id})`;
  return kind || id;
}

/**
 * `rules` with `{ match, value }` added as the group editor stores it: trimmed,
 * a material upper-cased, and not twice. The same array when there is nothing
 * to add (#3522).
 */
export function withFilamentRule(rules: FilamentRule[], match: FilamentRule['match'], value: string): FilamentRule[] {
  const trimmed = value.trim();
  if (!trimmed) return rules;
  const stored = match === 'material' ? trimmed.toUpperCase() : trimmed;
  if (rules.some((r) => r.match === match && r.value.toUpperCase() === stored.toUpperCase())) return rules;
  return [...rules, { match, value: stored }];
}

/** A filament override as a queue job carries it (`filament_overrides`). */
interface JudgedOverride {
  slot_id: number;
  type: string;
  tray_info_idx?: string | null;
  force_color_match?: boolean;
}

/**
 * The file's filaments as the rules judge them once the job's overrides apply
 * (#3522). Mirrors `judged_requirements` on the server: an overridden slot is
 * judged as its override names it, with its filament id only for a forced
 * colour (a swap clears the id), and one left naming no id isn't judged here:
 * the tray matcher never maps it onto a spool the user may not use.
 */
export function judgedFilaments<T extends { slot_id: number; type: string; tray_info_idx?: string | null }>(
  reqs: T[] | undefined,
  overrides: JudgedOverride[] | undefined,
): T[] {
  const bySlot = new Map((overrides ?? []).map((o) => [o.slot_id, o]));
  return (reqs ?? []).flatMap((req) => {
    const override = bySlot.get(req.slot_id);
    if (!override) return [req];
    const id = override.force_color_match ? (override.tray_info_idx ?? '').trim() : '';
    return id ? [{ ...req, type: override.type, tray_info_idx: id }] : [];
  });
}

/** Names of the requirements the policies don't allow, each once, in order. */
export function blockedFilamentNames(
  reqs: { type: string; tray_info_idx?: string | null }[] | undefined,
  policies: FilamentPolicy[] | null | undefined,
): string[] {
  const names = (reqs ?? [])
    .filter((req) => !filamentAllowed(policies, req.type, req.tray_info_idx))
    .map((req) => filamentName(req.type, req.tray_info_idx));
  return [...new Set(names)];
}

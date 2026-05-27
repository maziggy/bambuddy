/**
 * Resolve a Bambu Lab wiki URL for a maintenance task based on the printer model.
 *
 * Model families:
 *   - X1, P1         → carbon rods
 *   - P2S, X2D       → hardened steel rods (X2D shares P2S's gantry — #988)
 *   - A1, A1 Mini    → linear rails (Y axis)
 *   - H2D, H2C, H2S  → linear rails (X-axis lubrication)
 *
 * Returns null when no wiki page applies (e.g. "Clean Carbon Rods" on an H2D),
 * which the caller renders as a task with no clickable help link.
 */
export function getMaintenanceWikiUrl(typeName: string, printerModel: string | null): string | null {
  const model = (printerModel || '').toUpperCase().replace(/[- ]/g, '');

  const isX1 = model.includes('X1');
  const isP1 = model.includes('P1');
  const isA1Mini = model.includes('A1MINI');
  const isA1 = model.includes('A1') && !isA1Mini;
  const isH2D = model.includes('H2D');
  const isH2C = model.includes('H2C');
  const isH2S = model.includes('H2S');
  const isH2 = isH2D || isH2C || isH2S;
  const isP2S = model.includes('P2S');
  const isX2D = model.includes('X2D');
  // X2D shares the hardened steel rod hardware and belt layout with P2S,
  // so its maintenance routes use the P2S wiki pages until dedicated
  // X2D pages are published by Bambu Lab.
  const isSteelRod = isP2S || isX2D;

  switch (typeName) {
    case 'Lubricate Steel Rods':
      if (isSteelRod) return 'https://wiki.bambulab.com/en/p2s/maintenance/lubricate-x-y-z-axis';
      return null;

    case 'Clean Steel Rods':
      if (isSteelRod) return 'https://wiki.bambulab.com/en/p2s/maintenance/lubricate-x-y-z-axis';
      return null;

    case 'Lubricate Linear Rails':
      if (isA1Mini) return 'https://wiki.bambulab.com/en/a1-mini/maintenance/lubricate-y-axis';
      if (isA1) return 'https://wiki.bambulab.com/en/a1/maintenance/lubricate-y-axis';
      if (isH2) return 'https://wiki.bambulab.com/en/h2/maintenance/x-axis-lubrication';
      return null;

    case 'Clean Nozzle/Hotend':
      if (isX1 || isP1) return 'https://wiki.bambulab.com/en/x1/troubleshooting/nozzle-clog';
      if (isA1Mini || isA1) return 'https://wiki.bambulab.com/en/a1-mini/troubleshooting/nozzle-clog';
      if (isH2) return 'https://wiki.bambulab.com/en/h2/maintenance/nozzl-cold-pull-maintenance-and-cleaning';
      if (isSteelRod) return 'https://wiki.bambulab.com/en/p2s/maintenance/cold-pull-maintenance-hotend';
      return 'https://wiki.bambulab.com/en/x1/troubleshooting/nozzle-clog';

    case 'Check Belt Tension':
      if (isX1) return 'https://wiki.bambulab.com/en/x1/maintenance/belt-tension';
      if (isP1) return 'https://wiki.bambulab.com/en/p1/maintenance/p1p-maintenance';
      if (isA1Mini) return 'https://wiki.bambulab.com/en/a1-mini/maintenance/belt_tension';
      if (isA1) return 'https://wiki.bambulab.com/en/a1/maintenance/belt_tension';
      if (isH2D) return 'https://wiki.bambulab.com/en/h2/maintenance/belt-tension';
      if (isH2C) return 'https://wiki.bambulab.com/en/h2c/maintenance/belt-tension';
      if (isH2S) return 'https://wiki.bambulab.com/en/h2s/maintenance/belt-tension';
      if (isSteelRod) return 'https://wiki.bambulab.com/en/p2s/maintenance/belt-tension';
      return 'https://wiki.bambulab.com/en/x1/maintenance/belt-tension';

    case 'Clean Carbon Rods':
      if (isX1 || isP1) return 'https://wiki.bambulab.com/en/general/carbon-rods-clearance';
      return null;

    case 'Clean Linear Rails':
      if (isA1Mini) return 'https://wiki.bambulab.com/en/a1-mini/maintenance/lubricate-y-axis';
      if (isA1) return 'https://wiki.bambulab.com/en/a1/maintenance/lubricate-y-axis';
      if (isH2) return 'https://wiki.bambulab.com/en/h2/maintenance/x-axis-lubrication';
      return null;

    case 'Clean Build Plate':
      return 'https://wiki.bambulab.com/en/filament-acc/acc/pei-plate-clean-guide';

    case 'Check PTFE Tube':
      if (isX1 || isP1) return 'https://wiki.bambulab.com/en/x1/maintenance/replace-ptfe-tube';
      if (isA1Mini || isA1) return 'https://wiki.bambulab.com/en/a1-mini/maintenance/ptfe-tube';
      if (isH2D) return 'https://wiki.bambulab.com/en/h2/maintenance/replace-ptfe-tube-on-h2d-printer';
      if (isH2S) return 'https://wiki.bambulab.com/en/h2s/maintenance/replace-ptfe-tube-on-h2s-printer';
      if (isH2C) return 'https://wiki.bambulab.com/en/h2/maintenance/replace-ptfe-tube-on-h2d-printer'; // H2C uses H2D guide
      if (isSteelRod) return 'https://wiki.bambulab.com/en/x1/maintenance/replace-ptfe-tube'; // P2S/X2D use similar PTFE
      return 'https://wiki.bambulab.com/en/x1/maintenance/replace-ptfe-tube';

    case 'Replace HEPA Filter':
    case 'HEPA Filter':
    case 'Replace Carbon Filter':
    case 'Carbon Filter':
      if (isH2) return 'https://wiki.bambulab.com/en/h2/maintenance/replace-smoke-purifier-air-filte';
      return 'https://wiki.bambulab.com/en/x1/maintenance/replace-carbon-filter';

    case 'Lubricate Left Nozzle Rail':
    case 'Left Nozzle Rail':
      if (isH2) return 'https://wiki.bambulab.com/en/h2/maintenance/x-axis-lubrication';
      return null;

    default:
      return null;
  }
}

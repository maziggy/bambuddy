import { describe, it, expect } from 'vitest';
import * as THREE from 'three';

import { parseGcodeToolpath, layersByFilament, filterLayersByType, ToolpathType } from '../../lib/gcodeToolpath';
// @ts-expect-error -- vendored build output; typed by its sibling .d.ts, which
// vitest's resolver does not pick up for a bare .js import.
import { buildSegmentData, makeToolpath, TYPE_COLOR } from '../../lib/vendor/toolpathRenderer.js';

/**
 * A minimal but realistic slice: two layers, an outer wall square and some
 * infill on each, with a travel between them. Written the way Bambu and Orca
 * actually emit -- relative extrusion, `;TYPE:` before each run, `;WIDTH:`,
 * and a bare Z move for the layer change.
 */
const GCODE = `
;TYPE:Custom
M83
G1 Z0.2 F600
;TYPE:Outer wall
;WIDTH:0.42
G1 X0 Y0 F1200
G1 X10 Y0 E0.5
G1 X10 Y10 E0.5
G1 X0 Y10 E0.5
G1 X0 Y0 E0.5
;TYPE:Sparse infill
G1 X2 Y2 F9000
G1 X8 Y8 E0.3
G1 Z0.4 F600
;TYPE:Outer wall
G1 X0 Y0 F1200
G1 X10 Y0 E0.5
G1 X10 Y10 E0.5
;TYPE:Support
G1 X20 Y20 E0.4
`;

describe('parseGcodeToolpath', () => {
  const parsed = parseGcodeToolpath(GCODE);

  it('splits the file into layers in print order', () => {
    expect(parsed.layers.length).toBe(2);
    expect(parsed.layers[0].z).toBeCloseTo(0.2);
    expect(parsed.layers[1].z).toBeCloseTo(0.4);
  });

  it('counts extrusions and travels separately', () => {
    // 4 wall + 1 infill on layer 1; 2 wall + 1 support on layer 2.
    expect(parsed.segmentCount).toBe(8);
    // The two repositioning moves before a run, plus the initial Z lift.
    expect(parsed.travelCount).toBeGreaterThan(0);
  });

  it('classifies features from the ;TYPE: annotations', () => {
    const types = (layer: number) => {
      const p = parsed.layers[layer].paths;
      const out: number[] = [];
      for (let i = 0; i < p.length; i += 8) out.push(p[i + 3]);
      return out;
    };
    expect(types(0)).toContain(ToolpathType.wall);
    expect(types(0)).toContain(ToolpathType.sparseInfill);
    expect(types(1)).toContain(ToolpathType.support);
  });

  it('marks non-extruding moves as travel', () => {
    const p = parsed.layers[0].paths;
    const travels: number[] = [];
    for (let i = 0; i < p.length; i += 8) {
      if (p[i + 3] === ToolpathType.travel) travels.push(i);
    }
    expect(travels.length).toBeGreaterThan(0);
  });

  it('reads the extrusion width out of the file rather than assuming one', () => {
    expect(parsed.defaultWidth).toBeCloseTo(0.42);
  });

  it('handles absolute extrusion as well as relative', () => {
    // Without M83, E values are cumulative; treating them as relative would
    // make every move after the first look like a huge extrusion, and a
    // retraction would read as an extrusion rather than a travel.
    const absolute = parseGcodeToolpath(`
;TYPE:Outer wall
M82
G1 X0 Y0 Z0.2
G1 X10 Y0 E1.0
G1 X10 Y10 E2.0
G1 X0 Y10 E1.5
`);
    // Two extrusions (E rising), then one retraction (E falling) as a travel.
    expect(absolute.segmentCount).toBe(2);
    expect(absolute.travelCount).toBeGreaterThan(0);
  });

  it('keeps geometry for an unrecognised feature name', () => {
    // A type we do not know must never drop the move -- a hole in the preview
    // is far worse than a wrongly coloured segment.
    const unknown = parseGcodeToolpath(`
M83
;TYPE:Some Future Feature
G1 X0 Y0 Z0.2
G1 X5 Y5 E0.4
`);
    expect(unknown.segmentCount).toBe(1);
  });

  it('reports the model bounds', () => {
    expect(parsed.bounds).not.toBeNull();
    expect(parsed.bounds!.max[0]).toBeCloseTo(20);
    expect(parsed.bounds!.max[1]).toBeCloseTo(20);
  });

  it('returns nothing rather than throwing on a file with no moves', () => {
    const empty = parseGcodeToolpath('; just a comment\nM104 S200\n');
    expect(empty.layers).toEqual([]);
    expect(empty.bounds).toBeNull();
  });
});

describe('motion coordinates and comments', () => {
  const start = 'G90\nM83\nG0 X50 Y40 Z1\n';

  it('draws the same path with absolute and relative endpoints', () => {
    const absolute = parseGcodeToolpath(start + 'G1 X55 E1\nG1 Y47 E1');
    const relative = parseGcodeToolpath(start + 'G91\nG1 X5 E1\nG1 Y7 E1');
    expect(relative.segmentCount).toBe(2);
    expect(relative.layers).toEqual(absolute.layers);
    expect(relative.bounds?.max).toEqual([55, 47, 1]);
  });

  it('resumes absolute travel after a relative start sequence', () => {
    const parsed = parseGcodeToolpath(start + 'G91\nG1 Y-16\nG0 Z5\nG90\nG1 Y20');
    const data = buildSegmentData(parsed.layers, parsed.defaultWidth);
    expect(Array.from(data.travelPos)).toEqual([
      50, 40, 1, 50, 24, 1,
      50, 24, 1, 50, 24, 6,
      50, 24, 6, 50, 20, 6,
    ]);
  });

  it('keeps absolute extrusion active across XYZ mode changes', () => {
    const parsed = parseGcodeToolpath(start + 'M82\nG1 X51 E2\nG91\nG1 X1 E1\nG90\nG1 X53 E3');
    const data = buildSegmentData(parsed.layers, parsed.defaultWidth);
    expect(data.nSeg).toBe(2);
    expect(Array.from(data.travelPos)).toEqual([51, 40, 1, 52, 40, 1]);
  });

  it.each(['G2', 'G3'])('resolves %s endpoints and keeps I/J relative to the start', (arc) => {
    const absolute = parseGcodeToolpath(start + arc + ' X60 I5 Z3');
    const relative = parseGcodeToolpath(start + 'G91\n' + arc + ' X10 I5 Z2');
    const data = buildSegmentData(relative.layers, relative.defaultWidth);
    expect(relative.layers).toEqual(absolute.layers);
    expect(data.nTrav).toBeGreaterThan(1);
    expect(Array.from(data.travelPos.slice(-3))).toEqual([60, 40, 3]);
    for (let i = 0; i < data.nTrav; i++) {
      expect(data.travelPos[i * 6 + 5]).toBeCloseTo(1 + 2 * (i + 1) / data.nTrav, 5);
    }
  });

  it('ignores a trailing comment while retaining full-line slicer annotations', () => {
    const moves = ['G1 Y47', 'G1 Y47 ; next X55 E1', '; next X55 E1\nG1 Y47'];
    const meshes = moves.map((move) => {
      const parsed = parseGcodeToolpath(start + move + '\n; CHANGE_LAYER\n; FEATURE: Support\nG1 X51 E1');
      return buildSegmentData(parsed.layers, parsed.defaultWidth);
    });
    expect(meshes[1]).toEqual(meshes[0]);
    expect(meshes[2]).toEqual(meshes[0]);
    expect(Array.from(meshes[0].travelPos)).toEqual([50, 40, 1, 50, 47, 1]);
    expect(meshes[0].layerCount).toBe(2);
    expect(Array.from(meshes[0].meta.vType)).toEqual([ToolpathType.support, ToolpathType.support]);
  });
});

describe('the vendored libvgcode renderer accepts our parse', () => {
  const parsed = parseGcodeToolpath(GCODE);

  it('builds segment data from the parsed layers', () => {
    const data = buildSegmentData(parsed.layers, parsed.defaultWidth);
    expect(data.nSeg).toBe(parsed.segmentCount);
    expect(data.layerCount).toBe(2);
    expect(data.hasNaN).toBe(false);
    expect(data.bbox).not.toBeNull();
  });

  it('carries travel moves through as a separate stream', () => {
    const data = buildSegmentData(parsed.layers, parsed.defaultWidth);
    expect(data.nTrav).toBe(parsed.travelCount);
  });

  it('keeps per-vertex feature, width and layer metadata', () => {
    const data = buildSegmentData(parsed.layers, parsed.defaultWidth);
    expect(data.meta.vType.length).toBe(data.nV);
    expect(Array.from(data.meta.vType)).toContain(ToolpathType.wall);
    expect(Array.from(data.meta.vLayer)).toContain(1);
    // Width came from ;WIDTH:, not the fallback.
    expect(Array.from(data.meta.vWidth).some((w) => Math.abs(w - 0.42) < 1e-6)).toBe(true);
  });

  it('builds a three.js mesh on our own three version', () => {
    // The whole reason this renderer is usable: it imports no three and takes
    // the namespace as an argument, so it runs on our 0.181 rather than the
    // 0.160 its own package pins.
    const data = buildSegmentData(parsed.layers, parsed.defaultWidth);
    const handle = makeToolpath(THREE, data);

    expect(handle.mesh).toBeInstanceOf(THREE.Mesh);
    expect(handle.nSeg).toBe(parsed.segmentCount);
    expect(handle.layerCount).toBe(2);

    const geometry = handle.mesh.geometry as THREE.BufferGeometry;
    // The drawn primitive is libvgcode's diamond cross-section: 8 triangles,
    // 24 indices, instanced once per segment. That single indexed draw is what
    // keeps a million-segment print to one call.
    expect(geometry.index?.count).toBe(24);
    expect(geometry.attributes.seg_id_a_u.count).toBe(parsed.segmentCount);
    expect(geometry.attributes.seg_layer_u.count).toBe(parsed.segmentCount);

    handle.dispose();
  });

  it('exposes layer-range and travel controls', () => {
    const data = buildSegmentData(parsed.layers, parsed.defaultWidth);
    const handle = makeToolpath(THREE, data);

    expect(() => handle.setLayerRange(0, 0)).not.toThrow();
    expect(() => handle.setTravelVisible(true)).not.toThrow();
    expect(handle.travLines.visible).toBe(true);
    expect(() => handle.setTravelVisible(false)).not.toThrow();
    expect(handle.travLines.visible).toBe(false);

    handle.dispose();
  });

  it('ships the libvgcode feature palette', () => {
    // Sanity that the vendored module is the real thing and not a stub.
    expect(TYPE_COLOR[ToolpathType.wall]).toHaveLength(3);
    expect(TYPE_COLOR[ToolpathType.support]).toHaveLength(3);
  });
});

/**
 * BambuStudio's dialect. It does not emit any of the annotations the
 * OrcaSlicer/PrusaSlicer lineage uses -- no `;TYPE:`, no `;WIDTH:`, no
 * `;LAYER_CHANGE` -- and reading only those rendered a real Bambu file as one
 * undifferentiated colour with a layer per travel Z-hop (52 layers came out as
 * 23,165). Taken from an actual sliced plate.
 */
const BAMBU_GCODE = `
M83
; CHANGE_LAYER
; Z_HEIGHT: 0.2
; LINE_WIDTH: 0.42
; FEATURE: Outer wall
G1 X10 Y10 Z0.2 F600
G1 X20 Y10 E0.5
G1 X20 Y20 E0.5
; FEATURE: Sparse infill
G1 X12 Y12 F9000
G1 X18 Y18 E0.3
; a travel Z-hop, which must not start a layer
G1 Z0.6 F600
G1 X30 Y30 F9000
G1 Z0.2 F600
; FEATURE: Support
G1 X31 Y31 E0.2
; CHANGE_LAYER
; Z_HEIGHT: 0.36
; FEATURE: Outer wall
G1 X10 Y10 Z0.36 F600
G1 X20 Y10 E0.5
`;

describe('parseGcodeToolpath — BambuStudio dialect', () => {
  const parsed = parseGcodeToolpath(BAMBU_GCODE);

  it('reads features from "; FEATURE:" rather than ";TYPE:"', () => {
    const allTypes = parsed.layers.flatMap((layer) => {
      const out: number[] = [];
      for (let i = 0; i < layer.paths.length; i += 8) out.push(layer.paths[i + 3]);
      return out;
    });
    expect(allTypes).toContain(ToolpathType.sparseInfill);
    expect(allTypes).toContain(ToolpathType.support);
    // Everything falling back to `wall` is the signature of the dialect bug.
    expect(new Set(allTypes).size).toBeGreaterThan(2);
  });

  it('uses the explicit layer markers', () => {
    expect(parsed.layers.length).toBe(2);
    expect(parsed.layers[1].z).toBeCloseTo(0.36);
  });

  it('does not start a layer on a travel Z-hop', () => {
    // The hop to Z0.6 and back sits inside layer one; splitting there is what
    // multiplied the layer count by four hundred.
    const firstLayerTypes: number[] = [];
    const p = parsed.layers[0].paths;
    for (let i = 0; i < p.length; i += 8) firstLayerTypes.push(p[i + 3]);
    expect(firstLayerTypes).toContain(ToolpathType.support);
  });

  it('reads the width from "; LINE_WIDTH:"', () => {
    expect(parsed.defaultWidth).toBeCloseTo(0.42);
  });

  it('takes the typical width, not the widest', () => {
    // Widths in a real file span a 0.09 gap fill to a 1.0 purge line; the max
    // made every fallback segment absurdly fat.
    const mixed = parseGcodeToolpath(`
M83
; FEATURE: Outer wall
; LINE_WIDTH: 0.42
G1 X0 Y0 Z0.2
G1 X10 Y0 E0.5
G1 X10 Y10 E0.5
; LINE_WIDTH: 1.0
G1 X0 Y10 E0.5
`);
    expect(mixed.defaultWidth).toBeCloseTo(0.42);
  });

  it('does not draw a phantom segment from the origin', () => {
    // Position is unknown until the first move sets it; extruding from (0,0,0)
    // drew a stray line across the bed.
    const p = parsed.layers[0].paths;
    let touchesOrigin = false;
    for (let i = 0; i < p.length; i += 8) {
      if (p[i] === 0 && p[i + 1] === 0 && p[i + 3] !== ToolpathType.travel) touchesOrigin = true;
    }
    expect(touchesOrigin).toBe(false);
  });
});

describe('arc moves and filament tracking', () => {
  // BambuStudio has arc fitting on by default. A real plate carried 706
  // extruding G2/G3 moves against ~7800 linear ones, and dropping them left
  // holes through curved walls and tree supports -- the "huge gaps in the
  // support structure" this was reported as.
  const ARC_GCODE = `
M83
; FEATURE: Outer wall
; LINE_WIDTH: 0.42
G1 X10 Y0 Z0.2 F600
G3 X0 Y10 I-10 J0 E1.0
`;

  it('interpolates an arc into chords rather than dropping it', () => {
    const parsed = parseGcodeToolpath(ARC_GCODE);
    // A quarter circle of radius 10 at a 0.02mm chord tolerance is many
    // segments; the point is that it is neither 0 nor 1.
    expect(parsed.segmentCount).toBeGreaterThan(5);
  });

  it('keeps every interpolated chord on the arc', () => {
    const parsed = parseGcodeToolpath(ARC_GCODE);
    const centre = { x: 0, y: 0 };
    for (const layer of parsed.layers) {
      for (let i = 0; i < layer.paths.length; i += 8) {
        if (layer.paths[i + 3] === ToolpathType.travel) continue;
        const r = Math.hypot(layer.paths[i + 4] - centre.x, layer.paths[i + 5] - centre.y);
        // Every endpoint sits on the radius, within the chord tolerance.
        expect(Math.abs(r - 10)).toBeLessThan(0.1);
      }
    }
  });

  it('treats an arc with no X or Y as the helical travel lift it is', () => {
    // "G3 Z0.4 I1.2 J0 P1" is BambuStudio lifting the nozzle in a spiral. It
    // extrudes nothing and must not be mistaken for geometry.
    const parsed = parseGcodeToolpath(`
M83
; FEATURE: Outer wall
G1 X10 Y10 Z0.2 F600
G1 X20 Y10 E0.5
G3 Z0.6 I1.217 J0 P1 F60000
`);
    expect(parsed.segmentCount).toBe(1);
    expect(parsed.travelCount).toBeGreaterThan(1);
  });

  it('does not mistake G20 or G28 for an arc', () => {
    const parsed = parseGcodeToolpath(`
M83
G21
G28
; FEATURE: Outer wall
G1 X10 Y10 Z0.2
G1 X20 Y10 E0.5
`);
    expect(parsed.segmentCount).toBe(1);
  });

  it('tracks the active filament across tool changes', () => {
    const parsed = parseGcodeToolpath(`
M83
; FEATURE: Outer wall
T0
G1 X10 Y10 Z0.2 F600
G1 X20 Y10 E0.5
T1
G1 X20 Y20 E0.5
`);
    const tools: number[] = [];
    for (const layer of parsed.layers) {
      for (let i = 0; i < layer.paths.length; i += 8) {
        if (layer.paths[i + 3] !== ToolpathType.travel) tools.push(layer.paths[i + 7]);
      }
    }
    expect(tools).toContain(0);
    expect(tools).toContain(1);
  });

  it("ignores BambuStudio's sentinel tool numbers", () => {
    // T65535 / T65279 bracket the slicer's own bookkeeping and are not
    // filaments; treating them as such would key colours off a nonsense slot.
    const parsed = parseGcodeToolpath(`
M83
; FEATURE: Outer wall
T0
G1 X10 Y10 Z0.2 F600
T65535
G1 X20 Y10 E0.5
`);
    for (const layer of parsed.layers) {
      for (let i = 0; i < layer.paths.length; i += 8) {
        expect(layer.paths[i + 7]).toBeLessThanOrEqual(15);
      }
    }
  });

  it('re-keys types to filaments for the filament-coloured view', () => {
    const parsed = parseGcodeToolpath(`
M83
; FEATURE: Support
T1
G1 X10 Y10 Z0.2 F600
G1 X20 Y10 E0.5
`);
    const recoloured = layersByFilament(parsed.layers);
    const typeOf = (layers: typeof parsed.layers) => {
      for (const layer of layers) {
        for (let i = 0; i < layer.paths.length; i += 8) {
          if (layer.paths[i + 3] !== ToolpathType.travel) return layer.paths[i + 3];
        }
      }
      return -1;
    };
    expect(typeOf(parsed.layers)).toBe(ToolpathType.support);
    // Filament 1 becomes type 2 -- offset by one so slot 0 cannot collide
    // with the travel index.
    expect(typeOf(recoloured)).toBe(2);
    // The original must be untouched: both colourings are held at once.
    expect(typeOf(parsed.layers)).toBe(ToolpathType.support);
  });
});

describe('hiding a feature or filament', () => {
  const GCODE = `
M83
; LINE_WIDTH: 0.42
; FEATURE: Outer wall
T0
G1 X10 Y10 Z0.2 F600
G1 X20 Y10 E0.5
; FEATURE: Support
T1
G1 X30 Y10 E0.5
G1 X40 Y10 E0.5
`;

  const typesOf = (layers: ReturnType<typeof parseGcodeToolpath>['layers']) => {
    const out: number[] = [];
    for (const layer of layers) {
      for (let i = 0; i < layer.paths.length; i += 8) out.push(layer.paths[i + 3]);
    }
    return out;
  };

  it('removes the hidden feature and keeps the rest', () => {
    const parsed = parseGcodeToolpath(GCODE);
    const filtered = filterLayersByType(parsed.layers, new Set([ToolpathType.support]));
    expect(typesOf(filtered)).not.toContain(ToolpathType.support);
    expect(typesOf(filtered)).toContain(ToolpathType.wall);
  });

  it('keeps emptied layers so the range slider does not renumber', () => {
    // Dropping a layer that the filter emptied would shift every layer above
    // it, and the slider would then point at the wrong height.
    const parsed = parseGcodeToolpath(GCODE);
    const everything = new Set(typesOf(parsed.layers));
    const filtered = filterLayersByType(parsed.layers, everything);
    expect(filtered.length).toBe(parsed.layers.length);
    expect(typesOf(filtered)).toEqual([]);
  });

  it('keeps each kept record paired with its own width', () => {
    // The widths array is indexed in step with the records; dropping one
    // without dropping its width would smear widths across the rest.
    const parsed = parseGcodeToolpath(GCODE);
    const filtered = filterLayersByType(parsed.layers, new Set([ToolpathType.travel]));
    for (const layer of filtered) {
      expect(layer.widths.length).toBe(layer.paths.length / 8);
    }
  });

  it('hides by filament once the types are re-keyed', () => {
    const parsed = parseGcodeToolpath(GCODE);
    const byFilament = layersByFilament(parsed.layers);
    // Filament 1 is keyed as type 2.
    const filtered = filterLayersByType(byFilament, new Set([2]));
    expect(typesOf(filtered)).not.toContain(2);
    expect(typesOf(filtered)).toContain(1);
  });

  it.each([
    { hidden: 15, visible: [14, 16] },
    { hidden: 16, visible: [14, 15] },
  ])('hides only filament $hidden in the rendered mesh', ({ hidden, visible }) => {
    const parsed = parseGcodeToolpath('M83\nG0 X50 Y40 Z1\nG1 Y41\nT13\nG1 X51 E1\nT14\nG1 X52 E1\nT15\nG1 X53 E1');
    const filtered = filterLayersByType(layersByFilament(parsed.layers), new Set([hidden]));
    const data = buildSegmentData(filtered, parsed.defaultWidth);
    expect(data.nSeg).toBe(2);
    expect(data.nTrav).toBe(1);
    expect([...new Set(data.meta.vType)]).toEqual(visible);
  });

  it('returns the input untouched when nothing is hidden', () => {
    const parsed = parseGcodeToolpath(GCODE);
    expect(filterLayersByType(parsed.layers, new Set())).toBe(parsed.layers);
  });
});

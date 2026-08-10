import { describe, it, expect } from 'vitest';
import {
  isApiSliceableFileType,
  isApiSliceableFilename,
  isSliceableFileType,
  isSliceableFilename,
} from '../../utils/slicer';

/**
 * STEP splits the two slice paths.
 *
 * The desktop slicers open a STEP fine, so "Open in Slicer" must keep offering
 * it. Their command-line interfaces cannot load one -- OrcaSlicer 2.4.2 and
 * Bambu Studio 02.07.01.62 both answer "Unknown file format. Input file must
 * have .stl, .obj, .amf(.xml) extension." -- so the in-app "Slice" button and
 * the pipeline action, which both post to the sidecar, must not.
 *
 * One predicate used to serve both, which is why a STEP got a Slice button
 * that could only ever fail, several seconds and one upload later.
 */
describe('STEP is offered to the desktop slicer but not the sidecar', () => {
  it.each(['part.step', 'part.stp', 'PART.STEP'])('%s is a desktop handoff', (name) => {
    expect(isSliceableFilename(name)).toBe(true);
  });

  it.each(['part.step', 'part.stp', 'PART.STEP'])('%s is not sidecar-sliceable', (name) => {
    expect(isApiSliceableFilename(name)).toBe(false);
  });

  it.each(['cube.stl', 'project.3mf'])('%s stays sliceable both ways', (name) => {
    expect(isSliceableFilename(name)).toBe(true);
    expect(isApiSliceableFilename(name)).toBe(true);
  });

  it.each(['out.gcode', 'out.gcode.3mf'])('%s is slicer output, not input', (name) => {
    expect(isSliceableFilename(name)).toBe(false);
    expect(isApiSliceableFilename(name)).toBe(false);
  });

  it('applies the same split to stored file types', () => {
    expect(isSliceableFileType('step')).toBe(true);
    expect(isApiSliceableFileType('step')).toBe(false);
    expect(isApiSliceableFileType('stl')).toBe(true);
    expect(isApiSliceableFileType('3mf')).toBe(true);
    expect(isApiSliceableFileType('gcode.3mf')).toBe(false);
  });

  it('treats a missing type as not sliceable', () => {
    expect(isApiSliceableFileType(undefined)).toBe(false);
    expect(isApiSliceableFileType(null)).toBe(false);
    expect(isApiSliceableFileType('')).toBe(false);
  });
});

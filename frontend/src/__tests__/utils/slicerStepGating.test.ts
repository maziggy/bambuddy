import { describe, it, expect } from 'vitest';
import {
  isApiSliceableFileType,
  isApiSliceableFilename,
  isSliceableFileType,
  isSliceableFilename,
  type SlicerType,
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
 *
 * The desktop half is asserted against OrcaSlicer since #3029: Bambu Studio's
 * protocol handler takes 3MF only, so the handoff is per-slicer now. The split
 * this file is about is unchanged -- see the second describe for the new axis.
 */
describe('STEP is offered to the desktop slicer but not the sidecar', () => {
  it.each(['part.step', 'part.stp', 'PART.STEP'])('%s is a desktop handoff', (name) => {
    expect(isSliceableFilename(name, 'orcaslicer')).toBe(true);
  });

  it.each(['part.step', 'part.stp', 'PART.STEP'])('%s is not sidecar-sliceable', (name) => {
    expect(isApiSliceableFilename(name)).toBe(false);
  });

  it.each(['cube.stl', 'project.3mf'])('%s stays sliceable both ways', (name) => {
    expect(isSliceableFilename(name, 'orcaslicer')).toBe(true);
    expect(isApiSliceableFilename(name)).toBe(true);
  });

  it.each(['out.gcode', 'out.gcode.3mf'])('%s is slicer output, not input', (name) => {
    expect(isSliceableFilename(name, 'orcaslicer')).toBe(false);
    expect(isSliceableFilename(name, 'bambu_studio')).toBe(false);
    expect(isApiSliceableFilename(name)).toBe(false);
  });

  it('applies the same split to stored file types', () => {
    expect(isSliceableFileType('step', 'orcaslicer')).toBe(true);
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

/**
 * Which slicer the URL is handed to changes the answer (#3029).
 *
 * Bambu Studio funnels every protocol URL into ``Plater::import_model_id``,
 * which refuses a filename that is not .3mf before it even makes the request:
 * "Download failed, unknown file format." So a File Manager that offered an
 * STL handoff to Bambu Studio was offering an action that could only fail, and
 * the error blamed the file.
 *
 * OrcaSlicer only sends MakerWorld links down that path; a link to our own host
 * goes to its generic downloader, which has no extension check.
 */
describe('the desktop handoff is per-slicer', () => {
  it.each(['cube.stl', 'part.step', 'part.stp'])('%s goes to OrcaSlicer but not Bambu Studio', (name) => {
    expect(isSliceableFilename(name, 'orcaslicer')).toBe(true);
    expect(isSliceableFilename(name, 'bambu_studio')).toBe(false);
  });

  it('a 3MF goes to either', () => {
    expect(isSliceableFilename('project.3mf', 'orcaslicer')).toBe(true);
    expect(isSliceableFilename('project.3mf', 'bambu_studio')).toBe(true);
  });

  it('applies the same split to stored file types', () => {
    expect(isSliceableFileType('stl', 'orcaslicer')).toBe(true);
    expect(isSliceableFileType('stl', 'bambu_studio')).toBe(false);
    expect(isSliceableFileType('3mf', 'bambu_studio')).toBe(true);
    expect(isSliceableFileType('gcode.3mf', 'orcaslicer')).toBe(false);
  });

  it('treats a missing type as not sliceable for either', () => {
    expect(isSliceableFileType(undefined, 'orcaslicer')).toBe(false);
    expect(isSliceableFileType(null, 'bambu_studio')).toBe(false);
    expect(isSliceableFileType('', 'orcaslicer')).toBe(false);
  });

  it('falls back to the Bambu Studio list for an unrecognised slicer', () => {
    // `settings.open_in_slicer` is not validated on the way in, and
    // `openInSlicer` sends anything that is not exactly 'orcaslicer' to Bambu
    // Studio. The gate has to agree with where the URL actually goes.
    const bogus = 'prusa' as unknown as SlicerType;
    expect(isSliceableFilename('cube.stl', bogus)).toBe(false);
    expect(isSliceableFilename('project.3mf', bogus)).toBe(true);
  });
});

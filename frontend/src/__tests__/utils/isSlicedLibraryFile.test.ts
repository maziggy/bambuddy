/**
 * The Print button must follow what the file is, not what it is called (#2993).
 *
 * A plate exported from Studio, or a print dispatched through the cloud, is a
 * fully sliced 3MF named `Foo.3mf`. Deciding from the extension hid the Print
 * button on files the backend was perfectly willing to print -- `library.py`
 * has always accepted either type on the G-code path.
 */

import { describe, it, expect } from 'vitest';
import { isSlicedLibraryFile } from '../../utils/libraryFiles';

describe('isSlicedLibraryFile', () => {
  it('trusts file_type over a filename that disagrees', () => {
    // The reported file: sliced, named as a project.
    expect(isSlicedLibraryFile({ filename: 'Labyrinth.3mf', file_type: 'gcode.3mf' })).toBe(true);
  });

  it('leaves a genuine project alone', () => {
    expect(isSlicedLibraryFile({ filename: 'Labyrinth.3mf', file_type: '3mf' })).toBe(false);
  });

  it('accepts raw gcode', () => {
    expect(isSlicedLibraryFile({ filename: 'plate.gcode', file_type: 'gcode' })).toBe(true);
  });

  it('does not treat a model as printable', () => {
    expect(isSlicedLibraryFile({ filename: 'thing.stl', file_type: 'stl' })).toBe(false);
  });

  describe('the name answers where file_type does not', () => {
    it('with no file_type at all', () => {
      expect(isSlicedLibraryFile({ filename: 'Labyrinth.gcode.3mf' })).toBe(true);
      expect(isSlicedLibraryFile({ filename: 'plate.GCODE' })).toBe(true);
      expect(isSlicedLibraryFile({ filename: 'Labyrinth.3mf' })).toBe(false);
    });

    it('with an empty one', () => {
      expect(isSlicedLibraryFile({ filename: 'Labyrinth.gcode.3mf', file_type: '' })).toBe(true);
      expect(isSlicedLibraryFile({ filename: 'Labyrinth.gcode.3mf', file_type: null })).toBe(true);
    });

    it('and on a row stored before the backfill, where file_type is the stale half', () => {
      // Mirrors classify_file_type, which returns `gcode.3mf` for this name
      // without opening the file. Neither side may drop a Print button that
      // the other would keep.
      expect(isSlicedLibraryFile({ filename: 'Labyrinth.gcode.3mf', file_type: '3mf' })).toBe(true);
    });
  });
});

import { describe, expect, it } from 'vitest';
import { pickObjectIdAt, plateClickToMaskPoint } from '../../utils/skipObjects';

function imageData(width: number, height: number, pixels: number[]): ImageData {
  return {
    width,
    height,
    data: new Uint8ClampedArray(pixels),
    colorSpace: 'srgb',
  } as ImageData;
}

describe('pickObjectIdAt', () => {
  it('decodes the slicer object ID from RGB channels', () => {
    const pick = imageData(2, 1, [
      0, 0, 0, 0,
      52, 18, 1, 255,
    ]);

    expect(pickObjectIdAt(pick, 1, 0)).toBe(1 * 65536 + 18 * 256 + 52);
  });

  it('treats transparent and black pixels as empty plate space', () => {
    const pick = imageData(2, 1, [
      8, 0, 0, 0,
      0, 0, 0, 255,
    ]);

    expect(pickObjectIdAt(pick, 0, 0)).toBeNull();
    expect(pickObjectIdAt(pick, 1, 0)).toBeNull();
  });

  it('clamps click coordinates to the image bounds', () => {
    const pick = imageData(1, 1, [63, 0, 0, 255]);

    expect(pickObjectIdAt(pick, 99, -4)).toBe(63);
  });
});

describe('plateClickToMaskPoint', () => {
  const square = { left: 100, top: 50, width: 400, height: 400 };

  it('maps a click through the display scale when the mask fills the box', () => {
    // 400px box, 200px mask: the centre of the box is the centre of the mask.
    expect(plateClickToMaskPoint(square, 200, 200, 300, 250)).toEqual({ x: 100, y: 100 });
  });

  it('offsets by the letterbox bars when the mask is not square', () => {
    // A 200x100 mask in a 400x400 box renders 400x200, leaving 100px bars top
    // and bottom. Without that offset this click would read 100px too low.
    expect(plateClickToMaskPoint(square, 200, 100, 300, 250)).toEqual({ x: 100, y: 50 });
  });

  it('rejects clicks on a letterbox bar rather than clamping onto an edge object', () => {
    expect(plateClickToMaskPoint(square, 200, 100, 300, 100)).toBeNull();
    expect(plateClickToMaskPoint(square, 200, 100, 300, 400)).toBeNull();
  });

  it('rejects clicks outside the plate box', () => {
    expect(plateClickToMaskPoint(square, 200, 200, 90, 250)).toBeNull();
    expect(plateClickToMaskPoint(square, 200, 200, 300, 460)).toBeNull();
  });

  it('returns null for a collapsed box instead of dividing by zero', () => {
    expect(plateClickToMaskPoint({ left: 0, top: 0, width: 0, height: 0 }, 200, 200, 0, 0)).toBeNull();
  });
});

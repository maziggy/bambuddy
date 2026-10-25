import { describe, it, expect } from 'vitest';
import i18n from '../../i18n';
import { cameraTestSuccessMessage } from '../../utils/cameraTestMessage';

const t = i18n.getFixedT('en');

describe('cameraTestSuccessMessage', () => {
  it('puts the resolution in brackets after the word', () => {
    expect(cameraTestSuccessMessage({ resolution: '640x480' }, t)).toBe('Camera connected (640x480)');
  });

  it('leaves it out when the frame had none', () => {
    expect(cameraTestSuccessMessage({}, t)).toBe('Camera connected');
  });

  it('names a shared capture', () => {
    expect(cameraTestSuccessMessage({ resolution: '640x480', coalesced: true }, t)).toBe(
      'Camera connected (640x480) (shared with a capture already running)',
    );
  });

  it('names the live view, which wins over a shared capture', () => {
    expect(cameraTestSuccessMessage({ resolution: '640x480', coalesced: true, live_view: true }, t)).toBe(
      'Camera works (640x480) (frame from the live view, which is using the camera)',
    );
  });
});

/**
 * A K-profile write the printer did not acknowledge is reported as sent, not
 * as saved.
 */
import { describe, it, expect } from 'vitest';
import { kprofileWriteToast } from '../../utils/kprofileWrite';

describe('kprofileWriteToast', () => {
  it('says saved when the printer confirmed it', () => {
    expect(kprofileWriteToast({ success: true, confirmed: true, message: '' }, 'kProfiles.toast.profileSaved'))
      .toEqual({ key: 'kProfiles.toast.profileSaved', type: 'success' });
  });

  it('warns when the printer did not confirm it', () => {
    expect(kprofileWriteToast({ success: true, confirmed: false, message: '' }, 'kProfiles.toast.profileSaved'))
      .toEqual({ key: 'kProfiles.toast.writeUnconfirmed', type: 'warning' });
  });

  it('keeps the old answer shape working', () => {
    expect(kprofileWriteToast({ success: true, message: '' }, 'kProfiles.toast.profileDeleted').type).toBe('success');
  });
});

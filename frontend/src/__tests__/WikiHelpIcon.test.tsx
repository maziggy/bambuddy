import { describe, it, expect } from 'vitest';
import { render } from './utils';
import { screen } from '@testing-library/react';
import { WikiHelpIcon } from '../components/WikiHelpIcon';

describe('WikiHelpIcon', () => {
  it('renders an external link to the wiki base + path with trailing slash', () => {
    render(<WikiHelpIcon path="features/queue" />);
    const link = screen.getByRole('link');
    expect(link.getAttribute('href')).toBe('https://wiki.bambuddy.cool/features/queue/');
  });

  it('opens in a new tab with safe rel attribute', () => {
    render(<WikiHelpIcon path="features/archives" />);
    const link = screen.getByRole('link');
    expect(link.getAttribute('target')).toBe('_blank');
    expect(link.getAttribute('rel')).toBe('noopener noreferrer');
  });

  it('exposes a localized aria-label so screen readers announce the destination', () => {
    render(<WikiHelpIcon path="features/inventory" />);
    const link = screen.getByRole('link');
    const label = link.getAttribute('aria-label');
    expect(label).toBeTruthy();
    expect(label).not.toBe('');
    // The key should resolve — if i18n hasn't loaded, getByRole would still
    // pass but the label would be the raw key string. Guard against that.
    expect(label).not.toContain('onboarding.helpIcon');
  });
});

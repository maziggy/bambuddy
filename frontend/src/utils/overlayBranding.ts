import type { CSSProperties } from 'react';

export interface OverlayBranding {
  logo: boolean;
  logoRevision: number;
  from: string;
  to: string;
}

export const DEFAULT_BRANDING: OverlayBranding = { logo: false, logoRevision: 0, from: '', to: '' };

// Only six-digit hex colours can reach CSS. Invalid or missing pairs preserve
// the artwork's existing progress colour, including its state-specific styling.
export function isOverlayColour(value: string): boolean {
  return /^#[0-9a-f]{6}$/i.test(value);
}

export function overlayGradient(from: string | null, to: string | null): string | undefined {
  return from && to && isOverlayColour(from) && isOverlayColour(to)
    ? `linear-gradient(to right, ${from}, ${to})`
    : undefined;
}

export function overlayProgressTextStyle(background: string | undefined): CSSProperties | undefined {
  if (!background) return undefined;
  return {
    backgroundImage: background,
    backgroundClip: 'text',
    WebkitBackgroundClip: 'text',
    color: 'transparent',
    WebkitTextFillColor: 'transparent',
  };
}

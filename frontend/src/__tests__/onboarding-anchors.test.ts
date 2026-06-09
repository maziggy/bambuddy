/**
 * Regression backstop for the onboarding tour anchors documented in
 * docs/onboarding-tour-plan.md Appendix A. The future tour engine will
 * target each `[data-tour="..."]` selector listed below — if a component
 * gets refactored without preserving its anchor, the corresponding tour
 * step will silently fail at runtime. This test fails the PR instead.
 *
 * Source-level grep rather than DOM render: most anchor hosts are inside
 * pages gated by route + permission + sub-tab state that would require
 * extensive mocking to render at all (e.g. the auth-card lives inside
 * `usersSubTab === 'users'`). Source presence is the load-bearing
 * invariant; the tour engine's own tests will exercise rendered behaviour.
 */

import { dirname, resolve } from 'node:path';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, it, expect } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = resolve(__dirname, '..', '..');

interface AnchorSpec {
  anchor: string;
  file: string;
}

const LITERAL_ANCHORS: AnchorSpec[] = [
  { anchor: 'add-printer-button', file: 'src/pages/PrintersPage.tsx' },
  { anchor: 'printer-status-pill', file: 'src/pages/PrintersPage.tsx' },
  { anchor: 'printer-status-row', file: 'src/pages/PrintersPage.tsx' },
  { anchor: 'printer-ams-row', file: 'src/pages/PrintersPage.tsx' },
  { anchor: 'printer-camera', file: 'src/pages/PrintersPage.tsx' },
  { anchor: 'printer-controls', file: 'src/pages/PrintersPage.tsx' },
  { anchor: 'printer-customize', file: 'src/pages/PrintersPage.tsx' },
  { anchor: 'auth-card', file: 'src/pages/SettingsPage.tsx' },
  { anchor: 'vp-card', file: 'src/pages/SettingsPage.tsx' },
  { anchor: 'slicer-api-card', file: 'src/pages/SettingsPage.tsx' },
  { anchor: 'integrations-card', file: 'src/pages/SettingsPage.tsx' },
  { anchor: 'obico-card', file: 'src/pages/SettingsPage.tsx' },
  { anchor: 'add-user-button', file: 'src/pages/SettingsPage.tsx' },
  { anchor: 'groups-section', file: 'src/pages/SettingsPage.tsx' },
  { anchor: 'sso-section', file: 'src/pages/SettingsPage.tsx' },
  { anchor: 'add-spool-button', file: 'src/pages/InventoryPage.tsx' },
  { anchor: 'bambu-cloud-sync', file: 'src/pages/ProfilesPage.tsx' },
];

// Sidebar entries share a single `data-tour={`sidebar-${id}`}` expression
// on the NavLink — assert the template AND that each plan-listed id exists
// in `defaultNavItems` so the runtime selector actually resolves.
const SIDEBAR_IDS = [
  'queue',
  'archives',
  'stats',
  'maintenance',
  'files',
  'projects',
];

function readSource(relativePath: string): string {
  return readFileSync(resolve(FRONTEND_ROOT, relativePath), 'utf8');
}

describe('Onboarding tour anchors', () => {
  for (const { anchor, file } of LITERAL_ANCHORS) {
    it(`[data-tour="${anchor}"] is present in ${file}`, () => {
      const contents = readSource(file);
      expect(contents).toMatch(new RegExp(`data-tour="${anchor}"`));
    });
  }

  it('Sidebar NavLink applies the data-tour={`sidebar-${id}`} template', () => {
    const layout = readSource('src/components/Layout.tsx');
    expect(layout).toContain('data-tour={`sidebar-${id}`}');
  });

  for (const id of SIDEBAR_IDS) {
    it(`defaultNavItems includes '${id}' so [data-tour="sidebar-${id}"] resolves at runtime`, () => {
      const layout = readSource('src/components/Layout.tsx');
      expect(layout).toMatch(new RegExp(`\\{\\s*id:\\s*'${id}'`));
    });
  }
});

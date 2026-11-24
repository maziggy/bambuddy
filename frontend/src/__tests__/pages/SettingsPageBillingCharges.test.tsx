/**
 * The charging policy (#3581): what a print is charged, under Cost Tracking
 * while billing is on. Energy and printer wear for completed prints and for
 * failed or cancelled ones, and how the filament of a print that did not
 * finish is charged. Nothing is on until switched on, and each control saves
 * its own setting.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { SettingsPage } from '../../pages/SettingsPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { setAuthToken } from '../../api/client';

const baseSettings = {
  auto_archive: true,
  currency: 'USD',
  time_format: 'system',
  date_format: 'system',
  check_updates: false,
  check_printer_firmware: false,
  billing_enabled: true,
  // Set, so the page has no detected address of its own to save with any edit.
  external_url: window.location.origin,
};

const COMPLETED = 'Completed prints';
const FAILED = 'Failed and cancelled prints';
const SWITCHES: Array<[string, string]> = [
  [`${COMPLETED}: Energy Cost`, 'billing_completed_energy'],
  [`${COMPLETED}: Wear Cost`, 'billing_completed_wear'],
  [`${FAILED}: Energy Cost`, 'billing_failed_energy'],
  [`${FAILED}: Wear Cost`, 'billing_failed_wear'],
];
const FILAMENT = 'Filament charge';
const POLICY_KEYS = [...SWITCHES.map(([, key]) => key), 'billing_failed_filament'];

let saved: Array<Record<string, unknown>>;

function serve(settings: Record<string, unknown>) {
  server.use(
    http.get('/api/v1/settings/', () => HttpResponse.json(settings)),
    http.put('/api/v1/settings/', async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      saved.push(body);
      return HttpResponse.json({ ...settings, ...body });
    }),
    http.get('/api/v1/printers/', () => HttpResponse.json([])),
    http.get('/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: false, requires_setup: false })),
  );
}

beforeEach(() => {
  window.history.replaceState({}, '', '/?tab=general');
  vi.mocked(localStorage.getItem).mockReset();
  setAuthToken(null);
  saved = [];
});

async function control<T extends HTMLElement>(label: string) {
  const input = (await screen.findByLabelText(label)) as T;
  // Let the loaded settings land first; a change before that is overwritten.
  await new Promise(resolve => setTimeout(resolve, 200));
  return input;
}

/** The policy settings of the last save that differ from the defaults. */
function changed() {
  const body = saved.at(-1) ?? {};
  return Object.fromEntries(
    POLICY_KEYS.filter(key => (body[key] ?? false) !== false && body[key] !== 'proportional').map(key => [key, body[key]]),
  );
}

describe('SettingsPage - charging policy (#3581)', () => {
  it('by default nothing is added and failed prints pay their share, and nothing is saved unchanged', async () => {
    serve(baseSettings);
    render(<SettingsPage />);
    for (const [label] of SWITCHES) {
      expect((await control<HTMLInputElement>(label)).checked).toBe(false);
    }
    expect((await control<HTMLSelectElement>(FILAMENT)).value).toBe('proportional');
    await new Promise(resolve => setTimeout(resolve, 1000));
    expect(saved).toEqual([]);
  });

  it('shows what is saved', async () => {
    serve({
      ...baseSettings,
      billing_completed_energy: true,
      billing_completed_wear: false,
      billing_failed_filament: 'none',
      billing_failed_energy: false,
      billing_failed_wear: true,
    });
    render(<SettingsPage />);
    expect((await control<HTMLInputElement>(SWITCHES[0][0])).checked).toBe(true);
    expect((await control<HTMLInputElement>(SWITCHES[1][0])).checked).toBe(false);
    expect((await control<HTMLInputElement>(SWITCHES[2][0])).checked).toBe(false);
    expect((await control<HTMLInputElement>(SWITCHES[3][0])).checked).toBe(true);
    expect((await control<HTMLSelectElement>(FILAMENT)).value).toBe('none');
  });

  it.each(SWITCHES)('%s saves its own setting and no other', async (label, key) => {
    serve(baseSettings);
    render(<SettingsPage />);
    fireEvent.click(await control(label));
    await waitFor(() => expect(saved.at(-1)?.[key]).toBe(true), { timeout: 3000 });
    expect(changed()).toEqual({ [key]: true });
  });

  it.each(['full', 'none'])('the filament of a failed print can be charged as "%s"', async mode => {
    serve(baseSettings);
    render(<SettingsPage />);
    fireEvent.change(await control(FILAMENT), { target: { value: mode } });
    await waitFor(() => expect(saved.at(-1)?.billing_failed_filament).toBe(mode), { timeout: 3000 });
    expect(changed()).toEqual({ billing_failed_filament: mode });
  });

  it('is not offered while billing is off', async () => {
    serve({ ...baseSettings, billing_enabled: false });
    render(<SettingsPage />);
    await screen.findByText('Enable Billing');
    await new Promise(resolve => setTimeout(resolve, 200));
    expect(screen.queryByText('Print Charges')).toBeNull();
    expect(screen.queryByLabelText(FILAMENT)).toBeNull();
    expect(screen.queryByLabelText(SWITCHES[0][0])).toBeNull();
  });
});

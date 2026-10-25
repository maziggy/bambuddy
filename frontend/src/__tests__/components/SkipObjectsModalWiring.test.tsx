/**
 * SkipObjectsModal wiring: the request it sends when objects are skipped.
 *
 * The printer identifies objects by their slicer object id, not by their
 * position in the list, so picking the 1st and 3rd object must send their ids.
 * Already-skipped objects can't be picked again, and the submit button is gated
 * on printers:control and on the print being past layer 1.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const permissions = { granted: ['printers:control'] as string[] };

const mockUseAuth = {
  user: { id: 1, username: 'operator', permissions: [] as string[] },
  authEnabled: true,
  loading: false,
  isAdmin: false,
  login: vi.fn(),
  loginWithToken: vi.fn(),
  logout: vi.fn(),
  refreshAuth: vi.fn(),
  hasPermission: vi.fn((permission: string) => permissions.granted.includes(permission)),
  hasAnyPermission: vi.fn(() => true),
  canModify: vi.fn(() => true),
};

vi.mock('../../contexts/AuthContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/AuthContext')>();
  return { ...actual, useAuth: () => mockUseAuth };
});

import { render } from '../utils';
import { SkipObjectsModal } from '../../components/SkipObjectsModal';

const PRINTER_ID = 7;

// Ids deliberately unlike list positions.
const objects = [
  { id: 412, name: 'Bracket A', x: 10, y: 10, skipped: false },
  { id: 518, name: 'Bracket B', x: 20, y: 20, skipped: true },
  { id: 903, name: 'Bracket C', x: 30, y: 30, skipped: false },
  { id: 1207, name: 'Bracket D', x: 40, y: 40, skipped: false },
];

let skipCalls: { printerId: string; body: unknown }[] = [];

function setup({ layer = 12 }: { layer?: number } = {}) {
  skipCalls = [];
  server.use(
    http.get(`/api/v1/printers/${PRINTER_ID}/status`, () =>
      HttpResponse.json({ connected: true, state: 'RUNNING', layer_num: layer, cover_url: null }),
    ),
    http.get(`/api/v1/printers/${PRINTER_ID}/print/objects`, () =>
      HttpResponse.json({
        objects,
        total: objects.length,
        skipped_count: 1,
        is_printing: true,
        bbox_all: null,
      }),
    ),
    http.post('/api/v1/printers/:id/print/skip-objects', async ({ request, params }) => {
      skipCalls.push({ printerId: params.id as string, body: await request.json() });
      return HttpResponse.json({ success: true, message: 'Skipped', skipped_objects: [] });
    }),
  );
  const onClose = vi.fn();
  render(<SkipObjectsModal printerId={PRINTER_ID} isOpen onClose={onClose} />);
  return { onClose };
}

const row = (name: string) => screen.getByText(name).closest('button') as HTMLButtonElement;
const submit = () => screen.getByRole('button', { name: 'Skip Selected' });

describe('SkipObjectsModal wiring', () => {
  beforeEach(() => {
    permissions.granted = ['printers:control'];
  });

  it('skips the picked objects by their object ids', async () => {
    const user = userEvent.setup();
    const { onClose } = setup();
    await screen.findByText('Bracket A');
    await waitFor(() => expect(submit()).toBeDisabled());

    await user.click(row('Bracket A'));
    await user.click(row('Bracket C'));
    await waitFor(() => expect(submit()).toBeEnabled());
    await user.click(submit());

    // Confirm dialog: the confirm button carries the same label.
    expect(await screen.findByText('Skip 2 selected objects? This cannot be undone.')).toBeInTheDocument();
    const confirmButtons = screen.getAllByRole('button', { name: 'Skip Selected' });
    await user.click(confirmButtons[confirmButtons.length - 1]);

    await waitFor(() => expect(skipCalls).toHaveLength(1));
    expect(skipCalls[0].printerId).toBe(String(PRINTER_ID));
    expect([...(skipCalls[0].body as number[])].sort((a, b) => a - b)).toEqual([412, 903]);
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('sends nothing when the confirm is cancelled', async () => {
    const user = userEvent.setup();
    setup();
    await screen.findByText('Bracket A');
    await user.click(row('Bracket D'));
    await waitFor(() => expect(submit()).toBeEnabled());
    await user.click(submit());
    expect(await screen.findByText('Are you sure you want to skip "Bracket D"? This cannot be undone.')).toBeInTheDocument();

    const cancels = screen.getAllByRole('button', { name: 'Cancel' });
    await user.click(cancels[cancels.length - 1]);

    await waitFor(() => expect(screen.queryByText(/Are you sure you want to skip/)).toBeNull());
    expect(skipCalls).toEqual([]);
  });

  it('does not let an already-skipped object be picked', async () => {
    const user = userEvent.setup();
    setup();
    await screen.findByText('Bracket B');
    expect(row('Bracket B')).toBeDisabled();
    await user.click(row('Bracket B'));
    expect(screen.getByText('No objects selected')).toBeInTheDocument();
    expect(submit()).toBeDisabled();
  });

  it('select all picks only the objects still printing', async () => {
    const user = userEvent.setup();
    setup();
    await screen.findByText('Bracket A');
    await user.click(screen.getByRole('button', { name: /Select All/ }));
    await waitFor(() => expect(submit()).toBeEnabled());
    await user.click(submit());
    // All remaining objects selected -> the stronger warning.
    expect(await screen.findByText(/All remaining objects are selected/)).toBeInTheDocument();
    const confirmButtons = screen.getAllByRole('button', { name: 'Skip Selected' });
    await user.click(confirmButtons[confirmButtons.length - 1]);

    await waitFor(() => expect(skipCalls).toHaveLength(1));
    expect([...(skipCalls[0].body as number[])].sort((a, b) => a - b)).toEqual([412, 903, 1207]);
  });

  it('keeps submit disabled without printers:control', async () => {
    permissions.granted = [];
    const user = userEvent.setup();
    setup();
    await screen.findByText('Bracket A');
    await user.click(row('Bracket A'));
    expect(screen.getByText('1/3', { selector: 'footer span' })).toBeInTheDocument();
    expect(submit()).toBeDisabled();
    await user.click(submit());
    expect(screen.queryByText(/Are you sure you want to skip/)).toBeNull();
    expect(skipCalls).toEqual([]);
  });

  it('keeps submit disabled until the print is past layer 1', async () => {
    const user = userEvent.setup();
    setup({ layer: 1 });
    expect(await screen.findByText('Wait for layer 2+ to skip objects (currently layer 1)')).toBeInTheDocument();
    await user.click(row('Bracket A'));
    expect(submit()).toBeDisabled();
    await user.click(submit());
    expect(skipCalls).toEqual([]);
  });
});

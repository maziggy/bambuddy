/**
 * The preset editor's Material type dropdown follows the preferred slicer (#2928).
 *
 * The backend serves the options of the slicer chosen in Settings -> Slicer;
 * the editor must fetch them again when that choice changes, and a profile's
 * own type must stay visible even when the list does not have it.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useEffect } from 'react';
import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { ProfilesPage } from '../../pages/ProfilesPage';
import { setAuthToken } from '../../api/client';

const USER_PRESET = {
  setting_id: 'PFUS-types-1',
  name: 'My Custom Type @BBL X1C',
  type: 'filament',
  version: null,
  user_id: 'u1',
  updated_time: null,
  is_custom: true,
};
const BUILTIN_PRESET = {
  setting_id: 'GFSA00',
  name: 'Bambu PLA Basic @BBL X1C',
  type: 'filament',
  version: null,
  user_id: null,
  updated_time: null,
  is_custom: false,
};

const OPTIONS = {
  bambu_studio: ['PLA', 'TPU-AMS'],
  orcaslicer: ['PLA', 'SBS'],
} as const;

let slicer: keyof typeof OPTIONS;
let presetType: string | undefined;

function fieldsFor(chosen: keyof typeof OPTIONS) {
  return {
    version: '1',
    description: '',
    fields: [
      {
        key: 'filament_type',
        label: 'Filament Type',
        type: 'select',
        category: 'basic',
        description: 'Material type',
        options: OPTIONS[chosen].map((value) => ({ value, label: value })),
      },
    ],
  };
}

beforeEach(() => {
  slicer = 'bambu_studio';
  presetType = 'SBS';
  server.use(
    http.get('*/api/v1/cloud/status', () =>
      HttpResponse.json({ is_authenticated: true, email: 'user@example.com', region: 'global' }),
    ),
    http.get('*/api/v1/printers/', () => HttpResponse.json([])),
    http.get('*/api/v1/settings/', () => HttpResponse.json({ preferred_slicer: slicer })),
    http.get('*/api/v1/cloud/settings', () =>
      HttpResponse.json({ filament: [USER_PRESET, BUILTIN_PRESET], printer: [], process: [] }),
    ),
    http.get('*/api/v1/cloud/settings/:id', ({ params }) =>
      HttpResponse.json({
        setting_id: params.id,
        name: params.id === USER_PRESET.setting_id ? USER_PRESET.name : BUILTIN_PRESET.name,
        base_id: 'GFSA00',
        setting:
          params.id === USER_PRESET.setting_id
            ? { inherits: BUILTIN_PRESET.name, filament_type: presetType }
            : { filament_type: 'PLA' },
      }),
    ),
    http.get('*/api/v1/cloud/fields/:type', () => {
      return HttpResponse.json(fieldsFor(slicer));
    }),
  );
});

afterEach(() => {
  setAuthToken(null);
});

// The test's QueryClient, to change the settings the way the Settings page does.
let queryClient: QueryClient | undefined;
function GrabQueryClient() {
  const client = useQueryClient();
  useEffect(() => {
    queryClient = client;
  }, [client]);
  return null;
}

async function openCreateModal(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: 'New Preset' }));
  await screen.findByText('Create New Preset');
  const baseSelect = screen.getByText('Base Preset').parentElement!.querySelector('select')!;
  await user.selectOptions(baseSelect, 'GFSA00');
}

function typeSelect(): HTMLSelectElement {
  return screen.getByText('Filament Type').closest('div')!.parentElement!.querySelector('select')!;
}

const optionValues = (select: HTMLSelectElement) =>
  [...select.querySelectorAll('option')].map((o) => o.getAttribute('value')).filter((v) => v);

describe('ProfilesPage: Material type options', () => {
  it('offers what the server sends for the preferred slicer', async () => {
    const user = userEvent.setup();
    slicer = 'orcaslicer';
    render(<ProfilesPage />);

    await openCreateModal(user);

    await waitFor(() => expect(optionValues(typeSelect())).toEqual(['PLA', 'SBS']));
  });

  it('shows Bambu Studio’s list when that is the preferred slicer', async () => {
    const user = userEvent.setup();
    render(<ProfilesPage />);

    await openCreateModal(user);

    await waitFor(() => expect(optionValues(typeSelect())).toEqual(['PLA', 'TPU-AMS']));
  });

  it('fetches the list again when the preferred slicer changes', async () => {
    const user = userEvent.setup();
    render(
      <>
        <ProfilesPage />
        <GrabQueryClient />
      </>,
    );

    await openCreateModal(user);
    await waitFor(() => expect(optionValues(typeSelect())).toEqual(['PLA', 'TPU-AMS']));

    // Settings -> Slicer saved: the page writes the new settings into the cache.
    slicer = 'orcaslicer';
    act(() => {
      queryClient!.setQueryData(['settings'], { preferred_slicer: 'orcaslicer' });
    });

    await waitFor(() => expect(optionValues(typeSelect())).toEqual(['PLA', 'SBS']));
  });

  it('keeps a profile’s own type selectable when the list does not have it', async () => {
    const user = userEvent.setup();
    presetType = 'HSPLA+2.0';
    render(<ProfilesPage />);

    await user.click(await screen.findByText(USER_PRESET.name));
    const edit = await screen.findByRole('button', { name: 'Edit' });
    await waitFor(() => expect(edit).not.toBeDisabled());
    await user.click(edit);

    await waitFor(() => expect(typeSelect().value).toBe('HSPLA+2.0'));
    expect(optionValues(typeSelect())).toEqual(['HSPLA+2.0', 'PLA', 'TPU-AMS']);
  });

  it('does not add an option for a type the list has', async () => {
    const user = userEvent.setup();
    presetType = 'TPU-AMS';
    render(<ProfilesPage />);

    await user.click(await screen.findByText(USER_PRESET.name));
    const edit = await screen.findByRole('button', { name: 'Edit' });
    await waitFor(() => expect(edit).not.toBeDisabled());
    await user.click(edit);

    await waitFor(() => expect(typeSelect().value).toBe('TPU-AMS'));
    expect(optionValues(typeSelect())).toEqual(['PLA', 'TPU-AMS']);
  });
});

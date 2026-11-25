import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { SpoolCatalogSettings } from '../../components/SpoolCatalogSettings';

// One t for every render, as react-i18next gives: a new one each render would
// reload the catalogue on every render and undo what a test just saved.
const { t } = vi.hoisted(() => ({ t: (key: string, fallback?: string) => fallback ?? key }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t }),
}));

const mockShowToast = vi.fn();
vi.mock('../../contexts/ToastContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/ToastContext')>();
  return { ...actual, useToast: () => ({ showToast: mockShowToast }) };
});

vi.mock('../../api/client', () => ({
  api: {
    getSettings: vi.fn().mockResolvedValue({}),
    getSpoolCatalog: vi.fn().mockResolvedValue([]),
    addCatalogEntry: vi.fn(),
    updateCatalogEntry: vi.fn(),
  },
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
}));

import { api } from '../../api/client';

describe('SpoolCatalogSettings — local catalog UI', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSpoolCatalog).mockResolvedValue([]);
  });

  it('shows local CRUD buttons regardless of Spoolman state', async () => {
    render(<SpoolCatalogSettings />);

    await waitFor(() => {
      expect(screen.getByText('common.add')).toBeTruthy();
    });

    expect(screen.getByText('common.export')).toBeTruthy();
    expect(screen.getByText('common.import')).toBeTruthy();
    expect(screen.getByText('common.reset')).toBeTruthy();
  });

  it('renders the local Spool Catalog header and column layout', async () => {
    render(<SpoolCatalogSettings />);

    await waitFor(() => {
      expect(screen.getByText('settings.catalog.spoolCatalog')).toBeTruthy();
    });

    expect(screen.getByText('common.name')).toBeTruthy();
    expect(screen.getByText('settings.catalog.weight')).toBeTruthy();
    expect(screen.getByText('settings.catalog.type')).toBeTruthy();

    // No Spoolman-only columns leak in
    expect(screen.queryByText('settings.catalog.material')).toBeNull();
    expect(screen.queryByText('settings.catalog.spoolWeight')).toBeNull();
    expect(screen.queryByText('settings.spoolmanFilamentCatalogTitle')).toBeNull();
  });
});

// The #3727 columns: the brand a spool belongs to, and the brand's default
const rows = [
  { id: 1, name: 'Sunlu - Plastic V3', weight: 179, is_default: true, manufacturer: 'Sunlu', default_for_brand: false },
  { id: 2, name: 'Sunlu - Plastic V3 + Cardboard Core', weight: 217, is_default: true, manufacturer: 'Sunlu', default_for_brand: true },
  { id: 3, name: 'Mine', weight: 205, is_default: false, manufacturer: null, default_for_brand: false },
];

async function importFile(container: HTMLElement, data: unknown) {
  const input = container.querySelector('input[type="file"]') as HTMLInputElement;
  const json = JSON.stringify(data);
  const file = new File([json], 'spool-catalog.json', { type: 'application/json' });
  // jsdom's File has no text()
  Object.defineProperty(file, 'text', { value: async () => json });
  fireEvent.change(input, { target: { files: [file] } });
}

describe('SpoolCatalogSettings — manufacturer and brand default (#3727)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSpoolCatalog).mockResolvedValue(rows);
    let next = 10;
    vi.mocked(api.addCatalogEntry).mockImplementation(async (data) => ({
      id: next++, is_default: false, manufacturer: null, default_for_brand: false, ...data,
    }));
  });

  it('shows each row\'s manufacturer and marks the brand default', async () => {
    render(<SpoolCatalogSettings />);
    await screen.findByText('Sunlu - Plastic V3 + Cardboard Core');
    expect(screen.getByText('settings.catalog.manufacturer')).toBeTruthy();
    expect(screen.getAllByText('settings.catalog.brandDefault')).toHaveLength(1);
  });

  it('exports the new columns', async () => {
    let exported: Blob | undefined;
    const createUrl = vi.fn((blob: Blob) => { exported = blob; return 'blob:x'; });
    Object.assign(URL, { createObjectURL: createUrl, revokeObjectURL: vi.fn() });
    render(<SpoolCatalogSettings />);
    await screen.findByText('Mine');
    fireEvent.click(screen.getByText('common.export'));
    const data = JSON.parse(await new Promise<string>((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as string);
      reader.readAsText(exported!);
    }));
    expect(data).toContainEqual({
      name: 'Sunlu - Plastic V3 + Cardboard Core', weight: 217, manufacturer: 'Sunlu', default_for_brand: true,
    });
    expect(data).toContainEqual({ name: 'Mine', weight: 205, manufacturer: null, default_for_brand: false });
  });

  it('imports an export from before #3727 (name and weight only)', async () => {
    const { container } = render(<SpoolCatalogSettings />);
    await screen.findByText('Mine');
    await importFile(container, [{ name: 'Old Row', weight: 190 }]);
    await waitFor(() => expect(api.addCatalogEntry).toHaveBeenCalledTimes(1));
    expect(api.addCatalogEntry).toHaveBeenCalledWith({
      name: 'Old Row', weight: 190, manufacturer: null, default_for_brand: false,
    });
  });

  it('never lets an imported default take over from one the catalogue has', async () => {
    const { container } = render(<SpoolCatalogSettings />);
    await screen.findByText('Mine');
    await importFile(container, [
      { name: 'Other Sunlu', weight: 230, manufacturer: 'SUNLU', default_for_brand: true },
      { name: 'eSUN A', weight: 240, manufacturer: 'eSUN', default_for_brand: true },
      { name: 'eSUN B', weight: 241, manufacturer: 'esun', default_for_brand: true },
    ]);
    await waitFor(() => expect(api.addCatalogEntry).toHaveBeenCalledTimes(3));
    const sent = vi.mocked(api.addCatalogEntry).mock.calls.map(([d]) => [d.name, d.default_for_brand]);
    // Sunlu has its default already; eSUN takes the file's first one only
    expect(sent).toEqual([['Other Sunlu', false], ['eSUN A', true], ['eSUN B', false]]);
  });

  it('adds a row as its brand\'s default', async () => {
    render(<SpoolCatalogSettings />);
    await screen.findByText('Mine');
    fireEvent.click(screen.getByText('common.add'));
    fireEvent.change(screen.getByPlaceholderText('settings.catalog.namePlaceholder'), { target: { value: 'eSUN - Plastic' } });
    fireEvent.change(screen.getByPlaceholderText('settings.catalog.manufacturerPlaceholder'), { target: { value: ' eSUN ' } });
    fireEvent.change(screen.getByPlaceholderText('g'), { target: { value: '240' } });
    fireEvent.click(screen.getByRole('switch', { name: 'settings.catalog.brandDefault' }));
    fireEvent.click(screen.getAllByText('common.add').pop()!);
    await waitFor(() => expect(api.addCatalogEntry).toHaveBeenCalledWith({
      name: 'eSUN - Plastic', weight: 240, manufacturer: 'eSUN', default_for_brand: true,
    }));
  });

  it('offers the brand default only once a manufacturer is set', async () => {
    render(<SpoolCatalogSettings />);
    await screen.findByText('Mine');
    fireEvent.click(screen.getByText('common.add'));
    const toggle = screen.getByRole('switch', { name: 'settings.catalog.brandDefault' });
    expect(toggle).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText('settings.catalog.manufacturerPlaceholder'), { target: { value: 'Sunlu' } });
    expect(toggle).not.toBeDisabled();
  });

  it('moves the brand default in the list when an edit takes it', async () => {
    vi.mocked(api.updateCatalogEntry).mockImplementation(async (id, data) => ({
      id, is_default: true, manufacturer: null, default_for_brand: false, ...data,
    }));
    // After the save the page reloads, and the server has moved the star
    vi.mocked(api.getSpoolCatalog).mockResolvedValueOnce(rows).mockResolvedValue([
      { ...rows[0], default_for_brand: true },
      { ...rows[1], default_for_brand: false },
      rows[2],
    ]);
    render(<SpoolCatalogSettings />);
    await screen.findByText('Mine');
    // Edit "Sunlu - Plastic V3" (sorted first) and make it Sunlu's default
    const row = screen.getByText('Sunlu - Plastic V3').closest('tr')!;
    fireEvent.click(row.querySelectorAll('button')[0]);
    fireEvent.click(screen.getByRole('switch', { name: 'settings.catalog.brandDefault' }));
    fireEvent.click(screen.getAllByRole('button').find((b) => b.className.includes('text-green-500'))!);
    await waitFor(() => expect(api.updateCatalogEntry).toHaveBeenCalledWith(1, {
      name: 'Sunlu - Plastic V3', weight: 179, manufacturer: 'Sunlu', default_for_brand: true,
    }));
    await waitFor(() => {
      const edited = screen.getByText('Sunlu - Plastic V3').closest('tr')!;
      const core = screen.getByText('Sunlu - Plastic V3 + Cardboard Core').closest('tr')!;
      expect(edited.textContent).toContain('settings.catalog.brandDefault');
      expect(core.textContent).not.toContain('settings.catalog.brandDefault');
    });
  });
});

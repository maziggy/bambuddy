import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, fireEvent, act } from '@testing-library/react';
import { focusManager } from '@tanstack/react-query';
import { render } from '../utils';
import { setColorCatalog, __resetColorCatalogForTests } from '../../utils/colors';
import { LabelTemplatePickerModal } from '../../components/LabelTemplatePickerModal';
import { api, ApiError, setAuthToken } from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => ({
  // The field lists are real exports the modal renders from.
  ...(await importOriginal<typeof import('../../api/client')>()),
  api: {
    printSpoolLabels: vi.fn(),
    printSpoolmanSpoolLabels: vi.fn(),
    previewSpoolLabel: vi.fn(),
    previewSpoolmanSpoolLabel: vi.fn(),
    getSettings: vi.fn().mockResolvedValue({}),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
    getCurrentUser: vi.fn(),
    getUiSettings: vi.fn().mockResolvedValue({}),
    getLabelTemplates: vi.fn(),
    createLabelTemplate: vi.fn(),
    updateLabelTemplate: vi.fn(),
    deleteLabelTemplate: vi.fn(),
  },
}));

const PDF_BLOB = new Blob([new Uint8Array([0x25, 0x50, 0x44, 0x46])], { type: 'application/pdf' });
const PNG_BLOB = new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: 'image/png' });
const ZIP_BLOB = new Blob([new Uint8Array([0x50, 0x4b, 0x03, 0x04])], { type: 'application/zip' });

// What a label carried before the fields were selectable (#2981), in print order.
const DEFAULT_FIELDS = ['brand', 'material', 'hex', 'name', 'location', 'qr', 'spool_id'];

/** The request the print button sends, with the options' defaults filled in. */
function printRequest(overrides: Record<string, unknown>) {
  return {
    monochrome: false,
    starting_position: 1,
    fields: DEFAULT_FIELDS,
    format: 'pdf',
    dpi: 300,
    ...overrides,
  };
}

function pickTemplate(template: string) {
  fireEvent.change(screen.getByTestId('label-template-select'), { target: { value: template } });
}

function clickPrint() {
  fireEvent.click(screen.getByTestId('label-print-button'));
}

const SPOOLS = [
  { id: 1, material: 'PLA', subtype: 'Basic', brand: 'Polymaker', color_name: 'Red', rgba: 'FF0000FF' },
  { id: 2, material: 'PETG', subtype: null, brand: 'Sunlu', color_name: 'Blue', rgba: '0000FFFF' },
  { id: 3, material: 'ABS', subtype: null, brand: null, color_name: 'Black', rgba: '000000FF' },
  { id: 4, material: 'PLA', subtype: 'Matte', brand: 'Polymaker', color_name: 'Ivory', rgba: 'F5E6D3FF' },
];

beforeEach(() => {
  vi.clearAllMocks();
  // setup.ts stubs localStorage with bare mocks; give it a store so the
  // remembered options survive a reopen, as they do in a browser.
  const store = new Map<string, string>();
  vi.mocked(localStorage.getItem).mockImplementation((key) => store.get(key) ?? null);
  vi.mocked(localStorage.setItem).mockImplementation((key, value) => void store.set(key, String(value)));
  vi.mocked(api.previewSpoolLabel).mockResolvedValue(PNG_BLOB);
  vi.mocked(api.previewSpoolmanSpoolLabel).mockResolvedValue(PNG_BLOB);
  vi.mocked(api.getSettings).mockResolvedValue({} as never);
  vi.mocked(api.getAuthStatus).mockResolvedValue({ auth_enabled: false } as never);
  vi.mocked(api.getLabelTemplates).mockResolvedValue([]);
  Object.defineProperty(window.URL, 'createObjectURL', {
    value: vi.fn(() => 'blob:mock'),
    configurable: true,
  });
  Object.defineProperty(window.URL, 'revokeObjectURL', {
    value: vi.fn(),
    configurable: true,
  });
  vi.spyOn(window, 'open').mockImplementation(() => ({}) as Window);
});

describe('a colour name that only the catalog knows (#3090)', () => {
  // The list labels a spool by its colour, and most Bambu spools carry no
  // colour name — the name comes from resolving the swatch's hex against the
  // catalog, which is fetched once at startup. The filter that builds this
  // list is memoised, so it has to be told the catalog arrived; otherwise a
  // search typed first keeps the empty result it computed without one.
  const nameless = [{ id: 9, material: 'PLA', subtype: 'Silk+', brand: 'Bambu Lab', color_name: null, rgba: 'D02727FF' }];

  beforeEach(() => {
    __resetColorCatalogForTests();
  });

  const openModal = () =>
    render(
      <LabelTemplatePickerModal
        isOpen
        onClose={vi.fn()}
        availableSpools={nameless}
        initialSelectedIds={[]}
        spoolmanMode={false}
      />,
    );

  it('labels the spool from the catalog instead of falling back to its material', () => {
    setColorCatalog({ d02727: 'Candy Red' });
    openModal();

    expect(screen.getByText(/Candy Red/)).toBeInTheDocument();
  });

  it('re-filters when the catalog arrives after the query was typed', async () => {
    openModal();
    fireEvent.change(screen.getByPlaceholderText(/Search/i), { target: { value: 'candy' } });
    expect(screen.queryByText(/Candy Red/)).not.toBeInTheDocument();

    setColorCatalog({ d02727: 'Candy Red' });

    await waitFor(() => expect(screen.getByText(/Candy Red/)).toBeInTheDocument());
  });
});

describe('LabelTemplatePickerModal', () => {
  it('does not render when closed', () => {
    render(
      <LabelTemplatePickerModal
        isOpen={false}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[1]}
        spoolmanMode={false}
      />,
    );
    expect(screen.queryByText(/Print spool labels/i)).not.toBeInTheDocument();
  });

  it('lists all available spools by default', () => {
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[1]}
        spoolmanMode={false}
      />,
    );
    expect(screen.getByText(/Red · Polymaker/)).toBeInTheDocument();
    expect(screen.getByText(/Blue · Sunlu/)).toBeInTheDocument();
    expect(screen.getByText(/Black/)).toBeInTheDocument();
    expect(screen.getByText(/Ivory · Polymaker/)).toBeInTheDocument();
  });

  it('keeps the panel from becoming a programmatically scrollable clipping container', () => {
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[1, 2, 3, 4]}
        spoolmanMode={false}
      />,
    );

    const panel = screen.getByTestId('label-template-picker-panel');
    expect(panel).toHaveClass('overflow-clip');
    expect(panel).not.toHaveClass('overflow-hidden');
  });

  it('shows the live selected count in the header', () => {
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[1, 4]}
        spoolmanMode={false}
      />,
    );
    expect(screen.getByText(/2 selected/i)).toBeInTheDocument();
  });

  it('search narrows the list but preserves selection state', () => {
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[3]}  // Black ABS pre-selected
        spoolmanMode={false}
      />,
    );
    const searchInput = screen.getByPlaceholderText(/Search name, brand, or #ID/i);
    fireEvent.change(searchInput, { target: { value: 'polymaker' } });
    // Polymaker spools (Red, Ivory) visible; Sunlu/no-brand hidden
    expect(screen.getByText(/Red · Polymaker/)).toBeInTheDocument();
    expect(screen.getByText(/Ivory · Polymaker/)).toBeInTheDocument();
    expect(screen.queryByText(/Blue · Sunlu/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Black$/)).not.toBeInTheDocument();
    // Selection still includes the now-hidden Black ABS
    expect(screen.getByText(/1 selected/i)).toBeInTheDocument();
  });

  it('search by spool ID works', () => {
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[]}
        spoolmanMode={false}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText(/Search/i), { target: { value: '#2' } });
    expect(screen.getByText(/Blue · Sunlu/)).toBeInTheDocument();
    expect(screen.queryByText(/Red · Polymaker/)).not.toBeInTheDocument();
  });

  it('material chip narrows the visible list', () => {
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[]}
        spoolmanMode={false}
      />,
    );
    // Pick the "PLA" chip
    fireEvent.click(screen.getByRole('button', { name: 'PLA' }));
    expect(screen.getByText(/Red · Polymaker/)).toBeInTheDocument();
    expect(screen.getByText(/Ivory · Polymaker/)).toBeInTheDocument();
    expect(screen.queryByText(/Blue · Sunlu/)).not.toBeInTheDocument();
  });

  it('Select all visible only adds visible spools to the selection', () => {
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[3]}  // start with Black ABS selected
        spoolmanMode={false}
      />,
    );
    // Filter to PLA, then Select all visible — should add the 2 PLA spools to
    // the selection without dropping Black ABS.
    fireEvent.click(screen.getByRole('button', { name: 'PLA' }));
    fireEvent.click(screen.getByText(/Select all visible/i));
    expect(screen.getByText(/3 selected/i)).toBeInTheDocument();
  });

  it('Clear all empties the selection regardless of filter', () => {
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[1, 2, 3, 4]}
        spoolmanMode={false}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'PLA' }));
    fireEvent.click(screen.getByText(/Clear all/i));
    // Header count badge disappears once selection hits 0
    expect(screen.queryByText(/selected/i)).not.toBeInTheDocument();
  });

  it('disables the print button when nothing is selected', () => {
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[]}
        spoolmanMode={false}
      />,
    );
    expect(screen.getByTestId('label-print-button')).toBeDisabled();
  });

  it('sends only the currently checked IDs to the local endpoint', async () => {
    vi.mocked(api.printSpoolLabels).mockResolvedValue(PDF_BLOB);
    const onClose = vi.fn();
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={onClose}
        availableSpools={SPOOLS}
        initialSelectedIds={[1, 2, 3]}
        spoolmanMode={false}
      />,
    );

    fireEvent.click(screen.getByText(/Blue · Sunlu/));  // uncheck spool 2
    pickTemplate('box_62x29');
    clickPrint();

    await waitFor(() => {
      expect(api.printSpoolLabels).toHaveBeenCalledWith(
        printRequest({ spool_ids: [1, 3], template: 'box_62x29' }),
      );
    });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('routes to the Spoolman endpoint when spoolmanMode is true', async () => {
    vi.mocked(api.printSpoolmanSpoolLabels).mockResolvedValue(PDF_BLOB);
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[1]}
        spoolmanMode={true}
      />,
    );

    pickTemplate('ams_holder_75x55');
    clickPrint();

    await waitFor(() => {
      expect(api.printSpoolmanSpoolLabels).toHaveBeenCalledWith(
        printRequest({ spool_ids: [1], template: 'ams_holder_75x55' }),
      );
    });
    expect(api.printSpoolLabels).not.toHaveBeenCalled();
  });

  it('keeps the modal open and shows error when the API rejects', async () => {
    vi.mocked(api.printSpoolLabels).mockRejectedValue(new Error('boom'));
    const onClose = vi.fn();
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={onClose}
        availableSpools={SPOOLS}
        initialSelectedIds={[1]}
        spoolmanMode={false}
      />,
    );

    pickTemplate('avery_l7160');
    clickPrint();

    await waitFor(() => {
      expect(api.printSpoolLabels).toHaveBeenCalled();
    });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('shows empty-state message when no spools are available at all', () => {
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={[]}
        initialSelectedIds={[]}
        spoolmanMode={false}
      />,
    );
    expect(screen.getByText(/No spools to show/i)).toBeInTheDocument();
  });

  it('shows no-matches message when search excludes everything', () => {
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[]}
        spoolmanMode={false}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText(/Search/i), { target: { value: 'zzz-no-match' } });
    expect(screen.getByText(/No spools match/i)).toBeInTheDocument();
  });

  it('offers every template and keeps the footer reachable (#1230)', () => {
    // #1230: the modal outgrew max-h-[90vh] and clipped its footer. The body
    // between header and footer is the part that scrolls, the spool list can
    // shrink (min-h-0), and the template choice is a single select.
    const { container } = render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[]}
        spoolmanMode={false}
      />,
    );

    const options = [...(screen.getByTestId('label-template-select') as HTMLSelectElement).options];
    expect(options.map((o) => o.value)).toEqual([
      'ams_holder_74x33',
      'ams_holder_75x55',
      'box_40x30',
      'box_62x29',
      'avery_l7160',
      'avery_5160',
      'custom',
    ]);
    expect(screen.getByRole('button', { name: /Cancel/i })).toBeInTheDocument();
    expect(screen.getByTestId('label-template-picker-panel')).toHaveClass('max-h-[90vh]');

    const spoolListScroller = container.querySelector('div.flex-1.overflow-y-auto');
    expect(spoolListScroller).not.toBeNull();
    expect(spoolListScroller!.className).toContain('min-h-0');
    expect(spoolListScroller!.className).not.toMatch(/min-h-\[\d/);
  });

  // #1410: an "ID | colour" sort toggle in the modal must flow through to the
  // PDF — the backend (labels.py) prints in the order it receives spool_ids,
  // so the modal's "submit in ID order" default was forcing every PDF to
  // appear in spool-number order regardless of user choice. Toggling to
  // colour mode must reorder both the visible list AND the payload so the
  // printed sheet groups colours together.
  it('sorts the submit payload by HSL hue when sort mode is "By colour" (#1410)', async () => {
    vi.mocked(api.printSpoolLabels).mockResolvedValue(PDF_BLOB);
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[1, 2, 3, 4]}  // Red / Blue / Black / Ivory all picked
        spoolmanMode={false}
      />,
    );

    // Default is ID-sorted; flip to colour.
    fireEvent.click(screen.getByRole('button', { name: 'By colour' }));
    pickTemplate('box_62x29');
    clickPrint();

    await waitFor(() => {
      // Expected colour-sort order for the SPOOLS fixture:
      //   Red    (1) — hue 0°   — chromatic
      //   Ivory  (4) — hue ≈34° — chromatic
      //   Blue   (2) — hue 240° — chromatic
      //   Black  (3) — saturation ≈0 → neutrals bucket, lightness 0 → last
      // Rainbow first, then neutrals (dark→light) per design choice for #1410.
      expect(api.printSpoolLabels).toHaveBeenCalledWith(
        printRequest({ spool_ids: [1, 4, 2, 3], template: 'box_62x29' }),
      );
    });
  });

  it('keeps ID-order submission by default (#1410 regression guard)', async () => {
    // Adding the sort toggle must NOT change the default behaviour — IDs go
    // in ascending order unless the user explicitly clicks "By colour".
    vi.mocked(api.printSpoolLabels).mockResolvedValue(PDF_BLOB);
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[1, 2, 3, 4]}
        spoolmanMode={false}
      />,
    );

    pickTemplate('box_40x30');
    clickPrint();

    await waitFor(() => {
      expect(api.printSpoolLabels).toHaveBeenCalledWith(
        printRequest({ spool_ids: [1, 2, 3, 4], template: 'box_40x30' }),
      );
    });
  });

  it('sends monochrome:true when the black & white checkbox is ticked (#1870)', async () => {
    vi.mocked(api.printSpoolLabels).mockResolvedValue(PDF_BLOB);
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[1]}
        spoolmanMode={false}
      />,
    );

    fireEvent.click(screen.getByText(/black & white printer/i));
    pickTemplate('box_40x30');
    clickPrint();

    await waitFor(() => {
      expect(api.printSpoolLabels).toHaveBeenCalledWith(
        printRequest({ spool_ids: [1], template: 'box_40x30', monochrome: true }),
      );
    });
  });

  it('sends the selected starting position for an Avery sheet', async () => {
    vi.mocked(api.printSpoolLabels).mockResolvedValue(PDF_BLOB);
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[1]}
        spoolmanMode={false}
      />,
    );

    pickTemplate('avery_5160');
    fireEvent.change(screen.getByTestId('label-starting-position'), { target: { value: '8' } });
    expect(screen.getByTestId('label-starting-position-status')).toHaveTextContent(/Positions 1 through 7/i);
    clickPrint();

    await waitFor(() => {
      expect(api.printSpoolLabels).toHaveBeenCalledWith(
        printRequest({ spool_ids: [1], template: 'avery_5160', starting_position: 8 }),
      );
    });
  });

  it('renders a single skipped position without treating the value as a pluralization key', () => {
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[1]}
        spoolmanMode={false}
      />,
    );

    pickTemplate('avery_l7160');
    fireEvent.change(screen.getByTestId('label-starting-position'), { target: { value: '2' } });
    expect(screen.getByTestId('label-starting-position-status')).toHaveTextContent(
      'Positions 1 through 1 will be left blank on the first sheet.',
    );
  });

  it("checks the starting position against the chosen sheet's own capacity", () => {
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[1]}
        spoolmanMode={false}
      />,
    );

    pickTemplate('avery_l7160');
    fireEvent.change(screen.getByTestId('label-starting-position'), { target: { value: '25' } });
    expect(screen.getByTestId('label-starting-position-status')).toHaveTextContent(/from 1 to 21/);
    expect(screen.getByTestId('label-print-button')).toBeDisabled();

    // 25 is on the first sheet of an Avery 5160 (30 per page).
    pickTemplate('avery_5160');
    expect(screen.getByTestId('label-print-button')).toBeEnabled();
  });

  it('offers a starting position only for sheet templates', () => {
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[1]}
        spoolmanMode={false}
      />,
    );

    pickTemplate('box_40x30');
    expect(screen.queryByTestId('label-starting-position')).not.toBeInTheDocument();
    pickTemplate('avery_5160');
    expect(screen.getByTestId('label-starting-position')).toBeInTheDocument();
  });
});

describe('choosing what the label shows (#2981)', () => {
  const openModal = (props: Partial<Parameters<typeof LabelTemplatePickerModal>[0]> = {}) =>
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[1]}
        spoolmanMode={false}
        {...props}
      />,
    );

  it('starts from the lines labels always carried', () => {
    openModal();
    for (const field of DEFAULT_FIELDS) {
      expect(screen.getByTestId(`label-field-${field}`)).toBeChecked();
    }
    for (const field of ['material_number', 'temps', 'weight', 'note', 'added']) {
      expect(screen.getByTestId(`label-field-${field}`)).not.toBeChecked();
    }
  });

  it('sends the chosen lines in print order', async () => {
    vi.mocked(api.printSpoolLabels).mockResolvedValue(PDF_BLOB);
    openModal();
    pickTemplate('box_40x30');

    fireEvent.click(screen.getByTestId('label-field-temps'));
    fireEvent.click(screen.getByTestId('label-field-qr'));
    fireEvent.click(screen.getByTestId('label-field-brand'));
    clickPrint();

    await waitFor(() => {
      expect(api.printSpoolLabels).toHaveBeenCalledWith(
        printRequest({
          spool_ids: [1],
          template: 'box_40x30',
          fields: ['material', 'hex', 'name', 'location', 'temps', 'spool_id'],
        }),
      );
    });
  });

  it('puts the defaults back on reset', () => {
    openModal();
    fireEvent.click(screen.getByTestId('label-field-brand'));
    fireEvent.click(screen.getByTestId('label-field-weight'));

    fireEvent.click(screen.getByRole('button', { name: /Reset to default/i }));

    expect(screen.getByTestId('label-field-brand')).toBeChecked();
    expect(screen.getByTestId('label-field-weight')).not.toBeChecked();
  });

  it('remembers the lines per template, and the rest of the options, for next time', () => {
    const { unmount } = openModal();
    pickTemplate('box_40x30');
    fireEvent.click(screen.getByTestId('label-field-weight'));
    fireEvent.click(screen.getByText(/black & white printer/i));
    fireEvent.click(screen.getByTestId('label-format-png'));
    unmount();

    openModal();
    expect(screen.getByTestId('label-template-select')).toHaveValue('box_40x30');
    expect(screen.getByTestId('label-field-weight')).toBeChecked();
    expect(screen.getByRole('checkbox', { name: /black & white printer/i })).toBeChecked();
    expect(screen.getByTestId('label-format-png')).toHaveAttribute('aria-pressed', 'true');

    // A sheet holds a different amount, so its lines are chosen separately.
    pickTemplate('avery_l7160');
    expect(screen.getByTestId('label-field-weight')).not.toBeChecked();
  });

  it('ignores remembered options it does not recognise', () => {
    localStorage.setItem(
      'bambuddy-label-print-options',
      JSON.stringify({ template: 'gone', fields: { box_40x30: ['brand', 'bogus'] }, format: 'tiff', dpi: 1 }),
    );
    openModal();

    expect(screen.getByTestId('label-template-select')).toHaveValue('ams_holder_74x33');
    expect(screen.getByTestId('label-format-pdf')).toHaveAttribute('aria-pressed', 'true');
    pickTemplate('box_40x30');
    expect(screen.getByTestId('label-field-brand')).toBeChecked();
    expect(screen.getByTestId('label-field-material')).not.toBeChecked();
  });
});

describe('the label preview (#2981)', () => {
  it('renders the first spool that will print, with the chosen options', async () => {
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[4, 2]}
        spoolmanMode={false}
      />,
    );
    pickTemplate('box_62x29');
    fireEvent.click(screen.getByTestId('label-field-temps'));

    await waitFor(() => {
      expect(api.previewSpoolLabel).toHaveBeenLastCalledWith(
        {
          spool_id: 2,
          template: 'box_62x29',
          monochrome: false,
          fields: [...DEFAULT_FIELDS.slice(0, 5), 'temps', 'qr', 'spool_id'],
        },
        expect.any(AbortSignal),
      );
    });
    expect(await screen.findByAltText('Label preview')).toHaveAttribute('src', 'blob:mock');
    expect(screen.getByText('Preview of #2')).toBeInTheDocument();
  });

  it('asks for a spool when none is selected', () => {
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[]}
        spoolmanMode={false}
      />,
    );
    expect(screen.getByText(/Select a spool to see its label/i)).toBeInTheDocument();
    expect(api.previewSpoolLabel).not.toHaveBeenCalled();
  });

  it('uses the Spoolman preview in Spoolman mode', async () => {
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[1]}
        spoolmanMode={true}
      />,
    );
    await waitFor(() => expect(api.previewSpoolmanSpoolLabel).toHaveBeenCalled());
    expect(api.previewSpoolLabel).not.toHaveBeenCalled();
  });

  it('says why when the preview fails', async () => {
    vi.mocked(api.previewSpoolLabel).mockRejectedValue(new Error('Spoolman not reachable'));
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[1]}
        spoolmanMode={false}
      />,
    );
    expect(await screen.findByText(/Could not render the preview: Spoolman not reachable/)).toBeInTheDocument();
  });
});

describe('PNG output (#2981)', () => {
  function captureDownloads() {
    const names: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      names.push(this.download);
    });
    return names;
  }

  it('sends the format and resolution, and saves the image instead of opening a tab', async () => {
    vi.mocked(api.printSpoolLabels).mockResolvedValue(PNG_BLOB);
    const downloads = captureDownloads();
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[1]}
        spoolmanMode={false}
      />,
    );
    pickTemplate('box_40x30');
    fireEvent.click(screen.getByTestId('label-format-png'));
    fireEvent.change(screen.getByTestId('label-dpi-select'), { target: { value: '203' } });
    clickPrint();

    await waitFor(() => {
      expect(api.printSpoolLabels).toHaveBeenCalledWith(
        printRequest({ spool_ids: [1], template: 'box_40x30', format: 'png', dpi: 203 }),
      );
    });
    await waitFor(() => expect(downloads).toEqual(['bambuddy-labels-box_40x30.png']));
    expect(window.open).not.toHaveBeenCalled();
  });

  it('names several labels as a ZIP', async () => {
    vi.mocked(api.printSpoolLabels).mockResolvedValue(ZIP_BLOB);
    const downloads = captureDownloads();
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[1, 2]}
        spoolmanMode={false}
      />,
    );
    pickTemplate('box_40x30');
    fireEvent.click(screen.getByTestId('label-format-png'));
    clickPrint();

    await waitFor(() => expect(downloads).toEqual(['bambuddy-labels-box_40x30.zip']));
  });

  it('only offers a resolution for PNG', () => {
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[1]}
        spoolmanMode={false}
      />,
    );
    expect(screen.queryByTestId('label-dpi-select')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('label-format-png'));
    expect(screen.getByTestId('label-dpi-select')).toHaveValue('300');
  });
});

describe('custom sheets and saved templates (#3296)', () => {
  // A 3 × 8 sheet of 70 × 36 mm labels on A4, edge to edge (Avery 3490).
  const LAYOUT_3490 = {
    page: 'a4',
    label_w_mm: 70,
    label_h_mm: 36,
    cols: 3,
    rows: 8,
    top_mm: 4.5,
    left_mm: 0,
    col_gap_mm: 0,
    row_gap_mm: 0,
  } as const;
  const SAVED = {
    id: 5,
    name: 'Avery 3490',
    layout: LAYOUT_3490,
    created_at: '2026-10-25T00:00:00Z',
    updated_at: '2026-10-25T00:00:00Z',
  };

  const openModal = (spoolmanMode = false) =>
    render(
      <LabelTemplatePickerModal
        isOpen
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[1]}
        spoolmanMode={spoolmanMode}
      />,
    );

  function typeLayout(layout: Record<string, string | number>) {
    for (const [field, value] of Object.entries(layout)) {
      if (field === 'page') {
        fireEvent.change(screen.getByTestId('label-sheet-page'), { target: { value } });
      } else {
        fireEvent.change(screen.getByTestId(`label-sheet-${field}`), { target: { value: String(value) } });
      }
    }
  }

  it('prints a one-off custom sheet with its layout', async () => {
    vi.mocked(api.printSpoolLabels).mockResolvedValue(PDF_BLOB);
    openModal();
    pickTemplate('custom');
    typeLayout(LAYOUT_3490);
    fireEvent.change(screen.getByTestId('label-starting-position'), { target: { value: '24' } });
    clickPrint();

    await waitFor(() =>
      expect(api.printSpoolLabels).toHaveBeenCalledWith(
        printRequest({ spool_ids: [1], template: 'custom', layout: LAYOUT_3490, starting_position: 24 }),
      ),
    );
  });

  it('bounds the starting position by the custom grid', () => {
    openModal();
    pickTemplate('custom');
    typeLayout(LAYOUT_3490);
    fireEvent.change(screen.getByTestId('label-starting-position'), { target: { value: '25' } });

    expect(screen.getByTestId('label-starting-position-status')).toHaveTextContent('1 to 24');
    expect(screen.getByTestId('label-print-button')).toBeDisabled();
  });

  it('explains a sheet that runs off the page and will not print it', async () => {
    openModal();
    pickTemplate('custom');
    typeLayout({ ...LAYOUT_3490, left_mm: 1 });

    expect(screen.getByTestId('label-sheet-problem')).toHaveTextContent('211 mm wide');
    expect(screen.getAllByTestId('label-sheet-cell-off-page')).toHaveLength(8);
    expect(screen.getByTestId('label-print-button')).toBeDisabled();
    expect(screen.getByTestId('label-preview')).toHaveTextContent(/sheet layout is complete/);
  });

  it('asks for every field before drawing or printing', () => {
    openModal();
    pickTemplate('custom');
    typeLayout({ rows: '' });

    expect(screen.getByTestId('label-sheet-problem')).toHaveTextContent(/every field/);
    expect(screen.queryByTestId('label-sheet-diagram')).not.toBeInTheDocument();
    expect(screen.getByTestId('label-print-button')).toBeDisabled();
  });

  it('previews a custom sheet with its layout', async () => {
    openModal();
    pickTemplate('custom');
    typeLayout(LAYOUT_3490);

    await waitFor(() =>
      expect(api.previewSpoolLabel).toHaveBeenLastCalledWith(
        expect.objectContaining({ template: 'custom', layout: LAYOUT_3490 }),
        expect.anything(),
      ),
    );
  });

  it('remembers the custom sheet for next time', () => {
    const first = openModal();
    pickTemplate('custom');
    typeLayout({ label_w_mm: '70', rows: '8' });
    first.unmount();

    openModal();
    expect(screen.getByTestId('label-template-select')).toHaveValue('custom');
    expect(screen.getByTestId('label-sheet-label_w_mm')).toHaveValue('70');
    expect(screen.getByTestId('label-sheet-rows')).toHaveValue('8');
  });

  it('lists saved templates and prints with the saved layout', async () => {
    vi.mocked(api.getLabelTemplates).mockResolvedValue([SAVED]);
    vi.mocked(api.printSpoolmanSpoolLabels).mockResolvedValue(PDF_BLOB);
    openModal(true);

    await screen.findByRole('option', { name: 'Avery 3490' });
    pickTemplate('saved:5');
    expect(screen.getByTestId('label-template-hint')).toHaveTextContent('24 labels (3 × 8)');
    expect(screen.getByTestId('label-sheet-diagram')).toBeInTheDocument();
    clickPrint();

    await waitFor(() =>
      expect(api.printSpoolmanSpoolLabels).toHaveBeenCalledWith(
        printRequest({ spool_ids: [1], template: 'custom', layout: LAYOUT_3490 }),
      ),
    );
  });

  it('saves a custom sheet under a name and switches to it', async () => {
    vi.mocked(api.createLabelTemplate).mockResolvedValue(SAVED);
    openModal();
    pickTemplate('custom');
    typeLayout(LAYOUT_3490);
    fireEvent.click(screen.getByTestId('label-field-note'));
    fireEvent.click(screen.getByTestId('label-template-save-as'));
    fireEvent.change(screen.getByTestId('label-template-name'), { target: { value: '  Avery 3490 ' } });
    // The list the dialog reloads after saving.
    vi.mocked(api.getLabelTemplates).mockResolvedValue([SAVED]);
    fireEvent.click(screen.getByTestId('label-template-save'));

    await waitFor(() =>
      expect(api.createLabelTemplate).toHaveBeenCalledWith({ name: 'Avery 3490', layout: LAYOUT_3490 }),
    );
    await waitFor(() => expect(screen.getByTestId('label-template-select')).toHaveValue('saved:5'));
    // The lines chosen for the custom sheet came along.
    expect(screen.getByTestId('label-field-note')).toBeChecked();
  });

  it('edits a saved template in place or as a new one', async () => {
    vi.mocked(api.getLabelTemplates).mockResolvedValue([SAVED]);
    const updated = { ...SAVED, layout: { ...LAYOUT_3490, rows: 7 } };
    vi.mocked(api.updateLabelTemplate).mockResolvedValue(updated);
    openModal();
    await screen.findByRole('option', { name: 'Avery 3490' });
    pickTemplate('saved:5');

    fireEvent.click(screen.getByTestId('label-template-edit'));
    typeLayout({ rows: '7' });
    vi.mocked(api.getLabelTemplates).mockResolvedValue([updated]);
    fireEvent.click(screen.getByTestId('label-template-save-changes'));
    await waitFor(() =>
      expect(api.updateLabelTemplate).toHaveBeenCalledWith(5, {
        name: 'Avery 3490',
        layout: { ...LAYOUT_3490, rows: 7 },
      }),
    );

    const copy = { ...updated, id: 6, name: 'Avery 3490 short' };
    vi.mocked(api.createLabelTemplate).mockResolvedValue(copy);
    vi.mocked(api.getLabelTemplates).mockResolvedValue([updated, copy]);
    await waitFor(() => expect(screen.getByTestId('label-template-edit')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('label-template-edit'));
    fireEvent.change(screen.getByTestId('label-template-name'), { target: { value: 'Avery 3490 short' } });
    fireEvent.click(screen.getByTestId('label-template-save-as-new'));
    await waitFor(() =>
      expect(api.createLabelTemplate).toHaveBeenCalledWith({
        name: 'Avery 3490 short',
        layout: { ...LAYOUT_3490, rows: 7 },
      }),
    );
    await waitFor(() => expect(screen.getByTestId('label-template-select')).toHaveValue('saved:6'));
  });

  it('deletes a saved template after confirming and goes back to the default', async () => {
    vi.mocked(api.getLabelTemplates).mockResolvedValue([SAVED]);
    vi.mocked(api.deleteLabelTemplate).mockResolvedValue(undefined);
    openModal();
    await screen.findByRole('option', { name: 'Avery 3490' });
    pickTemplate('saved:5');

    fireEvent.click(screen.getByTestId('label-template-delete'));
    expect(api.deleteLabelTemplate).not.toHaveBeenCalled();
    vi.mocked(api.getLabelTemplates).mockResolvedValue([]);
    // The confirm dialog's button, not the link that opened it.
    fireEvent.click(screen.getAllByRole('button', { name: 'Delete' }).at(-1)!);

    await waitFor(() => expect(api.deleteLabelTemplate).toHaveBeenCalledWith(5));
    await waitFor(() => expect(screen.getByTestId('label-template-select')).toHaveValue('ams_holder_74x33'));
  });

  it('falls back to the default when a remembered template is gone', async () => {
    const store = new Map([['bambuddy-label-print-options', JSON.stringify({ template: 'saved:9' })]]);
    vi.mocked(localStorage.getItem).mockImplementation((key) => store.get(key) ?? null);
    openModal();

    await waitFor(() => expect(api.getLabelTemplates).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByTestId('label-template-select')).toHaveValue('ams_holder_74x33'));
  });

  it('refuses a label too small to carry its text', () => {
    openModal();
    pickTemplate('custom');
    typeLayout({ ...LAYOUT_3490, label_w_mm: '39', cols: '1' });

    expect(screen.getByTestId('label-sheet-problem')).toHaveTextContent('at least 40 mm wide and 20 mm tall');
    expect(screen.getByTestId('label-print-button')).toBeDisabled();
  });

  it('measures US Letter exactly as the server does', () => {
    openModal();
    pickTemplate('custom');
    // Two 107.95 mm labels fill Letter's 215.9 mm; a 0.02 mm gap does not fit.
    typeLayout({ ...LAYOUT_3490, page: 'letter', label_w_mm: '107.95', cols: '2', rows: '1', top_mm: '0' });
    expect(screen.queryByTestId('label-sheet-problem')).not.toBeInTheDocument();

    typeLayout({ col_gap_mm: '0.02' });
    expect(screen.getByTestId('label-sheet-problem')).toHaveTextContent('215.92 mm wide');
  });

  it('caps every length as the server does', () => {
    openModal();
    pickTemplate('custom');
    typeLayout({ ...LAYOUT_3490, cols: '1', rows: '1', col_gap_mm: '301' });

    expect(screen.getByTestId('label-sheet-problem')).toHaveTextContent('at most 300 mm');
  });

  it('links each field to the explanation of what is wrong', () => {
    openModal();
    pickTemplate('custom');
    typeLayout({ ...LAYOUT_3490, left_mm: '1' });

    const problem = screen.getByTestId('label-sheet-problem');
    expect(screen.getByTestId('label-sheet-left_mm').getAttribute('aria-describedby')).toContain(problem.id);
    expect(screen.getByTestId('label-sheet-left_mm')).toHaveAttribute('aria-invalid', 'true');
  });

  it('says in the reader\'s language when a name is already taken', async () => {
    vi.mocked(api.createLabelTemplate).mockRejectedValue(
      new ApiError('A label template with this name already exists', 409),
    );
    openModal();
    pickTemplate('custom');
    typeLayout(LAYOUT_3490);
    fireEvent.click(screen.getByTestId('label-template-save-as'));
    fireEvent.change(screen.getByTestId('label-template-name'), { target: { value: 'Avery 3490' } });
    fireEvent.click(screen.getByTestId('label-template-save'));

    expect(await screen.findByText('A label template with this name already exists.')).toBeInTheDocument();
    expect(screen.queryByText(/Could not save the template/)).not.toBeInTheDocument();
  });

  it('keeps the lines ticked while the save was on its way', async () => {
    let finishSave: (value: typeof SAVED) => void = () => {};
    vi.mocked(api.createLabelTemplate).mockReturnValue(new Promise((resolve) => (finishSave = resolve)));
    openModal();
    pickTemplate('custom');
    typeLayout(LAYOUT_3490);
    fireEvent.click(screen.getByTestId('label-template-save-as'));
    fireEvent.change(screen.getByTestId('label-template-name'), { target: { value: 'Avery 3490' } });
    fireEvent.click(screen.getByTestId('label-template-save'));
    // Ticked after clicking Save, before the server answered.
    fireEvent.click(screen.getByTestId('label-field-weight'));
    vi.mocked(api.getLabelTemplates).mockResolvedValue([SAVED]);
    await act(async () => finishSave(SAVED));

    await waitFor(() => expect(screen.getByTestId('label-template-select')).toHaveValue('saved:5'));
    expect(screen.getByTestId('label-field-weight')).toBeChecked();
  });

  it('refuses a PNG of more sheet pages than the resolution allows', () => {
    const many = Array.from({ length: 26 }, (_, i) => ({ ...SPOOLS[0], id: i + 1 }));
    render(
      <LabelTemplatePickerModal
        isOpen
        onClose={vi.fn()}
        availableSpools={many}
        initialSelectedIds={many.map((s) => s.id)}
        spoolmanMode={false}
      />,
    );
    pickTemplate('custom');
    typeLayout({ ...LAYOUT_3490, cols: '1', rows: '1' });
    fireEvent.click(screen.getByTestId('label-format-png'));
    fireEvent.change(screen.getByTestId('label-dpi-select'), { target: { value: '600' } });

    // 26 one-label pages at 600 dpi is over the 25 allowed.
    expect(screen.getByTestId('label-png-too-large')).toHaveTextContent('25 sheet pages at 600 dpi, and this is 26');
    expect(screen.getByTestId('label-print-button')).toBeDisabled();

    fireEvent.change(screen.getByTestId('label-dpi-select'), { target: { value: '300' } });
    expect(screen.queryByTestId('label-png-too-large')).not.toBeInTheDocument();
    expect(screen.getByTestId('label-print-button')).toBeEnabled();
  });

  it('leaves an edit of a template deleted elsewhere, once a fresh list says so', async () => {
    vi.mocked(api.getLabelTemplates).mockResolvedValue([SAVED]);
    openModal();
    await screen.findByRole('option', { name: 'Avery 3490' });
    pickTemplate('saved:5');
    fireEvent.click(screen.getByTestId('label-template-edit'));
    expect(screen.getByTestId('label-sheet-editor')).toBeInTheDocument();

    // Someone else deletes it; the list is fetched again when the window regains focus.
    vi.mocked(api.getLabelTemplates).mockResolvedValue([]);
    act(() => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    });

    await waitFor(() => expect(screen.getByTestId('label-template-select')).toHaveValue('ams_holder_74x33'));
    expect(screen.queryByTestId('label-sheet-editor')).not.toBeInTheDocument();
    act(() => focusManager.setFocused(undefined));
  });

  it('keeps a remembered template while a cached list is being refreshed', async () => {
    // First open: the list has no templates, and stays cached in the query client.
    vi.mocked(api.getLabelTemplates).mockResolvedValue([]);
    const props = {
      onClose: vi.fn(),
      availableSpools: SPOOLS,
      initialSelectedIds: [1],
      spoolmanMode: false,
    };
    const view = render(<LabelTemplatePickerModal isOpen {...props} />);
    await waitFor(() => expect(api.getLabelTemplates).toHaveBeenCalledTimes(1));
    view.rerender(<LabelTemplatePickerModal isOpen={false} {...props} />);

    // Meanwhile the template was saved elsewhere and chosen in this browser.
    const store = new Map([['bambuddy-label-print-options', JSON.stringify({ template: 'saved:5' })]]);
    vi.mocked(localStorage.getItem).mockImplementation((key) => store.get(key) ?? null);
    vi.mocked(localStorage.setItem).mockImplementation((key, value) => void store.set(key, String(value)));
    let finish: (value: (typeof SAVED)[]) => void = () => {};
    vi.mocked(api.getLabelTemplates).mockReturnValue(new Promise((resolve) => (finish = resolve)));

    // Reopen: the cached list lacks it, the refetch is still on its way.
    view.rerender(<LabelTemplatePickerModal isOpen {...props} />);
    await waitFor(() => expect(api.getLabelTemplates).toHaveBeenCalledTimes(2));
    expect(store.get('bambuddy-label-print-options')).toContain('saved:5');

    await act(async () => finish([SAVED]));
    await waitFor(() => expect(screen.getByTestId('label-template-select')).toHaveValue('saved:5'));
    expect(store.get('bambuddy-label-print-options')).toContain('saved:5');
  });

  describe('for a user who may print but not edit inventory', () => {
    beforeEach(() => {
      setAuthToken('viewer-token');
      vi.mocked(api.getAuthStatus).mockResolvedValue({ auth_enabled: true, requires_setup: false } as never);
      vi.mocked(api.getCurrentUser).mockResolvedValue({
        id: 3,
        username: 'viewer',
        is_admin: false,
        is_active: true,
        groups: [],
        permissions: ['inventory:read'],
      } as never);
      vi.mocked(api.getLabelTemplates).mockResolvedValue([SAVED]);
    });

    afterEach(() => {
      setAuthToken(null);
    });

    it('can use saved templates and custom sheets but not change templates', async () => {
      openModal();
      await waitFor(() => expect(api.getCurrentUser).toHaveBeenCalled());
      await screen.findByRole('option', { name: 'Avery 3490' });

      pickTemplate('saved:5');
      expect(screen.getByTestId('label-sheet-diagram')).toBeInTheDocument();
      expect(screen.queryByTestId('label-template-edit')).not.toBeInTheDocument();
      expect(screen.queryByTestId('label-template-delete')).not.toBeInTheDocument();

      pickTemplate('custom');
      typeLayout(LAYOUT_3490);
      expect(screen.queryByTestId('label-template-save-as')).not.toBeInTheDocument();
      expect(screen.getByTestId('label-print-button')).toBeEnabled();
    });
  });
});

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { LabelTemplatePickerModal } from '../../components/LabelTemplatePickerModal';
import { api } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    printSpoolLabels: vi.fn(),
    printSpoolmanSpoolLabels: vi.fn(),
    getSettings: vi.fn().mockResolvedValue({}),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
  },
}));

const PDF_BLOB = new Blob([new Uint8Array([0x25, 0x50, 0x44, 0x46])], { type: 'application/pdf' });

const SPOOLS = [
  { id: 1, material: 'PLA', subtype: 'Basic', brand: 'Polymaker', color_name: 'Red', rgba: 'FF0000FF' },
  { id: 2, material: 'PETG', subtype: null, brand: 'Sunlu', color_name: 'Blue', rgba: '0000FFFF' },
  { id: 3, material: 'ABS', subtype: null, brand: null, color_name: 'Black', rgba: '000000FF' },
  { id: 4, material: 'PLA', subtype: 'Matte', brand: 'Polymaker', color_name: 'Ivory', rgba: 'F5E6D3FF' },
];

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getSettings).mockResolvedValue({} as never);
  vi.mocked(api.getAuthStatus).mockResolvedValue({ auth_enabled: false } as never);
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

  it('template buttons disabled when nothing is selected', () => {
    render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[]}
        spoolmanMode={false}
      />,
    );
    expect(screen.getByText(/AMS holder/i).closest('button')).toBeDisabled();
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
    fireEvent.click(screen.getByText(/Box label/i));

    await waitFor(() => {
      expect(api.printSpoolLabels).toHaveBeenCalledWith({
        spool_ids: [1, 3],
        template: 'box_62x29',
      });
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

    fireEvent.click(screen.getByText(/AMS holder/i));

    await waitFor(() => {
      expect(api.printSpoolmanSpoolLabels).toHaveBeenCalledWith({
        spool_ids: [1],
        template: 'ams_30x15',
      });
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

    fireEvent.click(screen.getByText(/Avery L7160/i));

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

  it('packs templates into a 2x2 grid so all 4 plus Cancel fit on short viewports (#1230)', () => {
    // Regression for #1230: with 4 templates stacked vertically (~310px) plus
    // header/search/action bar/footer, the modal blew past max-h-[90vh] on
    // Windows-11 + Brave-style viewports where browser chrome eats into 90vh.
    // overflow-hidden on the modal then clipped Avery 5160 and the Cancel
    // footer with no scroll path. The fix uses sm:grid-cols-2 so the 4
    // templates render as a 2x2 grid (~155px), trimming ~150px of vertical
    // and leaving room for the footer. The earlier min-h-0 on the spool list
    // is kept so it still yields any remaining slack.
    const { container } = render(
      <LabelTemplatePickerModal
        isOpen={true}
        onClose={vi.fn()}
        availableSpools={SPOOLS}
        initialSelectedIds={[]}
        spoolmanMode={false}
      />,
    );

    // All 4 templates must be in the DOM, including the last one.
    expect(screen.getByText(/AMS holder/i)).toBeInTheDocument();
    expect(screen.getByText(/Box label/i)).toBeInTheDocument();
    expect(screen.getByText(/Avery L7160/i)).toBeInTheDocument();
    expect(screen.getByText(/Avery 5160/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Cancel/i })).toBeInTheDocument();

    // Templates section must be a responsive grid (single column on mobile,
    // two columns from sm: up) — a future refactor that drops the grid and
    // reintroduces stacked rows fails CI.
    const templatesSection = container.querySelector('div.grid.sm\\:grid-cols-2');
    expect(templatesSection).not.toBeNull();
    expect(templatesSection!.className).toContain('grid-cols-1');
    expect(templatesSection!.querySelectorAll('button').length).toBe(4);

    // Spool list still uses min-h-0 so it can yield further on very tight viewports.
    const spoolListScroller = container.querySelector('div.flex-1.overflow-y-auto');
    expect(spoolListScroller).not.toBeNull();
    expect(spoolListScroller!.className).toContain('min-h-0');
    expect(spoolListScroller!.className).not.toMatch(/min-h-\[\d/);
  });
});

/**
 * The unknown-tag prompt can link the tag to a spool already in the
 * inventory (#3658), and asks which one when several match.
 */
import { describe, it, expect, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { render, screen, fireEvent, waitFor } from '../utils';
import { UnknownSpoolModal } from '../../components/UnknownSpoolModal';
import type { UnknownSpoolPrompt } from '../../hooks/useUnknownTagPrompt';

const base = {
  material: 'PLA',
  subtype: 'Matte',
  brand: 'Bambu Lab',
  color_name: 'Charcoal',
  rgba: '333333FF',
  label_weight: 1000,
  tag_uid: null,
  tray_uuid: null,
  archived_at: null,
};
const SPOOLS = [
  { ...base, id: 12, weight_used: 0, note: 'Sealed box' },
  { ...base, id: 13, weight_used: 0, note: 'Sealed box' },
  { ...base, id: 30, weight_used: 664, note: null },
  { ...base, id: 31, rgba: 'FFFFFFFF', color_name: 'Ivory White', weight_used: 0, note: null },
];

const prompt = (candidate_spool_ids: number[] = []): UnknownSpoolPrompt => ({
  printer_id: 1,
  ams_id: 0,
  tray_id: 0,
  printer_name: 'X1C',
  ams_label: 'AMS A',
  slot_label: 'Slot 1',
  material: 'PLA',
  color_hex: '333333FF',
  brand: 'PLA Matte',
  tag_uid: 'AABBCCDD11223344',
  tray_uuid: 'AABBCCDDEEFF0011AABBCCDDEEFF0011',
  candidate_spool_ids,
});

function setup(p: UnknownSpoolPrompt, canLink = true) {
  server.use(http.get('/api/v1/inventory/spools', () => HttpResponse.json(SPOOLS)));
  const onConfirm = vi.fn();
  const onLink = vi.fn();
  const onCancel = vi.fn();
  render(<UnknownSpoolModal prompt={p} isPending={false} canLink={canLink} onConfirm={onConfirm} onLink={onLink} onCancel={onCancel} />);
  return { onConfirm, onLink, onCancel };
}

describe('UnknownSpoolModal', () => {
  it('asks which spool it is when several match, matching ones first', async () => {
    const { onLink, onConfirm } = setup(prompt([12, 13, 30]));

    expect(screen.getByText(/several spools in your inventory match it/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByRole('radio')).toHaveLength(4));
    const rows = screen.getAllByRole('radio').map((r) => (r as HTMLInputElement).value);
    expect(rows).toEqual(['12', '13', '30', '31']);
    expect(screen.getByText('Matching spools')).toBeInTheDocument();
    expect(screen.getByText('664 g used')).toBeInTheDocument();
    expect(screen.getAllByText('“Sealed box”')).toHaveLength(2);

    const link = screen.getByRole('button', { name: 'Link' });
    expect(link).toBeDisabled();
    fireEvent.click(screen.getAllByRole('radio')[2]);
    fireEvent.click(screen.getByRole('button', { name: 'Link to #30' }));

    expect(onLink).toHaveBeenCalledWith(30);
    expect(onConfirm).not.toHaveBeenCalled();
    // Adding a new spool stays possible.
    expect(screen.getByRole('button', { name: 'Add as new spool' })).toBeInTheDocument();
  });

  it('offers linking next to adding when nothing matched', async () => {
    const { onLink } = setup(prompt());

    expect(screen.queryByRole('radio')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add to Inventory' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Link to existing spool…' }));

    await waitFor(() => expect(screen.getAllByRole('radio')).toHaveLength(4));
    fireEvent.click(screen.getAllByRole('radio')[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Link to #12' }));
    expect(onLink).toHaveBeenCalledWith(12);
  });

  it('keeps the plain add prompt in Spoolman mode', () => {
    const { onConfirm } = setup(prompt([12, 30]), false);

    expect(screen.queryByRole('button', { name: /link/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('radio')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Add to Inventory' }));
    expect(onConfirm).toHaveBeenCalled();
  });
});

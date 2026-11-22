/**
 * Linking a slot's RFID tag to a spool, or moving it from the spool it went
 * to by mistake (#3658). One link-tag call does it.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { render, screen, fireEvent, waitFor } from '../utils';
import { api } from '../../api/client';
import type { InventorySpool } from '../../api/client';
import { LinkTagModal } from '../../components/LinkTagModal';

const TAG_UID = 'AABBCCDD11223344';
const TRAY_UUID = 'AABBCCDDEEFF0011AABBCCDDEEFF0011';
const base = {
  material: 'PLA',
  subtype: 'Matte',
  brand: 'Bambu Lab',
  color_name: 'Charcoal',
  rgba: '333333FF',
  label_weight: 1000,
  weight_used: 0,
  note: null,
  tag_uid: null,
  tray_uuid: null,
  tag_type: null,
  data_origin: null,
  archived_at: null,
};
const WRONG = { ...base, id: 12, tag_uid: TAG_UID, tray_uuid: TRAY_UUID, tag_type: 'bambulab', data_origin: 'rfid_linked', weight_used: 12 };
const SPOOLS = [WRONG, { ...base, id: 13 }, { ...base, id: 30, weight_used: 664 }];

afterEach(() => vi.restoreAllMocks());

function serve() {
  server.use(http.get('/api/v1/inventory/spools', () => HttpResponse.json(SPOOLS)));
  return vi.spyOn(api, 'linkTagToSpool').mockResolvedValue({} as InventorySpool);
}

describe('LinkTagModal', () => {
  it('moves the tag and the slot, and says the usage stays with the old spool', async () => {
    const link = serve();
    const onClose = vi.fn();
    render(
      <LinkTagModal
        target={{
          tag: { tag_uid: TAG_UID, tray_uuid: TRAY_UUID },
          holder: WRONG as unknown as InventorySpool,
          slot: { printerId: 1, amsId: 0, trayId: 2 },
          location: 'X1C • AMS A Slot 3',
          material: 'PLA',
          colorHex: '333333FF',
        }}
        onClose={onClose}
      />,
    );

    expect(screen.getByText('Move tag to another spool')).toBeInTheDocument();
    expect(screen.getByText(/already booked to #12 stays with #12/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByRole('radio')).toHaveLength(2));
    // The spool that has the tag is no choice.
    expect(screen.getAllByRole('radio').map((r) => (r as HTMLInputElement).value)).toEqual(['13', '30']);

    fireEvent.click(screen.getAllByRole('radio')[1]);
    fireEvent.click(screen.getByRole('button', { name: 'Move to #30' }));

    await waitFor(() =>
      expect(link).toHaveBeenCalledWith(30, {
        tag_uid: TAG_UID,
        tray_uuid: TRAY_UUID,
        tag_type: 'bambulab',
        data_origin: 'rfid_linked',
        move: true,
        printer_id: 1,
        ams_id: 0,
        tray_id: 2,
      }),
    );
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("moves a spool's own tag from the spool dialog, without a slot", async () => {
    const link = serve();
    const onLinked = vi.fn();
    render(
      <LinkTagModal
        target={{ holder: WRONG as unknown as InventorySpool, material: 'PLA', colorHex: '333333FF' }}
        onClose={vi.fn()}
        onLinked={onLinked}
      />,
    );

    await waitFor(() => expect(screen.getAllByRole('radio')).toHaveLength(2));
    fireEvent.click(screen.getAllByRole('radio')[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Move to #13' }));

    await waitFor(() =>
      expect(link).toHaveBeenCalledWith(13, {
        tag_uid: TAG_UID,
        tray_uuid: TRAY_UUID,
        tag_type: 'bambulab',
        data_origin: 'rfid_linked',
        move: true,
      }),
    );
    await waitFor(() => expect(onLinked).toHaveBeenCalled());
  });

  it('links the tag of an unassigned RFID slot without moving anything', async () => {
    const link = serve();
    render(
      <LinkTagModal
        target={{
          tag: { tag_uid: TAG_UID, tray_uuid: '0'.repeat(32) },
          slot: { printerId: 1, amsId: 0, trayId: 0 },
          location: 'X1C • AMS A Slot 1',
          material: 'PLA',
          colorHex: '333333FF',
        }}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText('Link tag to a spool')).toBeInTheDocument();
    expect(screen.queryByText(/stays with/)).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByRole('radio')).toHaveLength(2));
    fireEvent.click(screen.getAllByRole('radio')[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Link to #13' }));

    await waitFor(() =>
      expect(link).toHaveBeenCalledWith(13, {
        tag_uid: TAG_UID,
        tray_uuid: undefined,
        tag_type: undefined,
        data_origin: 'rfid_linked',
        move: undefined,
        printer_id: 1,
        ams_id: 0,
        tray_id: 0,
      }),
    );
  });
});

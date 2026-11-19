/**
 * Printer reservations (#2785) show as bands in the reserved printer's lane,
 * labelled with the reservation, and only inside the visible window.
 */

import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueueTimelineView } from '../../components/QueueTimelineView';
import type { Printer, ReservationOccurrence } from '../../api/client';

const printers = [
  { id: 1, name: 'H2D', model: 'H2D' },
  { id: 2, name: 'X1C', model: 'X1C' },
] as unknown as Printer[];

const occ = (startOffsetH: number, endOffsetH: number, over: Partial<ReservationOccurrence> = {}) =>
  ({
    reservation_id: 1,
    label: 'Project X',
    printer_ids: [1],
    created_by_id: 3,
    created_by_username: 'alice',
    start: new Date(Date.now() + startOffsetH * 3600_000).toISOString(),
    end: new Date(Date.now() + endOffsetH * 3600_000).toISOString(),
    ...over,
  }) as ReservationOccurrence;

function renderTimeline(reservations: ReservationOccurrence[], onManage?: () => void) {
  render(
    <QueueTimelineView
      queueItems={[]}
      printers={printers}
      printerStatuses={{}}
      sjfEnabled={false}
      onItemClick={() => {}}
      reservations={reservations}
      onManageReservations={onManage}
      t={(key) => key}
    />,
  );
}

describe('QueueTimelineView reservations', () => {
  it('draws a labelled band for a reservation in view', () => {
    renderTimeline([occ(2, 6)]);
    const bands = screen.getAllByTestId('reservation-band');
    expect(bands).toHaveLength(1);
    expect(bands[0]).toHaveTextContent('Project X');
    expect(bands[0].getAttribute('title')).toContain('alice');
  });

  it('draws one band per reserved printer', () => {
    renderTimeline([occ(2, 6, { printer_ids: [1, 2] })]);
    expect(screen.getAllByTestId('reservation-band')).toHaveLength(2);
  });

  it('leaves out windows outside the visible 24 hours', () => {
    renderTimeline([occ(30, 40)]);
    expect(screen.queryByTestId('reservation-band')).toBeNull();
  });

  it('offers the manager when a handler is given', () => {
    const onManage = vi.fn();
    renderTimeline([], onManage);
    fireEvent.click(screen.getByText('queue.reservations.manage'));
    expect(onManage).toHaveBeenCalled();
  });
});

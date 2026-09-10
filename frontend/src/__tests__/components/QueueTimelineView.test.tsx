/**
 * The timeline draws each lane's pending jobs chained end to end, so the order
 * it chains them in is a claim about what the scheduler will dispatch next.
 *
 * It used to chain by queue position alone, which meant turning
 * Shortest-Job-First on changed the scheduler and the pending list but left
 * the timeline drawing the pre-SJF queue forever (#3043).
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueueTimelineView } from '../../components/QueueTimelineView';
import type { PrintQueueItem, Printer } from '../../api/client';

const HOUR = 3600;

const printers = [{ id: 1, name: 'Workshop X1C', model: 'X1C' }] as unknown as Printer[];

// Something has to be running for a lane to forecast at all: an idle printer's
// ASAP queue has no committed anchor to chain from, so the view drops it.
const running = {
  id: 100,
  printer_id: 1,
  status: 'printing',
  position: 0,
  archive_name: 'Running now',
  print_time_seconds: HOUR,
  started_at: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
} as unknown as PrintQueueItem;

const pending = (
  id: number,
  name: string,
  position: number,
  printTimeSeconds: number,
  over: Partial<PrintQueueItem> = {},
) =>
  ({
    id,
    printer_id: 1,
    status: 'pending',
    position,
    archive_name: name,
    print_time_seconds: printTimeSeconds,
    scheduled_time: null,
    manual_start: false,
    waiting_reason: null,
    ...over,
  }) as unknown as PrintQueueItem;

const queueItems = [
  running,
  pending(1, 'Long job', 1, 2 * HOUR),
  pending(2, 'Short job', 2, HOUR / 2),
  pending(3, 'Medium job', 3, HOUR),
];

function renderTimeline(sjfEnabled: boolean) {
  render(
    <QueueTimelineView
      queueItems={queueItems}
      printers={printers}
      printerStatuses={{ 1: { progress: 50, remaining_time: 30, state: 'RUNNING' } }}
      sjfEnabled={sjfEnabled}
      onItemClick={() => {}}
      t={(key: string) => key}
    />,
  );
}

/** Bar names, left to right. Bars are absolutely positioned, so the rendered
 *  offset is what the user reads -- not DOM order. The tooltip leads with the
 *  display name, which is cleaner to match on than the bar's own text (name
 *  and duration sit in adjacent divs with no separator between them). */
function barsLeftToRight(): string[] {
  return screen
    .getAllByRole('button')
    .filter(el => el.style.left.endsWith('%'))
    .sort((a, b) => parseFloat(a.style.left) - parseFloat(b.style.left))
    .map(el => (el.getAttribute('title') ?? '').split(' \u00b7 ')[0]);
}

describe('QueueTimelineView job ordering', () => {
  it('chains by queue position when SJF is off', () => {
    renderTimeline(false);
    expect(barsLeftToRight()).toEqual(['Running now', 'Long job', 'Short job', 'Medium job']);
  });

  it('chains shortest-first when SJF is on', () => {
    renderTimeline(true);
    expect(barsLeftToRight()).toEqual(['Running now', 'Short job', 'Medium job', 'Long job']);
  });

  it('keeps a jumped item ahead of a shorter one', () => {
    // The starvation guard is part of the scheduler's order too, so the
    // timeline has to draw it or it lies about the long job that is finally
    // about to run.
    render(
      <QueueTimelineView
        queueItems={[
          running,
          pending(1, 'Short job', 1, HOUR / 2),
          pending(2, 'Long job', 2, 2 * HOUR, { been_jumped: true }),
        ]}
        printers={printers}
        printerStatuses={{ 1: { progress: 50, remaining_time: 30, state: 'RUNNING' } }}
        sjfEnabled
        onItemClick={() => {}}
        t={(key: string) => key}
      />,
    );
    // Position order would put the short job first, so this only passes if
    // been_jumped is actually consulted.
    expect(barsLeftToRight()).toEqual(['Running now', 'Long job', 'Short job']);
  });
});

/**
 * A queued plate shows that plate's picture everywhere it is listed.
 *
 * The queue list asked for the plate thumbnail, but the timeline and the
 * compact history row asked for the file's, which is plate 1: two plates of
 * one file queued side by side looked like the same job.
 */

import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { QueueTimelineView } from '../../components/QueueTimelineView';
import { CompactHistoryRow } from '../../components/CompactHistoryRow';
import type { PrintQueueItem, Printer } from '../../api/client';

const printers = [{ id: 1, name: 'Workshop X1C', model: 'X1C' }] as unknown as Printer[];

const item = (over: Partial<PrintQueueItem>) =>
  ({
    id: 1,
    printer_id: 1,
    status: 'printing',
    position: 0,
    archive_name: 'Two plates',
    print_time_seconds: 3600,
    started_at: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
    scheduled_time: null,
    manual_start: false,
    waiting_reason: null,
    plate_id: 4,
    ...over,
  }) as unknown as PrintQueueItem;

const fromArchive = { archive_id: 7, archive_thumbnail: 'thumb.png' };
const fromLibrary = { archive_id: null, archive_thumbnail: null, library_file_id: 9, library_file_thumbnail: 'thumb.png' };

function timelineImages(queued: PrintQueueItem): string[] {
  const { container, unmount } = render(
    <QueueTimelineView
      queueItems={[queued]}
      printers={printers}
      printerStatuses={{ 1: { progress: 50, remaining_time: 30, state: 'RUNNING' } }}
      sjfEnabled={false}
      onItemClick={() => {}}
      t={(key: string) => key}
    />,
  );
  const srcs = Array.from(container.querySelectorAll('img')).map((img) => img.getAttribute('src') ?? '');
  unmount();
  return srcs;
}

function historyImages(done: PrintQueueItem): string[] {
  const { container, unmount } = render(
    <CompactHistoryRow
      item={done}
      onRequeue={() => {}}
      onRemove={() => {}}
      hasPermission={() => true}
      canModify={() => true}
      t={(key: string) => key}
    />,
  );
  const srcs = Array.from(container.querySelectorAll('img')).map((img) => img.getAttribute('src') ?? '');
  unmount();
  return srcs;
}

describe('queued plate thumbnail', () => {
  it.each([
    ['an archive', fromArchive, '/archives/7/plate-thumbnail/4'],
    ['a library file', fromLibrary, '/library/files/9/plate-thumbnail/4'],
  ])('the timeline shows the plate of %s', (_, source, url) => {
    const srcs = timelineImages(item(source));
    expect(srcs.length).toBeGreaterThan(0);
    for (const src of srcs) expect(src).toContain(url);
  });

  it.each([
    ['an archive', fromArchive, '/archives/7/plate-thumbnail/4'],
    ['a library file', fromLibrary, '/library/files/9/plate-thumbnail/4'],
  ])('a history row shows the plate of %s', (_, source, url) => {
    const srcs = historyImages(item({ ...source, status: 'completed', completed_at: new Date().toISOString() }));
    expect(srcs.length).toBeGreaterThan(0);
    for (const src of srcs) expect(src).toContain(url);
  });

  it('a single-plate job keeps the file thumbnail', () => {
    for (const src of timelineImages(item({ ...fromArchive, plate_id: null }))) {
      expect(src).toContain('/archives/7/thumbnail');
    }
  });
});

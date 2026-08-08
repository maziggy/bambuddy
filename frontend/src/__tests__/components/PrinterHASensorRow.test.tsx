/**
 * The Home Assistant sensor row on the printer card (#1148, #448).
 *
 * The reporter's whole ask is a glance-and-know row: "is the enclosure shut?"
 * So the assertions are about what the pill actually says. "on" is not an
 * answer to that question — "Open" is, and only Home Assistant's device_class
 * tells us which word to use.
 */

import { screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { PrinterHASensorRow } from '../../components/PrinterHASensorRow';
import { api } from '../../api/client';
import { render } from '../utils';

vi.mock('../../api/client', async () => {
  const actual = await vi.importActual<typeof import('../../api/client')>('../../api/client');
  return { ...actual, api: { ...actual.api, getHASensorReadings: vi.fn() } };
});

const getReadings = vi.mocked(api.getHASensorReadings);

function reading(overrides = {}) {
  return {
    id: 1,
    name: 'Enclosure Door',
    entity_id: 'binary_sensor.enclosure_door',
    kind: 'binary' as const,
    device_class: 'door',
    unit: null,
    state: 'off',
    value: null,
    alerting: false,
    block_print: false,
    reachable: true,
    last_changed: null,
    ...overrides,
  };
}

describe('PrinterHASensorRow', () => {
  beforeEach(() => {
    getReadings.mockReset();
  });

  it('renders nothing when the printer has no sensors', async () => {
    getReadings.mockResolvedValue([]);

    render(<PrinterHASensorRow printerId={4} />);

    await waitFor(() => expect(getReadings).toHaveBeenCalledWith(4));
    // No label, no divider, no empty-state placeholder — the row must not
    // reserve space on every card that has no sensors configured.
    expect(screen.queryByText('Sensors')).not.toBeInTheDocument();
  });

  it('names a door state by its device class, not by on/off', async () => {
    getReadings.mockResolvedValue([reading({ state: 'off' })]);

    render(<PrinterHASensorRow printerId={4} />);

    expect(await screen.findByText('Closed')).toBeInTheDocument();
    expect(screen.getByText('Enclosure Door')).toBeInTheDocument();
  });

  it('says Open for the same sensor in the other state', async () => {
    getReadings.mockResolvedValue([reading({ state: 'on', alerting: true })]);

    render(<PrinterHASensorRow printerId={4} />);

    expect(await screen.findByText('Open')).toBeInTheDocument();
  });

  it('falls back to on/off for a class it has no wording for', async () => {
    getReadings.mockResolvedValue([reading({ device_class: null, state: 'on' })]);

    render(<PrinterHASensorRow printerId={4} />);

    expect(await screen.findByText('On')).toBeInTheDocument();
  });

  it('shows a numeric reading with its unit', async () => {
    getReadings.mockResolvedValue([
      reading({
        kind: 'numeric',
        device_class: 'temperature',
        entity_id: 'sensor.enclosure_temp',
        name: 'Enclosure Temp',
        unit: '°C',
        state: '41.2',
        value: 41.2,
      }),
    ]);

    render(<PrinterHASensorRow printerId={4} />);

    expect(await screen.findByText('41.2 °C')).toBeInTheDocument();
  });

  it('reports an unreachable sensor as unavailable rather than as a state', async () => {
    // The pill must not read "Closed" for a door contact that dropped off the
    // network — that is the one wrong answer with real consequences.
    getReadings.mockResolvedValue([reading({ state: null, reachable: false })]);

    render(<PrinterHASensorRow printerId={4} />);

    expect(await screen.findByText('Unavailable')).toBeInTheDocument();
    expect(screen.queryByText('Closed')).not.toBeInTheDocument();
  });

  it('marks an alerting sensor for the eye', async () => {
    getReadings.mockResolvedValue([reading({ state: 'on', alerting: true })]);

    render(<PrinterHASensorRow printerId={4} />);

    const pill = (await screen.findByText('Open')).closest('span[title]');
    expect(pill?.className).toContain('red');
  });

  it('does not colour a quiet sensor as an alert', async () => {
    getReadings.mockResolvedValue([reading({ state: 'on', alerting: false })]);

    render(<PrinterHASensorRow printerId={4} />);

    const pill = (await screen.findByText('Open')).closest('span[title]');
    expect(pill?.className).not.toContain('red');
  });

  it('says so in the tooltip when a sensor holds prints', async () => {
    getReadings.mockResolvedValue([reading({ block_print: true })]);

    render(<PrinterHASensorRow printerId={4} />);

    await screen.findByText('Closed');
    expect(
      screen.getByTitle('binary_sensor.enclosure_door — holds prints while alerting')
    ).toBeInTheDocument();
  });

  it('lists every sensor bound to the printer', async () => {
    getReadings.mockResolvedValue([
      reading(),
      reading({
        id: 2,
        kind: 'numeric',
        name: 'Enclosure Temp',
        entity_id: 'sensor.enclosure_temp',
        device_class: 'temperature',
        unit: '°C',
        state: '22',
        value: 22,
      }),
    ]);

    render(<PrinterHASensorRow printerId={4} />);

    expect(await screen.findByText('Enclosure Door')).toBeInTheDocument();
    expect(screen.getByText('Enclosure Temp')).toBeInTheDocument();
  });
});

import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '../../api/client';
import { LocationSensorOptionsModal } from '../../components/LocationSensorOptionsModal';
import { render } from '../utils';
import {
  defaultLocationSensorDefaults,
  serializeLocationSensorAlertDefaults,
} from '../../utils/locationSensorDefaults';

vi.mock('../../api/client', async () => {
  const actual = await vi.importActual<typeof import('../../api/client')>('../../api/client');
  return {
    ...actual,
    api: {
      ...actual.api,
      getLocationHASensors: vi.fn(),
      getBindableLocationHAEntities: vi.fn(),
      updateLocationHASensor: vi.fn(),
      getSettings: vi.fn(),
      updateSettings: vi.fn(),
    },
  };
});

const getLocationSensors = vi.mocked(api.getLocationHASensors);
const getEntities = vi.mocked(api.getBindableLocationHAEntities);
const updateSensor = vi.mocked(api.updateLocationHASensor);
const getSettings = vi.mocked(api.getSettings);
const updateSettings = vi.mocked(api.updateSettings);

describe('LocationSensorOptionsModal', () => {
  beforeEach(() => {
    vi.mocked(window.localStorage.getItem).mockReset();
    vi.mocked(window.localStorage.setItem).mockReset();
    getLocationSensors.mockReset();
    getEntities.mockReset();
    updateSensor.mockReset();
    getSettings.mockReset();
    updateSettings.mockReset();
    getEntities.mockResolvedValue([]);
    getSettings.mockResolvedValue({ location_sensor_poll_interval: 120 } as never);
    updateSettings.mockResolvedValue({} as never);
  });

  it('shows a section for each of the three auto-add categories', async () => {
    render(<LocationSensorOptionsModal onClose={() => {}} />);

    expect(await screen.findByText('Temperature')).toBeInTheDocument();
    expect(screen.getByText('Humidity')).toBeInTheDocument();
    expect(screen.getByText('Battery')).toBeInTheDocument();
  });

  // The alert thresholds are server-backed (#2824 review round 4): they seed
  // the rule written onto each sensor row, so they must not differ per browser
  // and have to survive a backup/restore. Only the show-on-card default is
  // still local, because show_on_card is decided per sensor.
  it('saves the entered alert defaults to the server and closes', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<LocationSensorOptionsModal onClose={onClose} />);

    const aboveInputs = screen.getAllByText('Above °C');
    expect(aboveInputs.length).toBeGreaterThan(0);

    const inputs = screen.getAllByRole('spinbutton');
    await user.clear(inputs[0]);
    await user.type(inputs[0], '35');

    await user.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() =>
      expect(updateSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          location_sensor_alert_defaults: expect.stringContaining('"alertAbove":"35"'),
        })
      )
    );
    expect(onClose).toHaveBeenCalled();
  });

  it('keeps the show-on-card default in localStorage, without the alert fields', async () => {
    const user = userEvent.setup();
    render(<LocationSensorOptionsModal onClose={() => {}} />);

    await screen.findByText('Battery');
    await user.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() =>
      expect(window.localStorage.setItem).toHaveBeenCalledWith(
        'bambuddy-location-sensor-show-on-card-defaults',
        expect.stringContaining('"temperature":true')
      )
    );
    const written = vi
      .mocked(window.localStorage.setItem)
      .mock.calls.find((call) => call[0] === 'bambuddy-location-sensor-show-on-card-defaults')?.[1];
    expect(written).not.toContain('alertAbove');
    expect(written).not.toContain('notifyOnAlert');
  });

  it('does not offer an "above" threshold for the battery section', async () => {
    render(<LocationSensorOptionsModal onClose={() => {}} />);

    await screen.findByText('Battery');

    expect(screen.getAllByText(/^Above (°C|%)$/)).toHaveLength(2);
    expect(screen.getAllByText(/^Below (°C|%)$/)).toHaveLength(3);
    expect(screen.getAllByRole('spinbutton')).toHaveLength(6);
  });

  it('clears a stale saved "above" value for battery on save', async () => {
    getSettings.mockResolvedValue({
      location_sensor_poll_interval: 120,
      location_sensor_alert_defaults: JSON.stringify({
        temperature: { alertAbove: '', alertBelow: '', notifyOnAlert: false },
        humidity: { alertAbove: '', alertBelow: '', notifyOnAlert: false },
        battery: { alertAbove: '95', alertBelow: '15', notifyOnAlert: true },
      }),
    } as never);
    const user = userEvent.setup();
    render(<LocationSensorOptionsModal onClose={() => {}} />);

    await screen.findByText('Battery');
    await user.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() =>
      expect(updateSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          location_sensor_alert_defaults: expect.stringContaining('"battery":{"alertAbove":"","alertBelow":"15"'),
        })
      )
    );
  });

  it('saves the chosen above/below/optimal alert colors', async () => {
    const user = userEvent.setup();
    render(<LocationSensorOptionsModal onClose={() => {}} />);

    await screen.findByText('Battery');

    await user.selectOptions(screen.getByLabelText(/above threshold color/i), 'orange');
    await user.selectOptions(screen.getByLabelText(/below threshold color/i), 'purple');
    await user.selectOptions(screen.getByLabelText(/optimal value color/i), 'blue');
    await user.click(screen.getByRole('button', { name: /save/i }));

    expect(window.localStorage.setItem).toHaveBeenCalledWith('bambuddy-location-sensor-alert-above-color', 'orange');
    expect(window.localStorage.setItem).toHaveBeenCalledWith('bambuddy-location-sensor-alert-below-color', 'purple');
    expect(window.localStorage.setItem).toHaveBeenCalledWith('bambuddy-location-sensor-alert-optimal-color', 'blue');
  });

  it('loads the current poll interval and saves a changed value', async () => {
    getSettings.mockResolvedValue({ location_sensor_poll_interval: 300 } as never);
    const user = userEvent.setup();
    render(<LocationSensorOptionsModal onClose={() => {}} />);

    const input = await screen.findByLabelText(/update interval/i);
    await waitFor(() => expect(input).toHaveValue(300));

    await user.clear(input);
    await user.type(input, '90');
    await user.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() =>
      expect(updateSettings).toHaveBeenCalledWith(expect.objectContaining({ location_sensor_poll_interval: 90 }))
    );
  });

  // Both server-backed fields are seeded into local state by an effect, so a
  // settings response landing after the user has started typing used to
  // overwrite the edit — a cleared-and-retyped threshold came out as "3035".
  // Covers the interval too, which had the same shape before this guard.
  it('does not overwrite an in-progress edit when the settings response lands late', async () => {
    let resolveSettings: (value: unknown) => void = () => {};
    getSettings.mockReturnValue(
      new Promise((resolve) => {
        resolveSettings = resolve;
      }) as never
    );

    const user = userEvent.setup();
    render(<LocationSensorOptionsModal onClose={() => {}} />);

    // Form is up on the built-ins; edit before the server has answered.
    const inputs = screen.getAllByRole('spinbutton');
    await user.clear(inputs[0]);
    await user.type(inputs[0], '35');

    // Resolve and let the query actually propagate before asserting. A bare
    // waitFor would pass on its first tick — before the response reaches the
    // effect — and so would succeed even with the guard removed.
    await act(async () => {
      resolveSettings({
        location_sensor_poll_interval: 900,
        location_sensor_alert_defaults: JSON.stringify({
          temperature: { alertAbove: '30', alertBelow: '20', notifyOnAlert: false },
        }),
      });
      await new Promise((resolve) => setTimeout(resolve, 50));
    });

    // The late response must not put the server's 30 back over the typed 35.
    expect(screen.getAllByRole('spinbutton')[0]).toHaveValue(35);
  });

  it('still seeds the fields the user did not touch when the settings response lands late', async () => {
    // The other half of the guard above. Skipping the seed outright because a
    // keystroke beat the response would leave every untouched field on the
    // built-ins, and Save would then write those over the server's values for
    // fields the user never saw. "Seeded" and "touched" are tracked apart so
    // the seed still lands everywhere the user has not typed.
    let resolveSettings: (value: unknown) => void = () => {};
    getSettings.mockReturnValue(
      new Promise((resolve) => {
        resolveSettings = resolve;
      }) as never
    );

    const user = userEvent.setup();
    render(<LocationSensorOptionsModal onClose={() => {}} />);

    const inputs = screen.getAllByRole('spinbutton');
    await user.clear(inputs[0]);
    await user.type(inputs[0], '35');

    await act(async () => {
      resolveSettings({
        location_sensor_poll_interval: 900,
        location_sensor_alert_defaults: JSON.stringify({
          humidity: { alertAbove: '55', alertBelow: '25', notifyOnAlert: true },
        }),
      });
      await new Promise((resolve) => setTimeout(resolve, 50));
    });

    await user.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(updateSettings).toHaveBeenCalled());
    const patch = updateSettings.mock.calls[0][0] as Record<string, unknown>;
    // Seeded, so it matches the server and is left out of the patch entirely.
    // Unseeded it would still read 120 and be sent as a change nobody made.
    expect(patch.location_sensor_poll_interval).toBeUndefined();
    // The typed value survived...
    expect(patch.location_sensor_alert_defaults).toContain('"alertAbove":"35"');
    // ...and the category never touched kept the server's 55, not the built-in 30.
    expect(patch.location_sensor_alert_defaults).toContain('"alertAbove":"55"');
  });

  it('does not call updateSettings when nothing on the server side changed', async () => {
    // Both server-backed fields already match what the form would submit, so
    // an untouched Save must not issue a PATCH that needs admin rights.
    getSettings.mockResolvedValue({
      location_sensor_poll_interval: 120,
      location_sensor_alert_defaults: serializeLocationSensorAlertDefaults(defaultLocationSensorDefaults()),
    } as never);
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<LocationSensorOptionsModal onClose={onClose} />);

    await screen.findByLabelText(/update interval/i);
    await user.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(updateSettings).not.toHaveBeenCalled();
  });

  it('shows an error and keeps the modal open when saving a changed interval fails', async () => {
    updateSettings.mockRejectedValue(new Error('Forbidden'));
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<LocationSensorOptionsModal onClose={onClose} />);

    const input = await screen.findByLabelText(/update interval/i);
    await user.clear(input);
    await user.type(input, '90');
    await user.click(screen.getByRole('button', { name: /save/i }));

    expect(await screen.findByText('Forbidden')).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    // The server call happens before the localStorage writes, so a failed
    // PATCH must leave every local preference untouched — the error toast
    // says nothing was saved, and that has to stay true.
    const writtenKeys = vi.mocked(window.localStorage.setItem).mock.calls.map((call) => call[0]);
    expect(writtenKeys).not.toContain('bambuddy-location-sensor-show-on-card-defaults');
    expect(writtenKeys).not.toContain('bambuddy-location-sensor-colorize-values');
    expect(writtenKeys).not.toContain('bambuddy-location-sensor-alert-above-color');
    expect(writtenKeys).not.toContain('bambuddy-location-sensor-alert-below-color');
    expect(writtenKeys).not.toContain('bambuddy-location-sensor-alert-optimal-color');
  });

  it('clamps a poll interval below the 60s minimum on blur', async () => {
    const user = userEvent.setup();
    render(<LocationSensorOptionsModal onClose={() => {}} />);

    const input = await screen.findByLabelText(/update interval/i);
    await waitFor(() => expect(input).toHaveValue(120));

    await user.clear(input);
    await user.type(input, '10');
    await user.tab();

    expect(input).toHaveValue(60);
  });

  it('disables the color pickers when colorizing is turned off', async () => {
    const user = userEvent.setup();
    render(<LocationSensorOptionsModal onClose={() => {}} />);

    await screen.findByText('Battery');

    await user.click(screen.getByLabelText(/colorize sensor values/i));

    expect(screen.getByLabelText(/above threshold color/i)).toBeDisabled();
    expect(screen.getByLabelText(/below threshold color/i)).toBeDisabled();
    expect(screen.getByLabelText(/optimal value color/i)).toBeDisabled();
  });

  it('asks for confirmation before overwriting existing sensors, then applies the configured values', async () => {
    getLocationSensors.mockResolvedValue([
      { id: 1, device_class: 'temperature', entity_id: 'sensor.drybox_1_temperature' } as never,
      { id: 2, device_class: 'humidity', entity_id: 'sensor.drybox_1_humidity' } as never,
      { id: 3, device_class: 'battery', entity_id: 'sensor.drybox_1_battery' } as never,
      { id: 4, device_class: 'door', entity_id: 'binary_sensor.drybox_1_door' } as never,
    ]);
    getEntities.mockResolvedValue([
      { entity_id: 'sensor.drybox_1_temperature', friendly_name: 'Drybox 1 Temperature' } as never,
      { entity_id: 'sensor.drybox_1_battery', friendly_name: 'Drybox 1 Battery' } as never,
    ]);
    updateSensor.mockResolvedValue({} as never);
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<LocationSensorOptionsModal onClose={onClose} />);

    await screen.findByText('Battery');
    const inputs = screen.getAllByRole('spinbutton');
    await user.clear(inputs[0]);
    await user.type(inputs[0], '35');

    await user.click(screen.getByRole('button', { name: /^reset$/i }));
    expect(updateSensor).not.toHaveBeenCalled();
    expect(await screen.findByText(/cannot be undone/i)).toBeInTheDocument();

    await user.click(screen.getAllByRole('button', { name: /^reset$/i })[1]);

    await waitFor(() => expect(updateSensor).toHaveBeenCalledTimes(3));
    expect(updateSensor).toHaveBeenCalledWith(1, expect.objectContaining({ alert_above: 35, name: 'Drybox 1 Temperature' }));
    expect(updateSensor).toHaveBeenCalledWith(3, expect.objectContaining({ alert_above: null, name: 'Drybox 1 Battery' }));
    // Sensor 2's entity isn't in the Home Assistant list (e.g. currently
    // unreachable) — its name is left untouched rather than cleared.
    expect(updateSensor).toHaveBeenCalledWith(2, expect.not.objectContaining({ name: expect.anything() }));
    expect(onClose).toHaveBeenCalled();

    expect(updateSettings).toHaveBeenCalledWith(
      expect.objectContaining({
        location_sensor_alert_defaults: expect.stringContaining('"alertAbove":"35"'),
      })
    );
  });

  it('saves nothing when the reset fails part-way', async () => {
    // Reset used to save the options first and rewrite the sensors after, so a
    // rejected sensor PATCH left the settings and the six local preferences
    // saved behind an error toast that said nothing had been. Sensors first,
    // options after: a failure now means the toast is true.
    getLocationSensors.mockResolvedValue([{ id: 1, device_class: 'temperature' } as never]);
    getEntities.mockResolvedValue([]);
    updateSensor.mockRejectedValue(new Error('Forbidden'));
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<LocationSensorOptionsModal onClose={onClose} />);

    await screen.findByText('Battery');
    const inputs = screen.getAllByRole('spinbutton');
    await user.clear(inputs[0]);
    await user.type(inputs[0], '35');

    await user.click(screen.getByRole('button', { name: /^reset$/i }));
    await screen.findByText(/cannot be undone/i);
    await user.click(screen.getAllByRole('button', { name: /^reset$/i })[1]);

    await waitFor(() => expect(updateSensor).toHaveBeenCalled());
    expect(updateSettings).not.toHaveBeenCalled();
    // The render itself writes unrelated keys (theme), so scope this to the
    // preferences Save owns.
    const written = vi.mocked(window.localStorage.setItem).mock.calls.map(([key]) => key);
    expect(written.filter((key) => String(key).startsWith('bambuddy-location-sensor'))).toEqual([]);
    expect(onClose).not.toHaveBeenCalled();
  });

  it('does not overwrite anything when the reset confirmation is cancelled', async () => {
    getLocationSensors.mockResolvedValue([{ id: 1, device_class: 'temperature' } as never]);
    const user = userEvent.setup();
    render(<LocationSensorOptionsModal onClose={() => {}} />);

    await screen.findByText('Battery');
    await user.click(screen.getByRole('button', { name: /^reset$/i }));
    await screen.findByText(/cannot be undone/i);

    const cancelButtons = screen.getAllByRole('button', { name: /cancel/i });
    await user.click(cancelButtons[cancelButtons.length - 1]);

    expect(updateSensor).not.toHaveBeenCalled();
  });

  it('dismissing the reset confirm by clicking its own overlay does not close the Options dialog', async () => {
    // Regression: ConfirmModal used to render inside the Options overlay's
    // onClick=onClose div. ConfirmModal's own overlay doesn't stop
    // propagation, so a click meant only to dismiss it bubbled up and closed
    // Options too, dropping whatever the user had already changed.
    getLocationSensors.mockResolvedValue([{ id: 1, device_class: 'temperature' } as never]);
    const onClose = vi.fn();
    const user = userEvent.setup();
    const { container } = render(<LocationSensorOptionsModal onClose={onClose} />);

    await screen.findByText('Battery');
    await user.click(screen.getByRole('button', { name: /^reset$/i }));
    await screen.findByText(/cannot be undone/i);

    const overlays = container.querySelectorAll('.fixed.inset-0');
    expect(overlays.length).toBe(2);
    const confirmOverlay = overlays[overlays.length - 1];
    await user.click(confirmOverlay);

    expect(screen.queryByText(/cannot be undone/i)).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('does not persist anything when cancelled', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<LocationSensorOptionsModal onClose={onClose} />);

    await user.click(screen.getByRole('button', { name: /cancel/i }));

    expect(window.localStorage.setItem).not.toHaveBeenCalledWith(
      'bambuddy-location-sensor-auto-add-defaults',
      expect.anything()
    );
    expect(onClose).toHaveBeenCalled();
  });
});

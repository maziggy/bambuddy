/**
 * AddSmartPlugModal wiring: the create / update payloads per plug type,
 * the printer link, and the Tasmota discovery scan requests.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { AddSmartPlugModal } from '../../components/AddSmartPlugModal';
import type { SmartPlug } from '../../api/client';

type Sent = { method: string; path: string; body: Record<string, unknown> | null };

const PRINTERS = [
  { id: 3, name: 'Wiring Printer A', model: 'X1C', serial_number: 'SN-TEST-A' },
  { id: 4, name: 'Wiring Printer B', model: 'P1S', serial_number: 'SN-TEST-B' },
];

let sent: Sent[];

async function record(request: Request) {
  let body: Record<string, unknown> | null = null;
  try {
    body = (await request.json()) as Record<string, unknown>;
  } catch {
    body = null;
  }
  sent.push({ method: request.method, path: new URL(request.url).pathname, body });
}

const writes = () => sent.filter((s) => s.method !== 'GET');

beforeEach(() => {
  sent = [];
  server.use(
    http.get('*/api/v1/printers/', () => HttpResponse.json(PRINTERS)),
    http.get('*/api/v1/smart-plugs/', () => HttpResponse.json([])),
    http.get('*/api/v1/settings/', () =>
      HttpResponse.json({
        ha_enabled: true,
        ha_url: 'http://ha.example',
        ha_token: 'test-token-1',
        mqtt_broker: 'mqtt.example',
      }),
    ),
    http.get('*/api/v1/smart-plugs/ha/entities', () =>
      HttpResponse.json([
        { entity_id: 'switch.wiring_plug', friendly_name: 'Wiring Switch', state: 'off', domain: 'switch' },
      ]),
    ),
    http.get('*/api/v1/smart-plugs/ha/sensors', () => HttpResponse.json([])),
    http.post('*/api/v1/smart-plugs/', async ({ request }) => {
      await record(request);
      return HttpResponse.json({ id: 99 });
    }),
    http.patch('*/api/v1/smart-plugs/:id', async ({ request }) => {
      await record(request);
      return HttpResponse.json({ id: 5 });
    }),
  );
});

function nameInput() {
  return screen.getByPlaceholderText('Living Room Plug');
}

describe('AddSmartPlugModal wiring: create', () => {
  it('Tasmota: sends ip, type, printer_id and the default automation flags, no other type fields', async () => {
    const user = userEvent.setup();
    render(<AddSmartPlugModal onClose={() => undefined} />);

    await user.type(screen.getByPlaceholderText('192.168.1.100'), '192.0.2.20');
    await user.type(nameInput(), 'Desk Plug');
    const printerSelect = await screen.findByDisplayValue('No printer (manual control only)');
    await screen.findByRole('option', { name: 'Wiring Printer B' });
    await user.selectOptions(printerSelect, '4');
    await user.click(screen.getByRole('button', { name: 'Add' }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    const req = writes()[0];
    expect(req.method).toBe('POST');
    expect(req.path).toBe('/api/v1/smart-plugs/');
    expect(req.body).toMatchObject({
      name: 'Desk Plug',
      plug_type: 'tasmota',
      ip_address: '192.0.2.20',
      printer_id: 4,
      controls_printer_power: true,
      ha_entity_id: null,
      mqtt_power_topic: null,
      rest_on_url: null,
      username: null,
      password: null,
    });
  });

  it('Tasmota: unlinked sends printer_id null', async () => {
    const user = userEvent.setup();
    render(<AddSmartPlugModal onClose={() => undefined} />);

    await user.type(screen.getByPlaceholderText('192.168.1.100'), '192.0.2.21');
    await user.type(nameInput(), 'Loose Plug');
    await user.click(screen.getByRole('button', { name: 'Add' }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0].body).toMatchObject({ plug_type: 'tasmota', printer_id: null });
  });

  it('Tasmota without an IP sends nothing', async () => {
    const user = userEvent.setup();
    render(<AddSmartPlugModal onClose={() => undefined} />);

    await user.type(nameInput(), 'No IP');
    await user.click(screen.getByRole('button', { name: 'Add' }));

    expect(await screen.findByText(/IP address is required/i)).toBeInTheDocument();
    expect(writes()).toEqual([]);
  });

  it('Home Assistant: sends the picked entity and no Tasmota/MQTT/REST fields', async () => {
    const user = userEvent.setup();
    render(<AddSmartPlugModal onClose={() => undefined} />);

    await user.click(screen.getByRole('button', { name: 'HA' }));
    const search = await screen.findByPlaceholderText(/search/i);
    await user.click(search);
    await user.click(await screen.findByText('Wiring Switch'));
    await user.click(screen.getByRole('button', { name: 'Add' }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0].body).toMatchObject({
      name: 'Wiring Switch',
      plug_type: 'homeassistant',
      ha_entity_id: 'switch.wiring_plug',
      ip_address: null,
      username: null,
      password: null,
      mqtt_power_topic: null,
      rest_on_url: null,
      printer_id: null,
    });
  });

  it('switching from HA to Tasmota drops the picked HA entity from the payload', async () => {
    const user = userEvent.setup();
    render(<AddSmartPlugModal onClose={() => undefined} />);

    await user.click(screen.getByRole('button', { name: 'HA' }));
    await user.click(await screen.findByPlaceholderText(/search/i));
    await user.click(await screen.findByText('Wiring Switch'));
    await user.click(screen.getByRole('button', { name: 'Tasmota' }));
    await user.type(screen.getByPlaceholderText('192.168.1.100'), '192.0.2.22');
    await user.click(screen.getByRole('button', { name: 'Add' }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0].body).toMatchObject({
      plug_type: 'tasmota',
      ip_address: '192.0.2.22',
      ha_entity_id: null,
      ha_power_entity: null,
    });
  });

  it('MQTT: sends only the MQTT topic fields', async () => {
    const user = userEvent.setup();
    render(<AddSmartPlugModal onClose={() => undefined} />);

    await user.click(screen.getByRole('button', { name: 'MQTT' }));
    await user.type(await screen.findByPlaceholderText('zigbee2mqtt/shelly-working-room'), 'tele/wiring/SENSOR');
    await user.type(nameInput(), 'Meter');
    await user.click(screen.getByRole('button', { name: 'Add' }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0].body).toMatchObject({
      plug_type: 'mqtt',
      mqtt_power_topic: 'tele/wiring/SENSOR',
      mqtt_energy_topic: null,
      ip_address: null,
      ha_entity_id: null,
      rest_on_url: null,
    });
  });

  it('REST: sends the REST URLs and method only', async () => {
    const user = userEvent.setup();
    render(<AddSmartPlugModal onClose={() => undefined} />);

    await user.click(screen.getByRole('button', { name: 'REST' }));
    const [onUrl, offUrl] = await screen.findAllByPlaceholderText('http://openhab:8080/rest/items/MyPlug');
    await user.type(onUrl, 'http://plug.example/on');
    await user.type(offUrl, 'http://plug.example/off');
    await user.type(nameInput(), 'Rest Plug');
    await user.click(screen.getByRole('button', { name: 'Add' }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0].body).toMatchObject({
      plug_type: 'rest',
      rest_on_url: 'http://plug.example/on',
      rest_off_url: 'http://plug.example/off',
      rest_method: 'POST',
      ip_address: null,
      ha_entity_id: null,
      mqtt_power_topic: null,
      username: null,
      password: null,
    });
  });
});

describe('AddSmartPlugModal wiring: edit', () => {
  it('PATCHes the edited plug by id, keeping the saved-secret placeholder and the changed name', async () => {
    const user = userEvent.setup();
    const existing = {
      id: 5,
      name: 'Old Name',
      plug_type: 'tasmota',
      ip_address: '192.0.2.30',
      username: 'admin',
      password: '********',
      enabled: true,
      printer_id: 3,
      controls_printer_power: false,
    } as unknown as SmartPlug;
    render(<AddSmartPlugModal plug={existing} onClose={() => undefined} />);

    const name = await screen.findByDisplayValue('Old Name');
    await user.clear(name);
    await user.type(name, 'New Name');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    const req = writes()[0];
    expect(req.method).toBe('PATCH');
    expect(req.path).toBe('/api/v1/smart-plugs/5');
    expect(req.body).toMatchObject({
      name: 'New Name',
      ip_address: '192.0.2.30',
      username: 'admin',
      // The write-only placeholder goes back untouched; the backend keeps the stored secret.
      password: '********',
      printer_id: 3,
      controls_printer_power: false,
    });
  });
});

describe('AddSmartPlugModal wiring: Tasmota discovery', () => {
  it('starts and stops the scan, and picking a found device fills IP and name', async () => {
    server.use(
      http.post('*/api/v1/smart-plugs/discover/scan', async ({ request }) => {
        await record(request);
        return HttpResponse.json({ running: true, scanned: 0, total: 254 });
      }),
      http.get('*/api/v1/smart-plugs/discover/status', () =>
        HttpResponse.json({ running: true, scanned: 10, total: 254 }),
      ),
      http.get('*/api/v1/smart-plugs/discover/devices', () =>
        HttpResponse.json([{ ip_address: '192.0.2.55', name: 'Found Plug', state: 'ON' }]),
      ),
      http.post('*/api/v1/smart-plugs/discover/stop', async ({ request }) => {
        await record(request);
        return HttpResponse.json({ running: false, scanned: 10, total: 254 });
      }),
    );
    const user = userEvent.setup();
    render(<AddSmartPlugModal onClose={() => undefined} />);

    await user.click(screen.getByRole('button', { name: 'Discover Tasmota Devices' }));
    await waitFor(() => expect(writes().map((w) => w.path)).toEqual(['/api/v1/smart-plugs/discover/scan']));

    await user.click(await screen.findByText('Found Plug'));
    expect(screen.getByPlaceholderText('192.168.1.100')).toHaveValue('192.0.2.55');
    expect(nameInput()).toHaveValue('Found Plug');

    await user.click(screen.getByRole('button', { name: 'Stop Scanning' }));
    await waitFor(() =>
      expect(writes().map((w) => w.path)).toEqual([
        '/api/v1/smart-plugs/discover/scan',
        '/api/v1/smart-plugs/discover/stop',
      ]),
    );
  });
});

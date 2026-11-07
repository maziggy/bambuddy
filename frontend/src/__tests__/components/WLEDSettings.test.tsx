import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { useQueryClient } from '@tanstack/react-query';
import { WLEDSettings } from '../../components/WLEDSettings';
import { render } from '../utils';
import { server } from '../mocks/server';

const printer = {
  id: 1,
  name: 'X1C Workshop',
  serial_number: 'SERIAL1',
  ip_address: '192.168.1.10',
  model: 'X1C',
  location: null,
  nozzle_count: 1,
  supports_nozzle_flow_type: true,
  is_active: true,
  auto_archive: true,
  external_camera_url: null,
  external_camera_type: null,
  external_camera_enabled: false,
  external_camera_snapshot_url: null,
  camera_rotation: 0,
  plate_detection_enabled: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  wled_config: {
    enabled: false,
    base_url: 'http://wled.local',
    presets: {
      idle: 1,
      prepare: null,
      printing: 99,
      paused: null,
      finished: 9,
      error: null,
      queue_waiting: null,
      filament_problem: null,
      hms_error: null,
      offline: null,
    },
    finished_timeout_seconds: 120,
  },
};

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => { resolve = done; });
  return { promise, resolve };
}

const enabledPrinter = { ...printer, wled_config: { ...printer.wled_config, enabled: true } };
const secondPrinter = { ...enabledPrinter, id: 2, name: 'A1 Mini', wled_config: {
  ...enabledPrinter.wled_config, base_url: 'http://second.local',
} };

function RefetchableWLEDSettings() {
  const queryClient = useQueryClient();
  return <>
    <button onClick={() => void queryClient.refetchQueries({ queryKey: ['printers'] })}>Refetch printers</button>
    <WLEDSettings />
  </>;
}

describe('WLEDSettings', () => {
  it('ignores a late A result after B has loaded its presets', async () => {
    const oldA = deferred();
    const calls: number[] = [];
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([enabledPrinter, secondPrinter])),
      http.post('/api/v1/printers/:id/wled/presets', async ({ params }) => {
        const id = Number(params.id);
        calls.push(id);
        if (id === 1) await oldA.promise;
        return HttpResponse.json([{ id, name: id === 1 ? 'Old A' : 'Current B' }]);
      }),
    );
    render(<WLEDSettings />);
    await waitFor(() => expect(calls).toContain(1));
    await userEvent.selectOptions(screen.getByLabelText('Printer'), '2');
    await waitFor(() => expect(within(screen.getByLabelText('Printing')).getByRole('option', { name: 'Current B (2)' })).toBeInTheDocument());
    oldA.resolve();
    await new Promise((resolve) => setTimeout(resolve, 50));
    await waitFor(() => expect(within(screen.getByLabelText('Printing')).queryByRole('option', { name: 'Old A (1)' })).toBeNull());
    expect(within(screen.getByLabelText('Printing')).getByRole('option', { name: 'Current B (2)' })).toBeInTheDocument();
  });

  it('ignores an older A request after A to B to A and keeps the newest same-context result', async () => {
    const firstA = deferred();
    const calls: number[] = [];
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([enabledPrinter, secondPrinter])),
      http.post('/api/v1/printers/:id/wled/presets', async ({ params }) => {
        const id = Number(params.id);
        calls.push(id);
        const sequence = calls.length;
        if (id === 1 && sequence === 1) await firstA.promise;
        return HttpResponse.json([{ id: sequence, name: `Request ${sequence}` }]);
      }),
    );
    render(<WLEDSettings />);
    await waitFor(() => expect(calls).toEqual([1]));
    await userEvent.clear(screen.getByLabelText('Finished timeout (seconds)'));
    await userEvent.type(screen.getByLabelText('Finished timeout (seconds)'), '77');
    await userEvent.selectOptions(screen.getByLabelText('Printer'), '2');
    await waitFor(() => expect(calls).toEqual([1, 2]));
    await userEvent.selectOptions(screen.getByLabelText('Printer'), '1');
    await waitFor(() => expect(within(screen.getByLabelText('Printing')).getByRole('option', { name: 'Request 3 (3)' })).toBeInTheDocument());
    firstA.resolve();
    await new Promise((resolve) => setTimeout(resolve, 50));
    await waitFor(() => expect(within(screen.getByLabelText('Printing')).queryByRole('option', { name: 'Request 1 (1)' })).toBeNull());
    expect(within(screen.getByLabelText('Printing')).getByRole('option', { name: 'Request 3 (3)' })).toBeInTheDocument();
    expect(screen.getByLabelText('Finished timeout (seconds)')).toHaveValue(77);
  });

  it('ignores a late preset error after switching printer or URL', async () => {
    const oldRequest = deferred();
    let calls = 0;
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([enabledPrinter, secondPrinter])),
      http.post('/api/v1/printers/:id/wled/presets', async ({ params }) => {
        calls += 1;
        if (Number(params.id) === 1) {
          await oldRequest.promise;
          return HttpResponse.json({ detail: 'Old URL rejected' }, { status: 422 });
        }
        return HttpResponse.json([{ id: 2, name: 'Current B' }]);
      }),
    );
    render(<WLEDSettings />);
    await waitFor(() => expect(calls).toBe(1));
    await userEvent.selectOptions(screen.getByLabelText('Printer'), '2');
    await waitFor(() => expect(within(screen.getByLabelText('Printing')).getByRole('option', { name: 'Current B (2)' })).toBeInTheDocument());
    oldRequest.resolve();
    await new Promise((resolve) => setTimeout(resolve, 50));
    await waitFor(() => expect(screen.queryByText('Old URL rejected')).toBeNull());
    expect(screen.queryByText('WLED is unavailable. Saved preset IDs are kept.')).toBeNull();
  });

  it('ignores a response for the previous URL on the same printer', async () => {
    const oldRequest = deferred();
    let calls = 0;
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([enabledPrinter])),
      http.post('/api/v1/printers/1/wled/presets', async () => {
        calls += 1;
        if (calls === 1) await oldRequest.promise;
        return HttpResponse.json([{ id: calls, name: `Request ${calls}` }]);
      }),
    );
    render(<WLEDSettings />);
    await waitFor(() => expect(calls).toBe(1));
    await userEvent.clear(screen.getByLabelText('WLED URL'));
    await userEvent.type(screen.getByLabelText('WLED URL'), 'http://new.local');
    oldRequest.resolve();
    await new Promise((resolve) => setTimeout(resolve, 50));
    await waitFor(() => expect(within(screen.getByLabelText('Printing')).queryByRole('option', { name: 'Request 1 (1)' })).toBeNull());
    await userEvent.click(screen.getByRole('button', { name: 'Load presets' }));
    await waitFor(() => expect(within(screen.getByLabelText('Printing')).getByRole('option', { name: 'Request 2 (2)' })).toBeInTheDocument());
  });

  it('keeps the newest preset request for the same printer and URL', async () => {
    const oldRequest = deferred();
    let calls = 0;
    let printerReads = 0;
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([{
        ...enabledPrinter, name: `Printer read ${++printerReads}`,
      }])),
      http.post('/api/v1/printers/1/wled/presets', async () => {
        const sequence = ++calls;
        if (sequence === 1) await oldRequest.promise;
        return HttpResponse.json([{ id: sequence, name: `Request ${sequence}` }]);
      }),
    );
    render(<RefetchableWLEDSettings />);
    await waitFor(() => expect(calls).toBe(1));
    await userEvent.click(screen.getByRole('button', { name: 'Refetch printers' }));
    await waitFor(() => expect(within(screen.getByLabelText('Printing')).getByRole('option', { name: 'Request 2 (2)' })).toBeInTheDocument());
    oldRequest.resolve();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(within(screen.getByLabelText('Printing')).queryByRole('option', { name: 'Request 1 (1)' })).toBeNull();
  });

  it('does not apply a pending result after unmount', async () => {
    const pending = deferred();
    let started = false;
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([enabledPrinter])),
      http.post('/api/v1/printers/1/wled/presets', async () => {
        started = true;
        await pending.promise;
        return HttpResponse.json({ detail: 'Old request failed' }, { status: 422 });
      }),
    );
    const view = render(<WLEDSettings />);
    await waitFor(() => expect(started).toBe(true));
    view.unmount();
    pending.resolve();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByText('Old request failed')).toBeNull();
  });

  it('does not replace newer local edits when an older save completes', async () => {
    const pending = deferred();
    let started = false;
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([enabledPrinter])),
      http.post('/api/v1/printers/1/wled/presets', () => HttpResponse.json([])),
      http.patch('/api/v1/printers/1', async () => {
        started = true;
        await pending.promise;
        return HttpResponse.json(enabledPrinter);
      }),
    );
    render(<RefetchableWLEDSettings />);
    await screen.findByDisplayValue('http://wled.local');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(started).toBe(true));
    await userEvent.clear(screen.getByLabelText('WLED URL'));
    await userEvent.type(screen.getByLabelText('WLED URL'), 'http://new.local');
    pending.resolve();
    await new Promise((resolve) => setTimeout(resolve, 50));
    await userEvent.click(screen.getByRole('button', { name: 'Refetch printers' }));
    expect(screen.getByLabelText('WLED URL')).toHaveValue('http://new.local');
  });

  it('does not reload presets from the saved URL after a local URL edit and printer refetch', async () => {
    let printerReads = 0;
    let presetRequests = 0;
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([{
        ...enabledPrinter, name: `Printer read ${++printerReads}`,
      }])),
      http.post('/api/v1/printers/1/wled/presets', () => {
        presetRequests += 1;
        return HttpResponse.json([{ id: 3, name: 'Saved URL' }]);
      }),
    );
    render(<RefetchableWLEDSettings />);
    await screen.findByText('1 presets loaded');
    await userEvent.clear(screen.getByLabelText('WLED URL'));
    await userEvent.type(screen.getByLabelText('WLED URL'), 'http://new.local');
    await userEvent.click(screen.getByRole('button', { name: 'Refetch printers' }));
    await waitFor(() => expect(printerReads).toBe(2));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(presetRequests).toBe(1);
    expect(screen.getByLabelText('WLED URL')).toHaveValue('http://new.local');
  });

  const validationError = () => HttpResponse.json({
    detail: [{ msg: 'Value error, Invalid WLED URL' }],
  }, { status: 422 });

  it('shows the backend 422 message for Save without losing the input', async () => {
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([enabledPrinter])),
      http.post('/api/v1/printers/1/wled/presets', () => HttpResponse.json([])),
      http.patch('/api/v1/printers/1', validationError),
    );
    render(<WLEDSettings />);
    await screen.findByDisplayValue('http://wled.local');
    await userEvent.clear(screen.getByLabelText('WLED URL'));
    await userEvent.type(screen.getByLabelText('WLED URL'), 'http://invalid.local');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('Invalid WLED URL')).toBeInTheDocument();
    expect(screen.getByLabelText('WLED URL')).toHaveValue('http://invalid.local');
    expect(screen.queryByText('Could not save WLED settings')).toBeNull();
    expect(screen.queryByText('Unable to connect to WLED.')).toBeNull();
    expect(screen.queryByText('WLED is unavailable. Saved preset IDs are kept.')).toBeNull();
  });

  it('ignores a late Save 422 for an older draft snapshot', async () => {
    const pending = deferred();
    let sentTimeout: number | undefined;
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([enabledPrinter, secondPrinter])),
      http.post('/api/v1/printers/1/wled/presets', () => HttpResponse.json([])),
      http.patch('/api/v1/printers/1', async ({ request }) => {
        const body = await request.json() as { wled_config: typeof enabledPrinter.wled_config };
        sentTimeout = body.wled_config.finished_timeout_seconds;
        await pending.promise;
        return HttpResponse.json({ detail: 'Invalid finished timeout' }, { status: 422 });
      }),
    );
    render(<WLEDSettings />);
    await screen.findByDisplayValue('http://wled.local');
    await userEvent.clear(screen.getByLabelText('Finished timeout (seconds)'));
    await userEvent.type(screen.getByLabelText('Finished timeout (seconds)'), '10');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sentTimeout).toBe(10));
    await userEvent.clear(screen.getByLabelText('Finished timeout (seconds)'));
    await userEvent.type(screen.getByLabelText('Finished timeout (seconds)'), '20');
    pending.resolve();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled());
    expect(screen.queryByText('Invalid finished timeout')).toBeNull();
    expect(screen.queryByText('WLED settings saved')).toBeNull();
    await userEvent.selectOptions(screen.getByLabelText('Printer'), '2');
    await userEvent.selectOptions(screen.getByLabelText('Printer'), '1');
    expect(screen.getByLabelText('Finished timeout (seconds)')).toHaveValue(20);
  });

  it('shows a delayed Save 422 when the draft has not changed', async () => {
    const pending = deferred();
    let saveStarted = false;
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([enabledPrinter])),
      http.post('/api/v1/printers/1/wled/presets', () => HttpResponse.json([])),
      http.patch('/api/v1/printers/1', async () => {
        saveStarted = true;
        await pending.promise;
        return HttpResponse.json({ detail: 'Invalid finished timeout' }, { status: 422 });
      }),
    );
    render(<WLEDSettings />);
    await screen.findByDisplayValue('http://wled.local');
    await userEvent.clear(screen.getByLabelText('Finished timeout (seconds)'));
    await userEvent.type(screen.getByLabelText('Finished timeout (seconds)'), '10');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(saveStarted).toBe(true));
    pending.resolve();
    expect(await screen.findByText('Invalid finished timeout')).toBeInTheDocument();
    expect(screen.getByLabelText('Finished timeout (seconds)')).toHaveValue(10);
  });

  it('shows the backend 422 message for Load presets without marking WLED unavailable', async () => {
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([enabledPrinter])),
      http.post('/api/v1/printers/1/wled/presets', validationError),
    );
    render(<WLEDSettings />);
    expect(await screen.findByText('Invalid WLED URL')).toBeInTheDocument();
    expect(screen.queryByText('WLED is unavailable. Saved preset IDs are kept.')).toBeNull();
    expect(screen.getByLabelText('Printing')).toHaveValue('99');
    expect(screen.getByLabelText('WLED URL')).toHaveValue('http://wled.local');
  });

  it('shows the backend 422 message for Test connection without marking WLED unreachable', async () => {
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([enabledPrinter])),
      http.post('/api/v1/printers/1/wled/presets', () => HttpResponse.json([])),
      http.post('/api/v1/printers/1/wled/test-connection', validationError),
    );
    render(<WLEDSettings />);
    await screen.findByDisplayValue('http://wled.local');
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    expect(await screen.findByText('Invalid WLED URL')).toBeInTheDocument();
    expect(screen.getByText('Not tested')).toBeInTheDocument();
    expect(screen.queryByText('Unable to connect to WLED.')).toBeNull();
  });

  it('shows the backend 422 message for Test preset without a generic error', async () => {
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([enabledPrinter])),
      http.post('/api/v1/printers/1/wled/presets', () => HttpResponse.json([{ id: 99, name: 'Printing' }])),
      http.post('/api/v1/printers/1/wled/test-preset', validationError),
    );
    render(<WLEDSettings />);
    await screen.findByText('1 presets loaded');
    await userEvent.click(screen.getByRole('button', { name: 'Test Printing' }));
    expect(await screen.findByText('Invalid WLED URL')).toBeInTheDocument();
    expect(screen.queryByText('Could not activate preset')).toBeNull();
    expect(screen.getByLabelText('Printing')).toHaveValue('99');
    expect(screen.queryByText('Unable to connect to WLED.')).toBeNull();
    expect(screen.queryByText('WLED is unavailable. Saved preset IDs are kept.')).toBeNull();
  });

  it('ignores a late 422 from an old URL while showing the new URL', async () => {
    const pending = deferred();
    let calls = 0;
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([enabledPrinter])),
      http.post('/api/v1/printers/1/wled/presets', async () => {
        calls += 1;
        if (calls === 1) {
          await pending.promise;
          return validationError();
        }
        return HttpResponse.json([{ id: 2, name: 'New URL' }]);
      }),
    );
    render(<WLEDSettings />);
    await waitFor(() => expect(calls).toBe(1));
    await userEvent.clear(screen.getByLabelText('WLED URL'));
    await userEvent.type(screen.getByLabelText('WLED URL'), 'http://new.local');
    pending.resolve();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByText('Invalid WLED URL')).toBeNull();
    expect(screen.queryByText('WLED is unavailable. Saved preset IDs are kept.')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Load presets' }));
    await waitFor(() => expect(within(screen.getByLabelText('Printing')).getByRole('option', { name: 'New URL (2)' })).toBeInTheDocument());
  });

  it('keeps generic 502 errors for Save and Test preset', async () => {
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([enabledPrinter])),
      http.post('/api/v1/printers/1/wled/presets', () => HttpResponse.json([{ id: 99, name: 'Printing' }])),
      http.post('/api/v1/printers/1/wled/test-preset', () => HttpResponse.json({}, { status: 502 })),
      http.patch('/api/v1/printers/1', () => HttpResponse.json({}, { status: 502 })),
    );
    render(<WLEDSettings />);
    await screen.findByText('1 presets loaded');
    await userEvent.click(screen.getByRole('button', { name: 'Test Printing' }));
    expect(await screen.findByText('Could not activate preset')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('Could not save WLED settings')).toBeInTheDocument();
  });

  it('keeps unsaved edits when the printers query refetches', async () => {
    let requests = 0;
    server.use(
      http.get('/api/v1/printers/', () => {
        requests += 1;
        return HttpResponse.json([{
          ...printer,
          name: requests === 1 ? printer.name : 'Refetched printer',
          wled_config: { ...printer.wled_config, enabled: true },
        }]);
      }),
      http.post('/api/v1/printers/1/wled/presets', () =>
        HttpResponse.json([{ id: 3, name: 'Printing Cyan' }]),
      ),
    );
    render(<RefetchableWLEDSettings />);
    await screen.findByText('1 presets loaded');
    const url = screen.getByLabelText('WLED URL');
    await userEvent.clear(url);
    await userEvent.type(url, 'http://edited.local');
    await userEvent.selectOptions(screen.getByLabelText('Printing'), '3');
    await userEvent.clear(screen.getByLabelText('Finished timeout (seconds)'));
    await userEvent.type(screen.getByLabelText('Finished timeout (seconds)'), '45');
    await userEvent.click(screen.getByLabelText('Enable WLED integration'));
    await userEvent.click(screen.getByRole('button', { name: 'Refetch printers' }));
    await screen.findByRole('option', { name: 'Refetched printer' });

    expect(requests).toBe(2);
    expect(url).toHaveValue('http://edited.local');
    expect(screen.getByLabelText('Enable WLED integration')).not.toBeChecked();
    expect(screen.getByLabelText('Printing')).toHaveValue('3');
    expect(screen.getByLabelText('Finished timeout (seconds)')).toHaveValue(45);
  });

  it('loads each printers saved configuration once and keeps unsaved edits across switches', async () => {
    const second = {
      ...printer,
      id: 2,
      name: 'A1 Mini',
      wled_config: {
        ...printer.wled_config,
        enabled: true,
        base_url: 'http://second.local',
        presets: { ...printer.wled_config.presets, printing: 32 },
        finished_timeout_seconds: 60,
      },
    };
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([printer, second])),
      http.post('/api/v1/printers/2/wled/presets', () => HttpResponse.json([{ id: 32, name: 'Printing Cyan' }])),
    );
    render(<WLEDSettings />);
    await screen.findByDisplayValue('http://wled.local');
    await userEvent.click(screen.getByLabelText('Enable WLED integration'));
    await userEvent.clear(screen.getByLabelText('WLED URL'));
    await userEvent.type(screen.getByLabelText('WLED URL'), 'http://unsaved.local');
    await userEvent.selectOptions(screen.getByLabelText('Printer'), '2');
    await screen.findByDisplayValue('http://second.local');
    expect(screen.getByLabelText('Enable WLED integration')).toBeChecked();
    expect(screen.getByLabelText('Printing')).toHaveValue('32');
    expect(screen.getByLabelText('Finished timeout (seconds)')).toHaveValue(60);

    await userEvent.selectOptions(screen.getByLabelText('Printer'), '1');
    await screen.findByDisplayValue('http://unsaved.local');
    expect(screen.getByLabelText('Enable WLED integration')).toBeChecked();
    expect(screen.getByLabelText('Printing')).toHaveValue('99');
    expect(screen.getByLabelText('Finished timeout (seconds)')).toHaveValue(120);
  });

  it('keeps different unsaved drafts for A and B through repeated switches', async () => {
    server.use(http.get('/api/v1/printers/', () => HttpResponse.json([enabledPrinter, secondPrinter])));
    render(<WLEDSettings />);
    await screen.findByDisplayValue('http://wled.local');
    await userEvent.clear(screen.getByLabelText('Finished timeout (seconds)'));
    await userEvent.type(screen.getByLabelText('Finished timeout (seconds)'), '11');
    await userEvent.selectOptions(screen.getByLabelText('Printer'), '2');
    await screen.findByDisplayValue('http://second.local');
    await userEvent.clear(screen.getByLabelText('Finished timeout (seconds)'));
    await userEvent.type(screen.getByLabelText('Finished timeout (seconds)'), '22');
    for (let i = 0; i < 2; i += 1) {
      await userEvent.selectOptions(screen.getByLabelText('Printer'), '1');
      expect(screen.getByLabelText('Finished timeout (seconds)')).toHaveValue(11);
      await userEvent.selectOptions(screen.getByLabelText('Printer'), '2');
      expect(screen.getByLabelText('Finished timeout (seconds)')).toHaveValue(22);
    }
  });

  it('uses newer server values after a successful save clears the unchanged draft', async () => {
    let saved = enabledPrinter;
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([saved])),
      http.post('/api/v1/printers/1/wled/presets', () => HttpResponse.json([])),
      http.patch('/api/v1/printers/1', async ({ request }) => {
        const body = await request.json() as { wled_config: typeof enabledPrinter.wled_config };
        saved = { ...enabledPrinter, wled_config: body.wled_config };
        return HttpResponse.json(saved);
      }),
    );
    render(<RefetchableWLEDSettings />);
    await screen.findByDisplayValue('http://wled.local');
    await userEvent.clear(screen.getByLabelText('WLED URL'));
    await userEvent.type(screen.getByLabelText('WLED URL'), 'http://saved.local');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('WLED settings saved');
    saved = { ...saved, wled_config: { ...saved.wled_config, base_url: 'http://external.local' } };
    await userEvent.click(screen.getByRole('button', { name: 'Refetch printers' }));
    await waitFor(() => expect(screen.getByLabelText('WLED URL')).toHaveValue('http://external.local'));
  });

  it('keeps a newer A draft after the older save succeeds', async () => {
    const pending = deferred();
    let saved: typeof enabledPrinter | null = null;
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([saved ?? enabledPrinter, secondPrinter])),
      http.post('/api/v1/printers/:id/wled/presets', () => HttpResponse.json([])),
      http.patch('/api/v1/printers/1', async ({ request }) => {
        const body = await request.json() as { wled_config: typeof enabledPrinter.wled_config };
        await pending.promise;
        saved = { ...enabledPrinter, wled_config: body.wled_config };
        return HttpResponse.json(saved);
      }),
    );
    render(<WLEDSettings />);
    await screen.findByDisplayValue('http://wled.local');
    await userEvent.clear(screen.getByLabelText('WLED URL'));
    await userEvent.type(screen.getByLabelText('WLED URL'), 'http://version-one.local');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await userEvent.clear(screen.getByLabelText('WLED URL'));
    await userEvent.type(screen.getByLabelText('WLED URL'), 'http://version-two.local');
    pending.resolve();
    await waitFor(() => expect(saved?.wled_config.base_url).toBe('http://version-one.local'));
    await userEvent.selectOptions(screen.getByLabelText('Printer'), '2');
    await userEvent.selectOptions(screen.getByLabelText('Printer'), '1');
    expect(screen.getByLabelText('WLED URL')).toHaveValue('http://version-two.local');
  });

  it('keeps a newer non-URL edit unsaved when the older snapshot finishes saving', async () => {
    const pending = deferred();
    let sentTimeout: number | undefined;
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([enabledPrinter, secondPrinter])),
      http.post('/api/v1/printers/:id/wled/presets', () => HttpResponse.json([])),
      http.patch('/api/v1/printers/1', async ({ request }) => {
        const body = await request.json() as { wled_config: typeof enabledPrinter.wled_config };
        sentTimeout = body.wled_config.finished_timeout_seconds;
        await pending.promise;
        return HttpResponse.json({ ...enabledPrinter, wled_config: body.wled_config });
      }),
    );
    render(<WLEDSettings />);
    await screen.findByDisplayValue('http://wled.local');
    await userEvent.clear(screen.getByLabelText('Finished timeout (seconds)'));
    await userEvent.type(screen.getByLabelText('Finished timeout (seconds)'), '10');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sentTimeout).toBe(10));
    await userEvent.clear(screen.getByLabelText('Finished timeout (seconds)'));
    await userEvent.type(screen.getByLabelText('Finished timeout (seconds)'), '20');
    pending.resolve();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByText('WLED settings saved')).toBeNull();
    await userEvent.selectOptions(screen.getByLabelText('Printer'), '2');
    await userEvent.selectOptions(screen.getByLabelText('Printer'), '1');
    expect(screen.getByLabelText('Finished timeout (seconds)')).toHaveValue(20);
  });

  it('does not change B when a save for A completes after switching', async () => {
    const pending = deferred();
    let saveStarted = false;
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([enabledPrinter, secondPrinter])),
      http.post('/api/v1/printers/:id/wled/presets', () => HttpResponse.json([])),
      http.patch('/api/v1/printers/1', async ({ request }) => {
        saveStarted = true;
        const body = await request.json() as { wled_config: typeof enabledPrinter.wled_config };
        await pending.promise;
        return HttpResponse.json({ ...enabledPrinter, wled_config: body.wled_config });
      }),
    );
    render(<WLEDSettings />);
    await screen.findByDisplayValue('http://wled.local');
    await userEvent.clear(screen.getByLabelText('WLED URL'));
    await userEvent.type(screen.getByLabelText('WLED URL'), 'http://saved-a.local');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(saveStarted).toBe(true));
    await userEvent.selectOptions(screen.getByLabelText('Printer'), '2');
    await screen.findByDisplayValue('http://second.local');
    pending.resolve();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.getByLabelText('WLED URL')).toHaveValue('http://second.local');
    await userEvent.selectOptions(screen.getByLabelText('Printer'), '1');
    expect(screen.getByLabelText('WLED URL')).toHaveValue('http://saved-a.local');
  });

  it('retains A draft after a failed save and switching away and back', async () => {
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([enabledPrinter, secondPrinter])),
      http.post('/api/v1/printers/:id/wled/presets', () => HttpResponse.json([])),
      http.patch('/api/v1/printers/1', () => HttpResponse.json({}, { status: 502 })),
    );
    render(<WLEDSettings />);
    await screen.findByDisplayValue('http://wled.local');
    await userEvent.clear(screen.getByLabelText('WLED URL'));
    await userEvent.type(screen.getByLabelText('WLED URL'), 'http://unsaved-a.local');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Could not save WLED settings');
    await userEvent.selectOptions(screen.getByLabelText('Printer'), '2');
    await userEvent.selectOptions(screen.getByLabelText('Printer'), '1');
    expect(screen.getByLabelText('WLED URL')).toHaveValue('http://unsaved-a.local');
  });

  it('selects another printer when the current one disappears from the query', async () => {
    let listed = [enabledPrinter, secondPrinter];
    server.use(http.get('/api/v1/printers/', () => HttpResponse.json(listed)));
    render(<RefetchableWLEDSettings />);
    await screen.findByDisplayValue('http://wled.local');
    await userEvent.selectOptions(screen.getByLabelText('Printer'), '2');
    await screen.findByDisplayValue('http://second.local');
    await userEvent.clear(screen.getByLabelText('WLED URL'));
    await userEvent.type(screen.getByLabelText('WLED URL'), 'http://draft-b.local');
    listed = [enabledPrinter];
    await userEvent.click(screen.getByRole('button', { name: 'Refetch printers' }));
    await waitFor(() => expect(screen.getByLabelText('Printer')).toHaveValue('1'));
    expect(screen.getByLabelText('WLED URL')).toHaveValue('http://wled.local');
  });

  it('keeps the integration card and disabled toggle visible without printers', async () => {
    server.use(http.get('/api/v1/printers/', () => HttpResponse.json([])));
    render(<WLEDSettings />);

    expect(await screen.findByRole('heading', { name: 'WLED' })).toBeInTheDocument();
    expect(screen.getByText('No printers configured.')).toBeInTheDocument();
    expect(screen.getByLabelText('Enable WLED integration')).toBeDisabled();
  });

  it('keeps saved values visible and disabled until the integration is enabled', async () => {
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([printer])),
      http.post('/api/v1/printers/1/wled/presets', () =>
        HttpResponse.json([{ id: 1, name: 'Idle White' }, { id: 9, name: 'Finished Green' }]),
      ),
    );
    render(<WLEDSettings />);

    const url = await screen.findByDisplayValue('http://wled.local');
    const printing = screen.getByLabelText('Printing');
    const timeout = screen.getByLabelText('Finished timeout (seconds)');
    expect(url).toBeDisabled();
    expect(printing).toBeDisabled();
    expect(printing).toHaveValue('99');
    expect(screen.getByText(/During runtime, Bambuddy only activates the configured preset/)).toBeInTheDocument();
    expect(timeout).toBeDisabled();
    expect(timeout).toHaveValue(120);

    await userEvent.click(screen.getByLabelText('Enable WLED integration'));
    expect(url).toBeEnabled();
    expect(printing).toBeEnabled();
    await userEvent.click(screen.getByRole('button', { name: 'Load presets' }));

    expect(await screen.findByText('2 presets loaded')).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Saved preset 99 — currently unavailable' })).toBeInTheDocument();
    expect(printing).toHaveValue('99');

    await userEvent.click(screen.getByLabelText('Enable WLED integration'));
    expect(url).toBeDisabled();
    expect(printing).toBeDisabled();
    expect(printing).toHaveValue('99');
    expect(timeout).toHaveValue(120);
  });

  it('loads presets automatically for a saved enabled configuration', async () => {
    let requests = 0;
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([{
        ...printer,
        wled_config: { ...printer.wled_config, enabled: true },
      }])),
      http.post('/api/v1/printers/1/wled/presets', () => {
        requests += 1;
        return HttpResponse.json([
          { id: 1, name: 'Idle White' },
          { id: 9, name: 'Finished Green' },
          { id: 99, name: 'Printing Blue' },
        ]);
      }),
    );
    render(<WLEDSettings />);

    expect(await screen.findByText('3 presets loaded')).toBeInTheDocument();
    const printing = screen.getByLabelText('Printing');
    expect(printing).toHaveValue('99');
    expect(within(printing).getByRole('option', { name: 'Printing Blue (99)' })).toBeInTheDocument();
    expect(requests).toBe(1);
  });

  it('edits and saves enabled, URL, preset and finished timeout', async () => {
    let saved: unknown;
    let testedPreset: unknown;
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([{ ...printer, wled_config: null }])),
      http.post('/api/v1/printers/1/wled/presets', () => HttpResponse.json([{ id: 3, name: 'Printing Blue' }])),
      http.post('/api/v1/printers/1/wled/test-connection', () =>
        HttpResponse.json({ name: 'Kitchen LEDs', version: '0.15.0' })),
      http.post('/api/v1/printers/1/wled/test-preset', async ({ request }) => {
        testedPreset = await request.json();
        return HttpResponse.json({ success: true });
      }),
      http.patch('/api/v1/printers/1', async ({ request }) => {
        saved = await request.json();
        return HttpResponse.json({ ...printer, ...(saved as object) });
      }),
    );
    render(<WLEDSettings />);

    await screen.findByText('X1C Workshop');
    await userEvent.click(screen.getByLabelText('Enable WLED integration'));
    await userEvent.type(screen.getByLabelText('WLED URL'), 'http://wled.local');
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    expect(await screen.findByText('Connected to WLED (Kitchen LEDs) · v0.15.0')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Load presets' }));
    await screen.findByText('1 presets loaded');
    await userEvent.selectOptions(screen.getByLabelText('Printing'), '3');
    expect(screen.getByText('Configured states: 1 · Disabled states: 9')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Test Printing' }));
    await waitFor(() => expect(testedPreset).toEqual({ base_url: 'http://wled.local', preset_id: 3 }));
    await userEvent.clear(screen.getByLabelText('Finished timeout (seconds)'));
    await userEvent.type(screen.getByLabelText('Finished timeout (seconds)'), '30');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(saved).toEqual({
      wled_config: expect.objectContaining({
        enabled: true,
        base_url: 'http://wled.local',
        presets: expect.objectContaining({ printing: 3 }),
        finished_timeout_seconds: 30,
      }),
    }));
  });

  it('shows the offline state without clearing saved selections', async () => {
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([{
        ...printer,
        wled_config: { ...printer.wled_config, enabled: true },
      }])),
      http.post('/api/v1/printers/1/wled/presets', () => HttpResponse.json({}, { status: 502 })),
      http.post('/api/v1/printers/1/wled/test-connection', () => HttpResponse.json({}, { status: 502 })),
    );
    render(<WLEDSettings />);

    await screen.findByDisplayValue('http://wled.local');
    await userEvent.click(screen.getByRole('button', { name: 'Load presets' }));
    expect((await screen.findAllByText('WLED is unavailable. Saved preset IDs are kept.')).length).toBeGreaterThan(0);
    expect(screen.getByLabelText('Printing')).toHaveValue('99');
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    expect(await screen.findByText('Unable to connect to WLED.')).toBeInTheDocument();
  });
});

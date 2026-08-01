import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { render } from '../utils';
import { QsvDiagnosticPanel } from '../../components/QsvDiagnosticPanel';
import { api, type QsvDiagnosticResult } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    diagnoseQsv: vi.fn(),
    getSettings: vi.fn().mockResolvedValue({}),
  },
}));

function makeResult(
  overrides: Partial<QsvDiagnosticResult> = {},
): QsvDiagnosticResult {
  return {
    available: true,
    overall_status: 'ok',
    device: '/dev/dri/renderD128',
    summary_code: 'ok',
    stages: [
      {
        name: 'ffmpeg',
        status: 'ok',
        duration_ms: 1,
        code: null,
        detail: 'FFmpeg found',
      },
      {
        name: 'render_device',
        status: 'ok',
        duration_ms: 2,
        code: null,
        detail: '/dev/dri/renderD128',
      },
      {
        name: 'qsv_codecs',
        status: 'ok',
        duration_ms: 3,
        code: null,
        detail: 'h264_qsv, mjpeg_qsv',
      },
      {
        name: 'qsv_initialization',
        status: 'ok',
        duration_ms: 4,
        code: null,
        detail: 'Quick Sync initialized',
      },
    ],
    ...overrides,
  };
}

async function runDiagnostic() {
  const user = userEvent.setup();

  await user.click(
    screen.getByRole('button', {
      name: /run compatibility check/i,
    }),
  );
}

describe('QsvDiagnosticPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows the manual compatibility check before making a request', () => {
    render(<QsvDiagnosticPanel selected={false} />);

    expect(
      screen.getByRole('button', {
        name: /run compatibility check/i,
      }),
    ).toBeInTheDocument();

    expect(api.diagnoseQsv).not.toHaveBeenCalled();
  });

  it('shows the available state after a successful diagnostic', async () => {
    vi.mocked(api.diagnoseQsv).mockResolvedValue(makeResult());

    render(<QsvDiagnosticPanel selected={false} />);
    await runDiagnostic();

    expect(
      await screen.findByText(/intel quick sync is available/i),
    ).toBeInTheDocument();
  });

  it('maps render_device_missing to its specific message', async () => {
    vi.mocked(api.diagnoseQsv).mockResolvedValue(
      makeResult({
        available: false,
        overall_status: 'failed',
        summary_code: 'render_device_missing',
        device: '',
        stages: [
          {
            name: 'render_device',
            status: 'failed',
            duration_ms: 2,
            code: 'render_device_missing',
            detail: 'No DRM render device found',
          },
        ],
      }),
    );

    render(<QsvDiagnosticPanel selected={false} />);
    await runDiagnostic();

    expect(
      await screen.findByText(/no accessible gpu render device was found/i),
    ).toBeInTheDocument();
  });

  it('uses the generic failure message for an unknown code', async () => {
    vi.mocked(api.diagnoseQsv).mockResolvedValue(
      makeResult({
        available: false,
        overall_status: 'failed',
        summary_code: 'future_backend_code',
        stages: [
          {
            name: 'ffmpeg',
            status: 'failed',
            duration_ms: 1,
            code: null,
            detail: 'Unknown failure',
          },
        ],
      }),
    );

    render(<QsvDiagnosticPanel selected={false} />);
    await runDiagnostic();

    expect(
      await screen.findByText(
        /quick sync is not available.*open the details below/i,
      ),
    ).toBeInTheDocument();
  });

  it('reports a missing Intel QSV runtime', async () => {
    vi.mocked(api.diagnoseQsv).mockResolvedValue(
      makeResult({
        available: false,
        overall_status: 'failed',
        summary_code: 'qsv_runtime_missing',
        stages: [
          {
            name: 'render_device',
            status: 'failed',
            duration_ms: 10,
            code: 'qsv_runtime_missing',
            detail: 'Error creating a MFX session: -9.',
          },
        ],
      }),
    );

    render(<QsvDiagnosticPanel selected={false} />);
    await runDiagnostic();

    expect(
      await screen.findByText(/install libmfx-gen1\.2/i),
    ).toBeInTheDocument();
  });

  it('uses warning styling when unavailable QSV is selected', async () => {
    vi.mocked(api.diagnoseQsv).mockResolvedValue(
      makeResult({
        available: false,
        overall_status: 'failed',
        summary_code: 'qsv_initialization_failed',
        stages: [
          {
            name: 'qsv_initialization',
            status: 'failed',
            duration_ms: 10,
            code: 'qsv_initialization_failed',
            detail: 'MFX session failed',
          },
        ],
      }),
    );

    const { container } = render(<QsvDiagnosticPanel selected />);
    await runDiagnostic();

    await screen.findByText(/intel quick sync is unavailable/i);

    expect(
      container.querySelector('.border-amber-500\\/40'),
    ).toBeInTheDocument();
  });

  it('uses error styling when unavailable QSV is not selected', async () => {
    vi.mocked(api.diagnoseQsv).mockResolvedValue(
      makeResult({
        available: false,
        overall_status: 'failed',
        summary_code: 'qsv_initialization_failed',
        stages: [
          {
            name: 'qsv_initialization',
            status: 'failed',
            duration_ms: 10,
            code: 'qsv_initialization_failed',
            detail: 'MFX session failed',
          },
        ],
      }),
    );

    const { container } = render(
      <QsvDiagnosticPanel selected={false} />,
    );
    await runDiagnostic();

    await screen.findByText(/intel quick sync is unavailable/i);

    expect(
      container.querySelector('.border-red-500\\/30'),
    ).toBeInTheDocument();
  });
});

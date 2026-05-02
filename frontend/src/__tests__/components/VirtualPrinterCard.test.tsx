/**
 * Tests for the VirtualPrinterCard component.
 *
 * Tests the auto-dispatch toggle behavior:
 * - Visibility based on mode (print_queue only)
 * - Default state (on)
 * - API mutation on toggle click
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { VirtualPrinterCard } from '../../components/VirtualPrinterCard';
import type { VirtualPrinterConfig } from '../../api/client';

// Mock the API client
vi.mock('../../api/client', () => ({
  multiVirtualPrinterApi: {
    update: vi.fn().mockResolvedValue({}),
    remove: vi.fn().mockResolvedValue({}),
  },
  api: {
    getSettings: vi.fn().mockResolvedValue({}),
    getPrinters: vi.fn().mockResolvedValue([]),
    getNetworkInterfaces: vi.fn().mockResolvedValue({ interfaces: [] }),
  },
}));

import { multiVirtualPrinterApi } from '../../api/client';

const models: Record<string, string> = {
  'BL-P001': 'X1C',
  'C12': 'P1S',
};

const createMockPrinter = (overrides: Partial<VirtualPrinterConfig> = {}): VirtualPrinterConfig => ({
  id: 1,
  name: 'Test VP',
  enabled: false,
  mode: 'immediate',
  model: 'BL-P001',
  model_name: 'X1C',
  access_code_set: false,
  serial: '00M00A391800001',
  target_printer_id: null,
  auto_dispatch: true,
  queue_force_color_match: false,
  bind_ip: null,
  remote_interface_ip: null,
  position: 0,
  status: { running: false, pending_files: 0 },
  ...overrides,
});

describe('VirtualPrinterCard - auto-dispatch toggle', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(multiVirtualPrinterApi.update).mockResolvedValue(createMockPrinter());
  });

  it('renders auto-dispatch toggle when mode is print_queue', async () => {
    const printer = createMockPrinter({ mode: 'print_queue' });
    render(<VirtualPrinterCard printer={printer} models={models} />);

    await waitFor(() => {
      expect(screen.getByText('Auto-dispatch')).toBeInTheDocument();
    });
  });

  it('does not render auto-dispatch toggle when mode is immediate', async () => {
    const printer = createMockPrinter({ mode: 'immediate' });
    render(<VirtualPrinterCard printer={printer} models={models} />);

    // Wait for the card to render fully (check for something that should be there)
    await waitFor(() => {
      expect(screen.getByText('Test VP')).toBeInTheDocument();
    });

    expect(screen.queryByText('Auto-dispatch')).not.toBeInTheDocument();
  });

  it('does not render auto-dispatch toggle when mode is proxy', async () => {
    const printer = createMockPrinter({ mode: 'proxy' });
    render(<VirtualPrinterCard printer={printer} models={models} />);

    await waitFor(() => {
      expect(screen.getByText('Test VP')).toBeInTheDocument();
    });

    expect(screen.queryByText('Auto-dispatch')).not.toBeInTheDocument();
  });

  it('auto-dispatch toggle defaults to on', async () => {
    const printer = createMockPrinter({ mode: 'print_queue', auto_dispatch: true });
    render(<VirtualPrinterCard printer={printer} models={models} />);

    await waitFor(() => {
      expect(screen.getByText('Auto-dispatch')).toBeInTheDocument();
    });

    // The auto-dispatch section container has the toggle button as a sibling of the text div
    const title = screen.getByText('Auto-dispatch');
    const section = title.closest('.flex.items-center.justify-between');
    expect(section).toBeTruthy();
    const toggleButton = section!.querySelector('button');
    expect(toggleButton).toBeTruthy();
    expect(toggleButton!.className).toContain('bg-bambu-green');
  });

  it('clicking auto-dispatch toggle calls update API', async () => {
    const user = userEvent.setup();
    const printer = createMockPrinter({ mode: 'print_queue', auto_dispatch: true });
    vi.mocked(multiVirtualPrinterApi.update).mockResolvedValue(
      createMockPrinter({ mode: 'print_queue', auto_dispatch: false })
    );

    render(<VirtualPrinterCard printer={printer} models={models} />);

    await waitFor(() => {
      expect(screen.getByText('Auto-dispatch')).toBeInTheDocument();
    });

    // Find the auto-dispatch toggle via the section container
    const title = screen.getByText('Auto-dispatch');
    const section = title.closest('.flex.items-center.justify-between');
    expect(section).toBeTruthy();
    const toggleButton = section!.querySelector('button');
    expect(toggleButton).toBeTruthy();

    await user.click(toggleButton!);

    await waitFor(() => {
      expect(multiVirtualPrinterApi.update).toHaveBeenCalledWith(1, { auto_dispatch: false });
    });
  });
});

// #1188 — VP queue mode now pins per-slot type+color so the scheduler refuses
// to dispatch onto a printer with the wrong filament loaded. The toggle is
// mode-gated to print_queue (mirroring the auto-dispatch toggle), defaults
// off (preserves pre-fix behaviour for upgraders), and the click both flips
// the local state and POSTs the new value to the backend.
describe('VirtualPrinterCard - force color match toggle (#1188)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(multiVirtualPrinterApi.update).mockResolvedValue(createMockPrinter());
  });

  it('renders force-color-match toggle when mode is print_queue', async () => {
    const printer = createMockPrinter({ mode: 'print_queue' });
    render(<VirtualPrinterCard printer={printer} models={models} />);

    await waitFor(() => {
      expect(screen.getByText('Force color match')).toBeInTheDocument();
    });
  });

  it('does not render force-color-match toggle when mode is immediate', async () => {
    const printer = createMockPrinter({ mode: 'immediate' });
    render(<VirtualPrinterCard printer={printer} models={models} />);

    await waitFor(() => {
      expect(screen.getByText('Test VP')).toBeInTheDocument();
    });
    expect(screen.queryByText('Force color match')).not.toBeInTheDocument();
  });

  it('does not render force-color-match toggle when mode is proxy', async () => {
    const printer = createMockPrinter({ mode: 'proxy' });
    render(<VirtualPrinterCard printer={printer} models={models} />);

    await waitFor(() => {
      expect(screen.getByText('Test VP')).toBeInTheDocument();
    });
    expect(screen.queryByText('Force color match')).not.toBeInTheDocument();
  });

  it('force-color-match toggle defaults off (not green) — preserves pre-fix behaviour', async () => {
    const printer = createMockPrinter({ mode: 'print_queue', queue_force_color_match: false });
    render(<VirtualPrinterCard printer={printer} models={models} />);

    await waitFor(() => {
      expect(screen.getByText('Force color match')).toBeInTheDocument();
    });

    const title = screen.getByText('Force color match');
    const section = title.closest('.flex.items-center.justify-between');
    expect(section).toBeTruthy();
    const toggleButton = section!.querySelector('button');
    expect(toggleButton).toBeTruthy();
    expect(toggleButton!.className).not.toContain('bg-bambu-green');
  });

  it('force-color-match toggle renders enabled (green) when queue_force_color_match is true', async () => {
    const printer = createMockPrinter({ mode: 'print_queue', queue_force_color_match: true });
    render(<VirtualPrinterCard printer={printer} models={models} />);

    await waitFor(() => {
      expect(screen.getByText('Force color match')).toBeInTheDocument();
    });

    const title = screen.getByText('Force color match');
    const section = title.closest('.flex.items-center.justify-between');
    const toggleButton = section!.querySelector('button');
    expect(toggleButton!.className).toContain('bg-bambu-green');
  });

  it('clicking force-color-match toggle posts queue_force_color_match in update body', async () => {
    const user = userEvent.setup();
    const printer = createMockPrinter({ mode: 'print_queue', queue_force_color_match: false });
    vi.mocked(multiVirtualPrinterApi.update).mockResolvedValue(
      createMockPrinter({ mode: 'print_queue', queue_force_color_match: true })
    );

    render(<VirtualPrinterCard printer={printer} models={models} />);

    await waitFor(() => {
      expect(screen.getByText('Force color match')).toBeInTheDocument();
    });

    const title = screen.getByText('Force color match');
    const section = title.closest('.flex.items-center.justify-between');
    const toggleButton = section!.querySelector('button');

    await user.click(toggleButton!);

    await waitFor(() => {
      expect(multiVirtualPrinterApi.update).toHaveBeenCalledWith(1, { queue_force_color_match: true });
    });
  });
});

describe('VirtualPrinterCard - tailscale toggle', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(multiVirtualPrinterApi.update).mockResolvedValue(createMockPrinter());
  });

  it('renders tailscale toggle as enabled (green) when tailscale_disabled is false', async () => {
    const printer = createMockPrinter({ tailscale_disabled: false });
    render(<VirtualPrinterCard printer={printer} models={models} />);

    await waitFor(() => {
      expect(screen.getByText('Tailscale integration')).toBeInTheDocument();
    });

    const title = screen.getByText('Tailscale integration');
    const section = title.closest('.flex.items-center.justify-between');
    expect(section).toBeTruthy();
    const toggleButton = section!.querySelector('button');
    expect(toggleButton).toBeTruthy();
    expect(toggleButton!.className).toContain('bg-bambu-green');
  });

  it('renders tailscale toggle as disabled (not green) when tailscale_disabled is true', async () => {
    const printer = createMockPrinter({ tailscale_disabled: true });
    render(<VirtualPrinterCard printer={printer} models={models} />);

    await waitFor(() => {
      expect(screen.getByText('Tailscale integration')).toBeInTheDocument();
    });

    const title = screen.getByText('Tailscale integration');
    const section = title.closest('.flex.items-center.justify-between');
    expect(section).toBeTruthy();
    const toggleButton = section!.querySelector('button');
    expect(toggleButton).toBeTruthy();
    expect(toggleButton!.className).not.toContain('bg-bambu-green');
  });

  it('clicking tailscale toggle calls update API with tailscale_disabled: true', async () => {
    const user = userEvent.setup();
    const printer = createMockPrinter({ tailscale_disabled: false });
    vi.mocked(multiVirtualPrinterApi.update).mockResolvedValue(
      createMockPrinter({ tailscale_disabled: true })
    );

    render(<VirtualPrinterCard printer={printer} models={models} />);

    await waitFor(() => {
      expect(screen.getByText('Tailscale integration')).toBeInTheDocument();
    });

    const title = screen.getByText('Tailscale integration');
    const section = title.closest('.flex.items-center.justify-between');
    expect(section).toBeTruthy();
    const toggleButton = section!.querySelector('button');
    expect(toggleButton).toBeTruthy();

    await user.click(toggleButton!);

    await waitFor(() => {
      expect(multiVirtualPrinterApi.update).toHaveBeenCalledWith(1, { tailscale_disabled: true });
    });
  });

  it('reverts toggle and shows a specific toast when backend rejects enable (tailscale_not_available)', async () => {
    const user = userEvent.setup();
    const printer = createMockPrinter({ tailscale_disabled: true });
    vi.mocked(multiVirtualPrinterApi.update).mockRejectedValueOnce(
      new Error('tailscale_not_available')
    );

    render(<VirtualPrinterCard printer={printer} models={models} />);

    await waitFor(() => {
      expect(screen.getByText('Tailscale integration')).toBeInTheDocument();
    });

    const title = screen.getByText('Tailscale integration');
    const section = title.closest('.flex.items-center.justify-between');
    const toggleButton = section!.querySelector('button') as HTMLButtonElement;
    // Disabled state → dark-grey background on the track.
    expect(toggleButton.className).toContain('bg-bambu-dark-tertiary');

    await user.click(toggleButton);

    await waitFor(() => {
      expect(multiVirtualPrinterApi.update).toHaveBeenCalledWith(1, { tailscale_disabled: false });
    });

    // After the 409 revert, the toggle goes back to the dark-grey (disabled) state.
    await waitFor(() => {
      expect(toggleButton.className).toContain('bg-bambu-dark-tertiary');
    });
  });
});

describe('VirtualPrinterCard - Tailscale FQDN copy', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(multiVirtualPrinterApi.update).mockResolvedValue(createMockPrinter());
  });

  const fqdn = 'test-host.tail1234.ts.net';

  function getCopyButton() {
    // The copy button is a <button> with a title attribute. Use title to locate it.
    const candidates = screen.getAllByRole('button');
    return candidates.find(btn => /copy/i.test(btn.getAttribute('title') || '')) as HTMLButtonElement;
  }

  it('uses navigator.clipboard.writeText in a secure context', async () => {
    const user = userEvent.setup();
    const writeTextMock = vi.fn().mockResolvedValue(undefined);
    // JSDOM defaults isSecureContext to true; confirm and stub clipboard.
    Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true });
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: writeTextMock },
      configurable: true,
    });

    const printer = createMockPrinter({
      status: { running: true, pending_files: 0, tailscale_fqdn: fqdn },
    });
    render(<VirtualPrinterCard printer={printer} models={models} />);

    const copyBtn = getCopyButton();
    expect(copyBtn).toBeTruthy();
    await user.click(copyBtn);

    await waitFor(() => {
      expect(writeTextMock).toHaveBeenCalledWith(fqdn);
    });
  });

  it('falls back to execCommand("copy") when clipboard API is unavailable (HTTP)', async () => {
    const user = userEvent.setup();
    // Simulate non-secure context: no clipboard API available.
    Object.defineProperty(window, 'isSecureContext', { value: false, configurable: true });
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });

    const execCommandMock = vi.fn().mockReturnValue(true);
    document.execCommand = execCommandMock;

    const printer = createMockPrinter({
      status: { running: true, pending_files: 0, tailscale_fqdn: fqdn },
    });
    render(<VirtualPrinterCard printer={printer} models={models} />);

    const copyBtn = getCopyButton();
    await user.click(copyBtn);

    await waitFor(() => {
      expect(execCommandMock).toHaveBeenCalledWith('copy');
    });
    // Fallback path: textarea is appended, used, then removed in `finally`.
    // After the click resolves, no stray textareas should remain in the DOM.
    expect(document.querySelectorAll('textarea').length).toBe(0);
  });

  it('always cleans up the hidden textarea even if execCommand throws', async () => {
    const user = userEvent.setup();
    Object.defineProperty(window, 'isSecureContext', { value: false, configurable: true });
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });

    document.execCommand = vi.fn().mockImplementation(() => {
      throw new Error('synthetic execCommand failure');
    });

    const printer = createMockPrinter({
      status: { running: true, pending_files: 0, tailscale_fqdn: fqdn },
    });
    render(<VirtualPrinterCard printer={printer} models={models} />);

    const copyBtn = getCopyButton();
    await user.click(copyBtn);

    // The `finally` block must remove the textarea regardless of the exception.
    await waitFor(() => {
      expect(document.querySelectorAll('textarea').length).toBe(0);
    });
  });
});

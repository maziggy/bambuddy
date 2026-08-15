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
    getTailscaleStatus: vi.fn().mockResolvedValue({
      available: false,
      fqdn: '',
      hostname: '',
      tailnet_name: '',
      tailscale_ips: [],
      error: null,
    }),
  },
  api: {
    getSettings: vi.fn().mockResolvedValue({}),
    getPrinters: vi.fn().mockResolvedValue([]),
    getNetworkInterfaces: vi.fn().mockResolvedValue({ interfaces: [] }),
  },
}));

import { multiVirtualPrinterApi, api } from '../../api/client';

const models: Record<string, string> = {
  'BL-P001': 'X1C',
  'C12': 'P1S',
};

const createMockPrinter = (overrides: Partial<VirtualPrinterConfig> = {}): VirtualPrinterConfig => ({
  id: 1,
  name: 'Test VP',
  enabled: false,
  mode: 'archive',
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
    const printer = createMockPrinter({ mode: 'queue' });
    render(<VirtualPrinterCard printer={printer} models={models} />);

    await waitFor(() => {
      expect(screen.getByText('Auto-dispatch')).toBeInTheDocument();
    });
  });

  it('does not render auto-dispatch toggle when mode is immediate', async () => {
    const printer = createMockPrinter({ mode: 'archive' });
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
    const printer = createMockPrinter({ mode: 'queue', auto_dispatch: true });
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
    const printer = createMockPrinter({ mode: 'queue', auto_dispatch: true });
    vi.mocked(multiVirtualPrinterApi.update).mockResolvedValue(
      createMockPrinter({ mode: 'queue', auto_dispatch: false })
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
    const printer = createMockPrinter({ mode: 'queue' });
    render(<VirtualPrinterCard printer={printer} models={models} />);

    await waitFor(() => {
      expect(screen.getByText('Force color match')).toBeInTheDocument();
    });
  });

  it('does not render force-color-match toggle when mode is immediate', async () => {
    const printer = createMockPrinter({ mode: 'archive' });
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
    const printer = createMockPrinter({ mode: 'queue', queue_force_color_match: false });
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
    const printer = createMockPrinter({ mode: 'queue', queue_force_color_match: true });
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
    const printer = createMockPrinter({ mode: 'queue', queue_force_color_match: false });
    vi.mocked(multiVirtualPrinterApi.update).mockResolvedValue(
      createMockPrinter({ mode: 'queue', queue_force_color_match: true })
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

});

describe('VirtualPrinterCard - Tailscale FQDN copy', () => {
  const fqdn = 'test-host.tail1234.ts.net';

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(multiVirtualPrinterApi.update).mockResolvedValue(createMockPrinter());
    // FQDN now comes from the host-level Tailscale status endpoint, not VP status.
    // Tests in this block need the toggle to be ON (tailscale_disabled=false) so the
    // useQuery actually fires and the FQDN row renders.
    vi.mocked(multiVirtualPrinterApi.getTailscaleStatus).mockResolvedValue({
      available: true,
      fqdn,
      hostname: 'test-host',
      tailnet_name: 'tail1234.ts.net',
      tailscale_ips: ['100.64.0.1'],
      error: null,
    });
  });

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

    const printer = createMockPrinter({ tailscale_disabled: false });
    render(<VirtualPrinterCard printer={printer} models={models} />);

    const copyBtn = await waitFor(() => {
      const btn = getCopyButton();
      if (!btn) throw new Error('copy button not yet rendered');
      return btn;
    });
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

    const printer = createMockPrinter({ tailscale_disabled: false });
    render(<VirtualPrinterCard printer={printer} models={models} />);

    const copyBtn = await waitFor(() => {
      const btn = getCopyButton();
      if (!btn) throw new Error('copy button not yet rendered');
      return btn;
    });
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

    const printer = createMockPrinter({ tailscale_disabled: false });
    render(<VirtualPrinterCard printer={printer} models={models} />);

    const copyBtn = await waitFor(() => {
      const btn = getCopyButton();
      if (!btn) throw new Error('copy button not yet rendered');
      return btn;
    });
    await user.click(copyBtn);

    // The `finally` block must remove the textarea regardless of the exception.
    await waitFor(() => {
      expect(document.querySelectorAll('textarea').length).toBe(0);
    });
  });
});

// Non-proxy VPs with a target printer derive their access code from the
// target — the live-mirror bridge forwards slicer auth to the real printer,
// so the codes must match. The card surfaces the target's code read-only
// (with an Eye-toggle reveal) so the user knows what to type into the slicer
// but can't diverge it from the printer's. When no target is set, the field
// stays editable.
describe('VirtualPrinterCard - access code inherits from target', () => {
  const printers = [
    {
      id: 7,
      name: 'Workshop X1C',
      ip_address: '192.168.1.50',
      access_code: 'TGTCODE1',
      serial_number: '01P00A391800001',
      model: 'X1C',
      is_active: true,
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(multiVirtualPrinterApi.update).mockResolvedValue(createMockPrinter());
    // Re-mock the printers query for this block so the card has a target
    // printer it can read access_code from.
    vi.mocked(api.getPrinters).mockResolvedValue(printers as unknown as Awaited<ReturnType<typeof api.getPrinters>>);
  });

  it('shows target printer access code read-only when target is set on a non-proxy VP', async () => {
    const printer = createMockPrinter({ mode: 'queue', target_printer_id: 7 });
    render(<VirtualPrinterCard printer={printer} models={models} />);

    // Wait for the inheritance badge AND the actual code value to appear —
    // the badge renders synchronously from local state, but the value
    // depends on the printers query (api.getPrinters) resolving first.
    const codeInput = await waitFor(() => {
      const input = screen.getByLabelText('Access Code') as HTMLInputElement;
      if (input.value !== 'TGTCODE1') throw new Error('inherited value not populated yet');
      return input;
    });

    expect(screen.getByText('Inherited from target')).toBeInTheDocument();
    // Save button must NOT exist in the readonly path — the field is
    // managed via the target printer's settings, not this card.
    expect(screen.queryByRole('button', { name: /save/i })).not.toBeInTheDocument();
    expect(codeInput.readOnly).toBe(true);
    expect(codeInput.type).toBe('password');
  });

  it('toggles the access code to plaintext via the Eye button', async () => {
    const user = userEvent.setup();
    const printer = createMockPrinter({ mode: 'queue', target_printer_id: 7 });
    render(<VirtualPrinterCard printer={printer} models={models} />);

    await waitFor(() => {
      expect(screen.getByText('Inherited from target')).toBeInTheDocument();
    });

    const revealBtn = screen.getByRole('button', { name: /show access code/i });
    await user.click(revealBtn);

    const codeInput = screen.getByLabelText('Access Code') as HTMLInputElement;
    expect(codeInput.type).toBe('text');
  });

  it('keeps the editable input + Save button when no target is set', async () => {
    const printer = createMockPrinter({ mode: 'archive', target_printer_id: null });
    render(<VirtualPrinterCard printer={printer} models={models} />);

    await waitFor(() => {
      expect(screen.getByPlaceholderText('Enter 8-char code')).toBeInTheDocument();
    });

    // Inheritance badge must NOT appear when there's no target.
    expect(screen.queryByText('Inherited from target')).not.toBeInTheDocument();
    // Save button IS present in the editable path (disabled until 8 chars typed).
    expect(screen.getByRole('button', { name: /save/i })).toBeInTheDocument();
  });
});

// The collapsed header used to be a single flex row of flex-shrink-0 items,
// so it was as wide as its contents and spilled past the card's border --
// the reporter of #2808 saw an IP address and the enable toggle floating on
// the page background. jsdom does no layout, so these pin the CSS contract
// that makes wrapping possible rather than the pixels: a flexible, wrappable
// metadata group, and no item that both refuses to shrink and claims it will
// truncate.
describe('VirtualPrinterCard - header does not overflow the card', () => {
  const printers = [
    {
      id: 9,
      // The name a printer adopted without one gets (discovery.py) -- 25
      // unbreakable characters, and half of why this overflowed.
      name: 'Printer at 192.168.30.210',
      ip_address: '192.168.30.210',
      access_code: 'TGTCODE1',
      serial_number: '01P00A391800001',
      model: 'H2C',
      is_active: true,
    },
  ];

  const twoVlanPrinter = () =>
    createMockPrinter({
      name: 'H2C',
      mode: 'proxy',
      model_name: 'H2C',
      target_printer_id: 9,
      // Both are populated only when Bambuddy and the printer sit on
      // different subnets -- the reporter's case.
      bind_ip: '192.168.20.175',
      remote_interface_ip: '192.168.30.210',
    });

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(multiVirtualPrinterApi.update).mockResolvedValue(createMockPrinter());
    vi.mocked(api.getPrinters).mockResolvedValue(printers as unknown as Awaited<ReturnType<typeof api.getPrinters>>);
  });

  it('keeps both IPs visible instead of truncating them away', async () => {
    render(<VirtualPrinterCard printer={twoVlanPrinter()} models={models} />);

    await waitFor(() => {
      expect(screen.getByText('192.168.20.175')).toBeInTheDocument();
    });
    // An operator opens this page to check exactly these two values, so the
    // fix has to wrap them, not hide them.
    expect(screen.getByText('192.168.30.210')).toBeInTheDocument();
  });

  it('puts the metadata in a shrinkable, wrapping group', async () => {
    render(<VirtualPrinterCard printer={twoVlanPrinter()} models={models} />);

    const bindIp = await screen.findByText('192.168.20.175');
    const group = bindIp.parentElement as HTMLElement;

    expect(group.className).toContain('flex-wrap');
    // Without min-w-0 a flex item cannot shrink below its content, which is
    // what defeated the name's `truncate` before.
    expect(group.className).toContain('min-w-0');
    expect(group.className).toContain('flex-1');
  });

  it('has no header item that is both unshrinkable and truncating', async () => {
    render(<VirtualPrinterCard printer={twoVlanPrinter()} models={models} />);

    const bindIp = await screen.findByText('192.168.20.175');
    const group = bindIp.parentElement as HTMLElement;

    const selfCancelling = Array.from(group.children).filter(
      (el) => el.className.includes('flex-shrink-0') && el.className.includes('truncate')
    );
    expect(selfCancelling).toHaveLength(0);
  });

  it('clips at the card border as a backstop', async () => {
    const { container } = render(<VirtualPrinterCard printer={twoVlanPrinter()} models={models} />);

    await screen.findByText('192.168.20.175');
    // Scoped to this card on purpose: cards elsewhere render menus that paint
    // outside their own bounds, so this must not migrate into `Card`.
    const card = container.querySelector('.rounded-xl') as HTMLElement;
    expect(card.className).toContain('overflow-hidden');
  });
});

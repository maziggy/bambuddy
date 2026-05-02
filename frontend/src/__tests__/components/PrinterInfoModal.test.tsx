/**
 * Tests for PrinterInfoModal — focused on the CopyButton clipboard fallback
 * (#1174). Bambuddy is commonly deployed over plain HTTP on a LAN, where
 * `navigator.clipboard` is gated by the secure-context requirement and the
 * previous code (which only tried the modern API and silently swallowed the
 * failure) left both copy buttons inert.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { PrinterInfoModal } from '../../components/PrinterInfoModal';
import type { Printer } from '../../api/client';

function mockPrinter(): Printer {
  return {
    id: 1,
    name: 'Test P1S',
    serial_number: '01S00A123456789',
    ip_address: '192.168.1.42',
    access_code: '12345678',
    model: 'P1S',
    location: null,
    nozzle_count: 1,
    is_active: true,
    auto_archive: true,
    external_camera_url: null,
    external_camera_type: null,
    external_camera_enabled: false,
    camera_rotation: 0,
    plate_detection_enabled: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };
}

function getCopyButtons() {
  // CopyButton renders as an icon button with title="Copy to clipboard".
  return screen.getAllByRole('button').filter(
    btn => /copy/i.test(btn.getAttribute('title') || ''),
  );
}

describe('PrinterInfoModal — CopyButton clipboard fallback (#1174)', () => {
  let originalIsSecureContext: PropertyDescriptor | undefined;
  let originalClipboard: PropertyDescriptor | undefined;
  let originalExecCommand: typeof document.execCommand;

  beforeEach(() => {
    originalIsSecureContext = Object.getOwnPropertyDescriptor(window, 'isSecureContext');
    originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
    originalExecCommand = document.execCommand;
  });

  afterEach(() => {
    if (originalIsSecureContext) {
      Object.defineProperty(window, 'isSecureContext', originalIsSecureContext);
    }
    if (originalClipboard) {
      Object.defineProperty(navigator, 'clipboard', originalClipboard);
    }
    document.execCommand = originalExecCommand;
    vi.clearAllMocks();
  });

  it('uses navigator.clipboard.writeText in a secure context (HTTPS / localhost)', async () => {
    const user = userEvent.setup();
    const writeTextMock = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true });
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: writeTextMock },
      configurable: true,
    });

    render(<PrinterInfoModal printer={mockPrinter()} onClose={() => {}} />);

    const buttons = getCopyButtons();
    expect(buttons.length).toBeGreaterThanOrEqual(2); // serial + ip at minimum

    // Click the first copy button (IP address row appears first).
    await user.click(buttons[0]);
    await waitFor(() => {
      expect(writeTextMock).toHaveBeenCalled();
    });
    // The value passed must be a real string from the printer fixture, not "".
    expect(writeTextMock.mock.calls[0][0]).toMatch(/^(192\.168\.1\.42|01S00A123456789)$/);
  });

  it('falls back to execCommand("copy") on plain-HTTP LAN deployments — pre-fix #1174 path', async () => {
    // Repro of the reporter's exact environment: plain-HTTP, no clipboard API.
    // Pre-fix the catch-block silently swallowed the TypeError on
    // navigator.clipboard.writeText and the icon never flipped to the tick.
    const user = userEvent.setup();
    Object.defineProperty(window, 'isSecureContext', { value: false, configurable: true });
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });

    const execCommandMock = vi.fn().mockReturnValue(true);
    document.execCommand = execCommandMock;

    render(<PrinterInfoModal printer={mockPrinter()} onClose={() => {}} />);

    const buttons = getCopyButtons();
    await user.click(buttons[0]);

    await waitFor(() => {
      expect(execCommandMock).toHaveBeenCalledWith('copy');
    });
    // Off-screen textarea must be cleaned up on the success path; otherwise
    // every click would leak a hidden DOM node.
    expect(document.querySelectorAll('textarea').length).toBe(0);
  });

  it('cleans up the off-screen textarea even when execCommand throws', async () => {
    // The fallback path uses a try/finally around execCommand. The finally
    // block must remove the textarea even if the browser rejects the copy
    // (e.g. permission denied), so a hostile / restricted environment doesn't
    // leak DOM nodes per click.
    const user = userEvent.setup();
    Object.defineProperty(window, 'isSecureContext', { value: false, configurable: true });
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });

    document.execCommand = vi.fn().mockImplementation(() => {
      throw new Error('synthetic execCommand failure');
    });

    render(<PrinterInfoModal printer={mockPrinter()} onClose={() => {}} />);

    const buttons = getCopyButtons();
    await user.click(buttons[0]);

    await waitFor(() => {
      expect(document.querySelectorAll('textarea').length).toBe(0);
    });
  });
});

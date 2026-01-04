/**
 * Tests for the VirtualPrinterSettings component.
 *
 * Tests the virtual printer configuration UI including:
 * - Enable/disable toggle
 * - Access code management
 * - Archive mode selection
 * - Status display
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { VirtualPrinterSettings } from '../../components/VirtualPrinterSettings';

// Mock the API client
vi.mock('../../api/client', () => ({
  api: {
    getSettings: vi.fn().mockResolvedValue({}),
    updateSettings: vi.fn().mockResolvedValue({}),
  },
  virtualPrinterApi: {
    getSettings: vi.fn(),
    updateSettings: vi.fn(),
    getModels: vi.fn().mockResolvedValue({
      models: {
        '3DPrinter-X1-Carbon': 'X1C',
        'C12': 'P1S',
        'N7': 'P2S',
      },
    }),
  },
}));

// Import mocked module
import { virtualPrinterApi } from '../../api/client';

// Mock data factory
const createMockSettings = (overrides = {}) => ({
  enabled: false,
  access_code_set: false,
  mode: 'immediate' as const,
  model: '3DPrinter-X1-Carbon',
  status: {
    enabled: false,
    running: false,
    mode: 'immediate',
    name: 'Bambuddy',
    serial: '00M00A391800001',
    model: '3DPrinter-X1-Carbon',
    model_name: 'X1C',
    pending_files: 0,
  },
  ...overrides,
});

describe('VirtualPrinterSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Default mock implementation
    vi.mocked(virtualPrinterApi.getSettings).mockResolvedValue(createMockSettings());
    vi.mocked(virtualPrinterApi.updateSettings).mockResolvedValue(createMockSettings());
  });

  describe('rendering', () => {
    it('renders loading state initially', () => {
      // Delay the API response to catch loading state
      vi.mocked(virtualPrinterApi.getSettings).mockImplementation(
        () => new Promise(() => {}) // Never resolves
      );
      render(<VirtualPrinterSettings />);

      // Should show loading spinner
      expect(document.querySelector('.animate-spin')).toBeInTheDocument();
    });

    it('renders component title', async () => {
      render(<VirtualPrinterSettings />);

      await waitFor(() => {
        expect(screen.getByText('Virtual Printer')).toBeInTheDocument();
      });
    });

    it('renders enable toggle', async () => {
      render(<VirtualPrinterSettings />);

      await waitFor(() => {
        expect(screen.getByText('Enable Virtual Printer')).toBeInTheDocument();
      });
    });

    it('renders access code section', async () => {
      render(<VirtualPrinterSettings />);

      await waitFor(() => {
        expect(screen.getByText('Access Code')).toBeInTheDocument();
      });
    });

    it('renders archive mode section', async () => {
      render(<VirtualPrinterSettings />);

      await waitFor(() => {
        expect(screen.getByText('Archive Mode')).toBeInTheDocument();
      });
    });

    it('renders how it works info', async () => {
      render(<VirtualPrinterSettings />);

      await waitFor(() => {
        expect(screen.getByText('How it works:')).toBeInTheDocument();
      });
    });
  });

  describe('status indicator', () => {
    it('shows Stopped when not running', async () => {
      vi.mocked(virtualPrinterApi.getSettings).mockResolvedValue(
        createMockSettings({ status: { ...createMockSettings().status, running: false } })
      );

      render(<VirtualPrinterSettings />);

      await waitFor(() => {
        expect(screen.getByText('Stopped')).toBeInTheDocument();
      });
    });

    it('shows Running when active', async () => {
      vi.mocked(virtualPrinterApi.getSettings).mockResolvedValue(
        createMockSettings({
          enabled: true,
          status: { ...createMockSettings().status, enabled: true, running: true },
        })
      );

      render(<VirtualPrinterSettings />);

      await waitFor(() => {
        expect(screen.getByText('Running')).toBeInTheDocument();
      });
    });

    it('shows status details when running', async () => {
      vi.mocked(virtualPrinterApi.getSettings).mockResolvedValue(
        createMockSettings({
          enabled: true,
          status: {
            enabled: true,
            running: true,
            mode: 'immediate',
            name: 'Bambuddy',
            serial: '00M00A391800001',
            model: '3DPrinter-X1-Carbon',
            model_name: 'X1C',
            pending_files: 0,
          },
        })
      );

      render(<VirtualPrinterSettings />);

      await waitFor(() => {
        expect(screen.getByText('Status Details')).toBeInTheDocument();
        expect(screen.getByText('Bambuddy')).toBeInTheDocument();
        expect(screen.getByText('00M00A391800001')).toBeInTheDocument();
      });
    });
  });

  describe('access code', () => {
    it('shows warning when access code not set', async () => {
      vi.mocked(virtualPrinterApi.getSettings).mockResolvedValue(
        createMockSettings({ access_code_set: false })
      );

      render(<VirtualPrinterSettings />);

      await waitFor(() => {
        expect(screen.getByText('No access code set - required to enable')).toBeInTheDocument();
      });
    });

    it('shows success when access code is set', async () => {
      vi.mocked(virtualPrinterApi.getSettings).mockResolvedValue(
        createMockSettings({ access_code_set: true })
      );

      render(<VirtualPrinterSettings />);

      await waitFor(() => {
        expect(screen.getByText('Access code is set')).toBeInTheDocument();
      });
    });

    it('shows character count while typing', async () => {
      const user = userEvent.setup();
      render(<VirtualPrinterSettings />);

      await waitFor(() => {
        expect(screen.getByText('Access Code')).toBeInTheDocument();
      });

      const input = screen.getByPlaceholderText('Enter 8-char code');
      await user.type(input, '1234');

      expect(screen.getByText('(4/8)')).toBeInTheDocument();
    });

    it('saves access code on button click', async () => {
      const user = userEvent.setup();
      vi.mocked(virtualPrinterApi.updateSettings).mockResolvedValue(
        createMockSettings({ access_code_set: true })
      );

      render(<VirtualPrinterSettings />);

      await waitFor(() => {
        expect(screen.getByText('Access Code')).toBeInTheDocument();
      });

      const input = screen.getByPlaceholderText('Enter 8-char code');
      await user.type(input, '12345678');

      const saveButton = screen.getByRole('button', { name: 'Save' });
      await user.click(saveButton);

      await waitFor(() => {
        expect(virtualPrinterApi.updateSettings).toHaveBeenCalledWith({
          access_code: '12345678',
        });
      });
    });

    it('toggles password visibility', async () => {
      const user = userEvent.setup();
      render(<VirtualPrinterSettings />);

      await waitFor(() => {
        expect(screen.getByText('Access Code')).toBeInTheDocument();
      });

      const input = screen.getByPlaceholderText('Enter 8-char code');
      expect(input).toHaveAttribute('type', 'password');

      // Find and click the visibility toggle (eye icon button)
      const toggleButtons = screen.getAllByRole('button');
      const visibilityToggle = toggleButtons.find(
        (btn) => btn.querySelector('svg') && btn.className.includes('absolute')
      );

      if (visibilityToggle) {
        await user.click(visibilityToggle);
        expect(input).toHaveAttribute('type', 'text');
      }
    });
  });

  describe('archive mode', () => {
    it('renders immediate mode option', async () => {
      render(<VirtualPrinterSettings />);

      await waitFor(() => {
        expect(screen.getByText('Immediate')).toBeInTheDocument();
        expect(
          screen.getByText('Archive files as soon as they are uploaded')
        ).toBeInTheDocument();
      });
    });

    it('renders queue mode option', async () => {
      render(<VirtualPrinterSettings />);

      await waitFor(() => {
        expect(screen.getByText('Queue for Review')).toBeInTheDocument();
        expect(screen.getByText('Review and tag files before archiving')).toBeInTheDocument();
      });
    });

    it('highlights current mode', async () => {
      vi.mocked(virtualPrinterApi.getSettings).mockResolvedValue(
        createMockSettings({ mode: 'queue' })
      );

      render(<VirtualPrinterSettings />);

      await waitFor(() => {
        const queueButton = screen.getByText('Queue for Review').closest('button');
        expect(queueButton?.className).toContain('border-bambu-green');
      });
    });

    it('changes mode on click', async () => {
      const user = userEvent.setup();
      vi.mocked(virtualPrinterApi.updateSettings).mockResolvedValue(
        createMockSettings({ mode: 'queue' })
      );

      render(<VirtualPrinterSettings />);

      await waitFor(() => {
        expect(screen.getByText('Queue for Review')).toBeInTheDocument();
      });

      const queueButton = screen.getByText('Queue for Review').closest('button');
      if (queueButton) {
        await user.click(queueButton);
      }

      await waitFor(() => {
        expect(virtualPrinterApi.updateSettings).toHaveBeenCalledWith({ mode: 'queue' });
      });
    });
  });

  describe('enable/disable toggle', () => {
    it('cannot enable without access code', async () => {
      const user = userEvent.setup();
      vi.mocked(virtualPrinterApi.getSettings).mockResolvedValue(
        createMockSettings({ enabled: false, access_code_set: false })
      );

      render(<VirtualPrinterSettings />);

      await waitFor(() => {
        expect(screen.getByText('Enable Virtual Printer')).toBeInTheDocument();
      });

      // Find the toggle switch (it's a button with relative class containing the slider)
      const allButtons = screen.getAllByRole('button');
      const toggle = allButtons.find((btn) => btn.className.includes('rounded-full') && btn.className.includes('w-12'));

      if (toggle) {
        await user.click(toggle);
      }

      // Should not call update API (no access code set)
      expect(virtualPrinterApi.updateSettings).not.toHaveBeenCalled();
    });

    it('can enable when access code is set', async () => {
      const user = userEvent.setup();
      vi.mocked(virtualPrinterApi.getSettings).mockResolvedValue(
        createMockSettings({ enabled: false, access_code_set: true })
      );
      vi.mocked(virtualPrinterApi.updateSettings).mockResolvedValue(
        createMockSettings({ enabled: true, access_code_set: true })
      );

      render(<VirtualPrinterSettings />);

      await waitFor(() => {
        expect(screen.getByText('Enable Virtual Printer')).toBeInTheDocument();
      });

      // Find the toggle switch (it's a button with rounded-full and w-12 classes)
      const allButtons = screen.getAllByRole('button');
      const toggle = allButtons.find((btn) => btn.className.includes('rounded-full') && btn.className.includes('w-12'));

      expect(toggle).toBeDefined();
      if (toggle) {
        await user.click(toggle);
      }

      await waitFor(() => {
        expect(virtualPrinterApi.updateSettings).toHaveBeenCalledWith(
          expect.objectContaining({ enabled: true })
        );
      });
    });

    it('can disable when enabled', async () => {
      const user = userEvent.setup();
      vi.mocked(virtualPrinterApi.getSettings).mockResolvedValue(
        createMockSettings({ enabled: true, access_code_set: true })
      );
      vi.mocked(virtualPrinterApi.updateSettings).mockResolvedValue(
        createMockSettings({ enabled: false, access_code_set: true })
      );

      render(<VirtualPrinterSettings />);

      await waitFor(() => {
        expect(screen.getByText('Enable Virtual Printer')).toBeInTheDocument();
      });

      // Find the toggle switch
      const allButtons = screen.getAllByRole('button');
      const toggle = allButtons.find((btn) => btn.className.includes('rounded-full') && btn.className.includes('w-12'));

      expect(toggle).toBeDefined();
      if (toggle) {
        await user.click(toggle);
      }

      await waitFor(() => {
        expect(virtualPrinterApi.updateSettings).toHaveBeenCalledWith(
          expect.objectContaining({ enabled: false })
        );
      });
    });
  });

  describe('info section', () => {
    it('shows setup required warning', async () => {
      render(<VirtualPrinterSettings />);

      await waitFor(() => {
        expect(screen.getByText('Setup Required')).toBeInTheDocument();
      });
    });

    it('shows link to setup guide', async () => {
      render(<VirtualPrinterSettings />);

      await waitFor(() => {
        expect(screen.getByText('Read the setup guide before enabling')).toBeInTheDocument();
      });
    });

    it('shows how it works section', async () => {
      render(<VirtualPrinterSettings />);

      await waitFor(() => {
        expect(screen.getByText('How it works:')).toBeInTheDocument();
        expect(screen.getByText(/Complete the setup guide for your platform/)).toBeInTheDocument();
      });
    });
  });
});

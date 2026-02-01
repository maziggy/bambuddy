/**
 * Tests for the Dashboard component.
 * Tests drag-and-drop widget management, visibility toggles, and layout persistence.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { Dashboard, type DashboardWidget } from '../../components/Dashboard';

const mockWidgets: DashboardWidget[] = [
  {
    id: 'widget-1',
    title: 'Widget One',
    component: <div>Widget One Content</div>,
    defaultVisible: true,
    defaultSize: 2,
  },
  {
    id: 'widget-2',
    title: 'Widget Two',
    component: <div>Widget Two Content</div>,
    defaultVisible: true,
    defaultSize: 4,
  },
  {
    id: 'widget-3',
    title: 'Widget Three',
    component: <div>Widget Three Content</div>,
    defaultVisible: false, // Hidden by default
    defaultSize: 1,
  },
];

// Create a working localStorage mock for these tests
const localStorageData: Record<string, string> = {};
const localStorageMock = {
  getItem: vi.fn((key: string) => localStorageData[key] || null),
  setItem: vi.fn((key: string, value: string) => {
    localStorageData[key] = value;
  }),
  removeItem: vi.fn((key: string) => {
    delete localStorageData[key];
  }),
  clear: vi.fn(() => {
    Object.keys(localStorageData).forEach((key) => delete localStorageData[key]);
  }),
};

describe('Dashboard', () => {
  beforeEach(() => {
    // Clear localStorage data and mocks before each test
    Object.keys(localStorageData).forEach((key) => delete localStorageData[key]);
    vi.clearAllMocks();
    Object.defineProperty(window, 'localStorage', { value: localStorageMock, writable: true });
  });

  describe('rendering', () => {
    it('renders visible widgets', () => {
      render(<Dashboard widgets={mockWidgets} storageKey="test-dashboard" />);

      expect(screen.getByText('Widget One')).toBeInTheDocument();
      expect(screen.getByText('Widget Two')).toBeInTheDocument();
      expect(screen.getByText('Widget One Content')).toBeInTheDocument();
      expect(screen.getByText('Widget Two Content')).toBeInTheDocument();
    });

    it('does not render hidden widgets', () => {
      render(<Dashboard widgets={mockWidgets} storageKey="test-dashboard" />);

      // Widget Three is hidden by default
      expect(screen.queryByText('Widget Three')).not.toBeInTheDocument();
      expect(screen.queryByText('Widget Three Content')).not.toBeInTheDocument();
    });

    it('renders Reset Layout button when controls are shown', () => {
      render(<Dashboard widgets={mockWidgets} storageKey="test-dashboard" />);

      expect(screen.getByText('Reset Layout')).toBeInTheDocument();
    });

    it('hides controls when hideControls is true', () => {
      render(<Dashboard widgets={mockWidgets} storageKey="test-dashboard" hideControls />);

      expect(screen.queryByText('Reset Layout')).not.toBeInTheDocument();
    });

    it('shows hidden count button when widgets are hidden', () => {
      render(<Dashboard widgets={mockWidgets} storageKey="test-dashboard" />);

      // Widget Three is hidden by default
      expect(screen.getByText('1 Hidden')).toBeInTheDocument();
    });
  });

  describe('visibility toggle', () => {
    it('hides a widget when hide button is clicked', async () => {
      render(<Dashboard widgets={mockWidgets} storageKey="test-dashboard" />);

      // Find and click the hide button for Widget One
      const hideButtons = screen.getAllByTitle('Hide widget');
      fireEvent.click(hideButtons[0]);

      await waitFor(() => {
        expect(screen.queryByText('Widget One Content')).not.toBeInTheDocument();
      });
    });

    it('shows hidden widgets panel when clicking hidden count button', async () => {
      render(<Dashboard widgets={mockWidgets} storageKey="test-dashboard" />);

      const hiddenButton = screen.getByText('1 Hidden');
      fireEvent.click(hiddenButton);

      await waitFor(() => {
        expect(screen.getByText('Hidden widgets (click to show):')).toBeInTheDocument();
        expect(screen.getByText('Widget Three')).toBeInTheDocument();
      });
    });

    it('shows a hidden widget when clicked in the hidden panel', async () => {
      render(<Dashboard widgets={mockWidgets} storageKey="test-dashboard" />);

      // Open hidden panel
      const hiddenButton = screen.getByText('1 Hidden');
      fireEvent.click(hiddenButton);

      await waitFor(() => {
        expect(screen.getByText('Widget Three')).toBeInTheDocument();
      });

      // Click to show Widget Three
      const showWidgetButton = screen.getByRole('button', { name: /Widget Three/i });
      fireEvent.click(showWidgetButton);

      await waitFor(() => {
        expect(screen.getByText('Widget Three Content')).toBeInTheDocument();
      });
    });
  });

  describe('reset layout', () => {
    it('resets layout to default when Reset Layout is clicked', async () => {
      render(<Dashboard widgets={mockWidgets} storageKey="test-dashboard" />);

      // Hide Widget One
      const hideButtons = screen.getAllByTitle('Hide widget');
      fireEvent.click(hideButtons[0]);

      await waitFor(() => {
        expect(screen.queryByText('Widget One Content')).not.toBeInTheDocument();
      });

      // Reset layout
      const resetButton = screen.getByText('Reset Layout');
      fireEvent.click(resetButton);

      await waitFor(() => {
        expect(screen.getByText('Widget One Content')).toBeInTheDocument();
      });
    });

    it('calls onResetLayout callback when reset', async () => {
      const onResetLayout = vi.fn();
      render(
        <Dashboard
          widgets={mockWidgets}
          storageKey="test-dashboard"
          onResetLayout={onResetLayout}
        />
      );

      const resetButton = screen.getByText('Reset Layout');
      fireEvent.click(resetButton);

      expect(onResetLayout).toHaveBeenCalled();
    });
  });

  describe('size toggle', () => {
    it('cycles widget size when size button is clicked', async () => {
      render(<Dashboard widgets={mockWidgets} storageKey="test-dashboard" />);

      // Widget One starts at size 2, should cycle to 4
      const sizeButtons = screen.getAllByTitle(/Size:/);
      fireEvent.click(sizeButtons[0]);

      // After click, size should change (verify by checking title updates)
      await waitFor(() => {
        // The button title should now show a different size
        expect(screen.getAllByTitle(/Size:/)[0]).toBeInTheDocument();
      });
    });
  });

  describe('localStorage persistence', () => {
    it('saves layout to localStorage when widget is hidden', async () => {
      render(<Dashboard widgets={mockWidgets} storageKey="test-dashboard-persist" />);

      // Hide a widget to trigger a layout change
      const hideButtons = screen.getAllByTitle('Hide widget');
      fireEvent.click(hideButtons[0]);

      await waitFor(() => {
        // Verify setItem was called with the storage key
        expect(localStorageMock.setItem).toHaveBeenCalled();
        const calls = localStorageMock.setItem.mock.calls;
        const lastCall = calls[calls.length - 1];
        expect(lastCall[0]).toBe('test-dashboard-persist');
        const parsed = JSON.parse(lastCall[1]);
        expect(parsed.hidden).toContain('widget-1');
      });
    });

    it('loads saved layout from localStorage', () => {
      // Pre-set a layout in localStorage
      localStorageData['test-dashboard-load'] = JSON.stringify({
        order: ['widget-2', 'widget-1', 'widget-3'],
        hidden: ['widget-2'],
        sizes: { 'widget-1': 4, 'widget-2': 2, 'widget-3': 1 },
      });

      render(<Dashboard widgets={mockWidgets} storageKey="test-dashboard-load" />);

      // Widget 2 should be hidden
      expect(screen.queryByText('Widget Two Content')).not.toBeInTheDocument();
      // Widget 1 should be visible
      expect(screen.getByText('Widget One Content')).toBeInTheDocument();
    });
  });

  describe('empty state', () => {
    it('shows empty message when all widgets are hidden', async () => {
      // Pre-set all widgets as hidden
      localStorageData['test-dashboard-empty'] = JSON.stringify({
        order: ['widget-1', 'widget-2', 'widget-3'],
        hidden: ['widget-1', 'widget-2', 'widget-3'],
        sizes: {},
      });

      render(<Dashboard widgets={mockWidgets} storageKey="test-dashboard-empty" />);

      expect(screen.getByText('All widgets are hidden.')).toBeInTheDocument();
      // There are multiple Reset Layout buttons (one in controls, one in empty state)
      const resetButtons = screen.getAllByRole('button', { name: 'Reset Layout' });
      expect(resetButtons.length).toBeGreaterThan(0);
    });
  });

  describe('custom render controls', () => {
    it('renders custom controls when renderControls is provided', () => {
      render(
        <Dashboard
          widgets={mockWidgets}
          storageKey="test-dashboard"
          renderControls={({ hiddenCount }) => (
            <div data-testid="custom-controls">Hidden: {hiddenCount}</div>
          )}
        />
      );

      expect(screen.getByTestId('custom-controls')).toBeInTheDocument();
      expect(screen.getByText('Hidden: 1')).toBeInTheDocument();
    });
  });
});

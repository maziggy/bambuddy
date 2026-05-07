/**
 * Tests for the FilamentHoverCard component.
 * Focuses on fill level display and Spoolman source indicator.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '../utils';
import { FilamentHoverCard, EmptySlotHoverCard } from '../../components/FilamentHoverCard';

const baseFilamentData = {
  vendor: 'Bambu Lab' as const,
  profile: 'PLA Basic',
  colorName: 'Red',
  colorHex: 'FF0000',
  kFactor: '0.030',
  fillLevel: 75,
  trayUuid: 'A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4',
};

function renderWithHover(ui: React.ReactElement) {
  const result = render(ui);
  // Trigger hover to show the card
  const trigger = result.container.firstElementChild as HTMLElement;
  fireEvent.mouseEnter(trigger);
  return result;
}

describe('FilamentHoverCard', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  describe('fill level display', () => {
    it('shows fill percentage when fillLevel is set', async () => {
      renderWithHover(
        <FilamentHoverCard data={{ ...baseFilamentData, fillLevel: 75 }}>
          <div>trigger</div>
        </FilamentHoverCard>
      );

      vi.advanceTimersByTime(100);

      await waitFor(() => {
        expect(screen.getByText('75%')).toBeInTheDocument();
      });
    });

    it('shows dash when fillLevel is null', async () => {
      renderWithHover(
        <FilamentHoverCard data={{ ...baseFilamentData, fillLevel: null }}>
          <div>trigger</div>
        </FilamentHoverCard>
      );

      vi.advanceTimersByTime(100);

      await waitFor(() => {
        expect(screen.getByText('—')).toBeInTheDocument();
      });
    });

    it('shows 0% when fillLevel is zero', async () => {
      renderWithHover(
        <FilamentHoverCard data={{ ...baseFilamentData, fillLevel: 0 }}>
          <div>trigger</div>
        </FilamentHoverCard>
      );

      vi.advanceTimersByTime(100);

      await waitFor(() => {
        expect(screen.getByText('0%')).toBeInTheDocument();
      });
    });
  });

  describe('Spoolman source indicator', () => {
    it('shows Spoolman label when fillSource is spoolman', async () => {
      renderWithHover(
        <FilamentHoverCard data={{ ...baseFilamentData, fillLevel: 80, fillSource: 'spoolman' }}>
          <div>trigger</div>
        </FilamentHoverCard>
      );

      vi.advanceTimersByTime(100);

      await waitFor(() => {
        expect(screen.getByText('(Spoolman)')).toBeInTheDocument();
      });
    });

    it('does not show Spoolman label when fillSource is ams', async () => {
      renderWithHover(
        <FilamentHoverCard data={{ ...baseFilamentData, fillLevel: 80, fillSource: 'ams' }}>
          <div>trigger</div>
        </FilamentHoverCard>
      );

      vi.advanceTimersByTime(100);

      await waitFor(() => {
        expect(screen.getByText('80%')).toBeInTheDocument();
        expect(screen.queryByText('(Spoolman)')).not.toBeInTheDocument();
      });
    });

    it('does not show Spoolman label when fillLevel is null', async () => {
      renderWithHover(
        <FilamentHoverCard data={{ ...baseFilamentData, fillLevel: null, fillSource: 'spoolman' }}>
          <div>trigger</div>
        </FilamentHoverCard>
      );

      vi.advanceTimersByTime(100);

      await waitFor(() => {
        expect(screen.getByText('—')).toBeInTheDocument();
        expect(screen.queryByText('(Spoolman)')).not.toBeInTheDocument();
      });
    });

    it('does not show Spoolman label when fillSource is undefined', async () => {
      renderWithHover(
        <FilamentHoverCard data={{ ...baseFilamentData, fillLevel: 50 }}>
          <div>trigger</div>
        </FilamentHoverCard>
      );

      vi.advanceTimersByTime(100);

      await waitFor(() => {
        expect(screen.getByText('50%')).toBeInTheDocument();
        expect(screen.queryByText('(Spoolman)')).not.toBeInTheDocument();
      });
    });
  });

  describe('hover behavior', () => {
    it('does not show card when disabled', () => {
      renderWithHover(
        <FilamentHoverCard data={baseFilamentData} disabled>
          <div>trigger</div>
        </FilamentHoverCard>
      );

      vi.advanceTimersByTime(100);

      // Card should not be visible
      expect(screen.queryByText('PLA Basic')).not.toBeInTheDocument();
    });

    it('shows filament details on hover', async () => {
      renderWithHover(
        <FilamentHoverCard data={baseFilamentData}>
          <div>trigger</div>
        </FilamentHoverCard>
      );

      vi.advanceTimersByTime(100);

      await waitFor(() => {
        expect(screen.getByText('Red')).toBeInTheDocument();
        expect(screen.getByText('PLA Basic')).toBeInTheDocument();
        expect(screen.getByText('0.030')).toBeInTheDocument();
      });
    });
  });

  // The inventory section was previously hidden for `vendor === 'Bambu Lab'`
  // because BL spools were assumed to be managed entirely via RFID. #1133
  // removed that gate so users who don't want to scan via SpoolBuddy NFC
  // can still pick a BL spool from inventory the same way they pick a
  // third-party one.
  describe('inventory section vendor visibility (#1133)', () => {
    it('shows the assign-spool button on a Bambu Lab slot when the spool is unassigned', async () => {
      const onAssign = vi.fn();
      renderWithHover(
        <FilamentHoverCard
          data={{ ...baseFilamentData, vendor: 'Bambu Lab' }}
          inventory={{ assignedSpool: null, onAssignSpool: onAssign }}
        >
          <div>trigger</div>
        </FilamentHoverCard>
      );
      vi.advanceTimersByTime(100);
      await waitFor(() => {
        expect(screen.getByText(/assign/i)).toBeInTheDocument();
      });
    });

    it('shows the unassign button on a Bambu Lab slot when an inventory spool is already assigned', async () => {
      // Regression guard: the original gate hid BOTH the assign and unassign
      // buttons for BL slots. A user who'd already assigned an inventory
      // spool to a BL slot couldn't undo it without dropping into the
      // inventory page directly.
      const onUnassign = vi.fn();
      renderWithHover(
        <FilamentHoverCard
          data={{ ...baseFilamentData, vendor: 'Bambu Lab' }}
          inventory={{
            assignedSpool: {
              id: 1,
              material: 'PLA',
              brand: 'Devil Design',
              color_name: 'Black',
            },
            onUnassignSpool: onUnassign,
          }}
        >
          <div>trigger</div>
        </FilamentHoverCard>
      );
      vi.advanceTimersByTime(100);
      await waitFor(() => {
        expect(screen.getByText(/unassign/i)).toBeInTheDocument();
      });
    });

    it('still shows the assign-spool button for a non-Bambu vendor (no behaviour change)', async () => {
      const onAssign = vi.fn();
      renderWithHover(
        <FilamentHoverCard
          data={{ ...baseFilamentData, vendor: 'Polymaker' as unknown as 'Bambu Lab' }}
          inventory={{ assignedSpool: null, onAssignSpool: onAssign }}
        >
          <div>trigger</div>
        </FilamentHoverCard>
      );
      vi.advanceTimersByTime(100);
      await waitFor(() => {
        expect(screen.getByText(/assign/i)).toBeInTheDocument();
      });
    });
  });
});

// EmptySlotHoverCard is the hover wrapper rendered for a physically empty
// AMS slot. #1133 removed its inventory affordance: a slot with nothing
// loaded has no spool to attach an inventory record to, and offering the
// action there only led to users assigning the wrong spool to a slot the
// printer hadn't actually loaded yet. The configure-slot affordance is
// kept, since "preset for the next spool to land here" is still a sensible
// thing to do on an empty slot.
describe('EmptySlotHoverCard (#1133)', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  it('does not render an assign-spool affordance', async () => {
    const result = render(
      <EmptySlotHoverCard configureSlot={{ enabled: true, onConfigure: vi.fn() }}>
        <div>trigger</div>
      </EmptySlotHoverCard>
    );
    fireEvent.mouseEnter(result.container.firstElementChild as HTMLElement);
    vi.advanceTimersByTime(100);
    await waitFor(() => {
      // The card itself is showing — guard the negative assertion against
      // a card that simply never opened.
      expect(screen.getByText(/empty/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/assign/i)).not.toBeInTheDocument();
  });

  it('still shows the configure button on an empty slot', async () => {
    const onConfigure = vi.fn();
    const result = render(
      <EmptySlotHoverCard configureSlot={{ enabled: true, onConfigure }}>
        <div>trigger</div>
      </EmptySlotHoverCard>
    );
    fireEvent.mouseEnter(result.container.firstElementChild as HTMLElement);
    vi.advanceTimersByTime(100);
    await waitFor(() => {
      expect(screen.getByText(/configure/i)).toBeInTheDocument();
    });
  });
});

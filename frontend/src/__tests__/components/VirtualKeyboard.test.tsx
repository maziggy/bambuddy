/**
 * Regression for #2616. react-simple-keyboard ships as CommonJS; under the
 * bundler's CJS interop the default import can arrive as the module namespace
 * object rather than the Keyboard component, so rendering <Keyboard> throws
 * React #130 ("Element type is invalid ... got: object"). The on-screen keyboard
 * mounts on every SpoolBuddy screen the instant a text input is focused, so the
 * crash hit inventory search and the write-tag New Spool fields alike.
 */

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { VirtualKeyboard } from '../../components/VirtualKeyboard';

// focusin schedules a 100ms scrollIntoView on the focused input; jsdom doesn't
// implement it, so stub it or the deferred call throws an unhandled error after
// the test completes.
beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('VirtualKeyboard (#2616)', () => {
  it('renders the keyboard when a text input is focused (no invalid-element-type crash)', () => {
    render(
      <div>
        <input type="text" placeholder="Search spools..." />
        <VirtualKeyboard />
      </div>,
    );

    const input = screen.getByPlaceholderText('Search spools...');
    // The shell listens on document focusin, so drive a real focus event.
    fireEvent.focusIn(input);

    // A key from the layout must be on screen — proves <Keyboard> resolved to a
    // real component instead of throwing on an object element type.
    expect(screen.getByText('q')).toBeInTheDocument();
  });
});

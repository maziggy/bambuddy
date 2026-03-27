import { useEffect, useRef, useState, useCallback } from 'react';
import Keyboard from 'react-simple-keyboard';
import 'react-simple-keyboard/build/css/index.css';
import './VirtualKeyboard.css';

const FOCUSABLE_TYPES = new Set(['text', 'password', 'email', 'search', 'url', 'number']);

/**
 * Set value on a controlled React input using the native setter,
 * then dispatch an input event so React picks up the change.
 */
function setNativeValue(input: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const setter =
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set ??
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

export function VirtualKeyboard({ onVisibilityChange }: { onVisibilityChange?: (visible: boolean) => void }) {
  const [visible, setVisible] = useState(false);
  const [closing, setClosing] = useState(false);
  const closingRef = useRef(false);
  const [layoutName, setLayoutName] = useState('default');
  const activeInput = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);
  const keyboardRef = useRef<ReturnType<typeof Keyboard> | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Notify parent when keyboard visibility changes
  useEffect(() => {
    onVisibilityChange?.(visible);
  }, [visible, onVisibilityChange]);

  const handleFocusIn = useCallback((e: FocusEvent) => {
    if (closingRef.current) return;
    const target = e.target as HTMLElement;

    // Skip inputs that opt out (e.g. SpoolBuddySettingsPage numpad field)
    if (target.closest('[data-vkb="false"]')) return;

    if (target instanceof HTMLInputElement) {
      if (!FOCUSABLE_TYPES.has(target.type)) return;
    } else if (!(target instanceof HTMLTextAreaElement)) {
      return;
    }

    activeInput.current = target as HTMLInputElement | HTMLTextAreaElement;
    setVisible(true);
    setLayoutName('default');

    // Sync keyboard display with current value
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (keyboardRef.current as any)?.setInput?.(activeInput.current.value);

    // Scroll focused input into view after the keyboard renders and layout reflows
    setTimeout(() => {
      const card = target.closest('.bg-zinc-800, .rounded-lg, [data-vkb-group]') as HTMLElement | null;
      (card ?? target).scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }, 100);
  }, []);

  const handleFocusOut = useCallback(() => {
    // Delay to allow click on keyboard buttons to register
    setTimeout(() => {
      const active = document.activeElement;
      // Keep visible if focus moved to keyboard or back to same input
      if (
        active &&
        (containerRef.current?.contains(active) || active === activeInput.current)
      ) {
        return;
      }
      setVisible(false);
      activeInput.current = null;
    }, 150);
  }, []);

  useEffect(() => {
    document.addEventListener('focusin', handleFocusIn);
    document.addEventListener('focusout', handleFocusOut);
    return () => {
      document.removeEventListener('focusin', handleFocusIn);
      document.removeEventListener('focusout', handleFocusOut);
    };
  }, [handleFocusIn, handleFocusOut]);

  // Two-phase close: hide the keyboard immediately but keep the backdrop
  // alive for 400ms to absorb the ghost click that touch devices synthesize.
  const dismiss = useCallback(() => {
    closingRef.current = true;
    setClosing(true);
    activeInput.current?.blur();
    activeInput.current = null;
    setTimeout(() => {
      setVisible(false);
      setClosing(false);
      closingRef.current = false;
    }, 400);
  }, []);

  const onKeyPress = useCallback((button: string) => {
    const input = activeInput.current;
    if (!input) return;

    if (button === '{shift}') {
      setLayoutName(prev => prev === 'default' ? 'shift' : 'default');
      return;
    }
    if (button === '{lock}') {
      setLayoutName(prev => prev === 'default' ? 'shift' : 'default');
      return;
    }
    if (button === '{close}') {
      dismiss();
      return;
    }
    if (button === '{bksp}') {
      setNativeValue(input, input.value.slice(0, -1));
    } else if (button === '{space}') {
      setNativeValue(input, input.value + ' ');
    } else {
      setNativeValue(input, input.value + button);
      // Auto-unshift after typing one character (like mobile keyboards)
      if (layoutName === 'shift') {
        setLayoutName('default');
      }
    }

    // Keep focus on the input
    input.focus();
    // Sync keyboard internal state
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (keyboardRef.current as any)?.setInput?.(input.value);
  }, [layoutName, dismiss]);

  if (!visible) return null;

  return (
    <>
      {/* Backdrop: absorbs taps so they don't reach elements under the keyboard.
          Stays alive during closing phase to catch ghost clicks. */}
      <div
        className="fixed inset-0 z-[9998] bg-transparent"
        onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); if (!closing) dismiss(); }}
        onTouchStart={(e) => { e.preventDefault(); e.stopPropagation(); if (!closing) dismiss(); }}
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); }}
      />
      {!closing && (
      <div
        ref={containerRef}
        className="relative z-[9999] shrink-0"
        onMouseDown={(e) => e.preventDefault()}
        onTouchStart={(e) => {
          // Prevent focus loss but allow button interaction
          if (!(e.target as HTMLElement).closest('.hg-button')) {
            e.preventDefault();
          }
        }}
      >
        <Keyboard
        keyboardRef={(r: ReturnType<typeof Keyboard>) => { keyboardRef.current = r; }}
        layoutName={layoutName}
        onKeyPress={onKeyPress}
        theme="simple-keyboard vkb-theme"
        layout={{
          default: [
            '1 2 3 4 5 6 7 8 9 0 {bksp}',
            'q w e r t y u i o p',
            '{lock} a s d f g h j k l',
            '{shift} z x c v b n m . @',
            '{space} {close}',
          ],
          shift: [
            '! @ # $ % ^ & * ( ) {bksp}',
            'Q W E R T Y U I O P',
            '{lock} A S D F G H J K L',
            '{shift} Z X C V B N M , _',
            '{space} {close}',
          ],
        }}
        display={{
          '{bksp}': '\u232B',
          '{close}': '\u2715 Close',
          '{shift}': '\u21E7',
          '{lock}': '\u21EA',
          '{space}': ' ',
        }}
      />
      </div>
      )}
    </>
  );
}

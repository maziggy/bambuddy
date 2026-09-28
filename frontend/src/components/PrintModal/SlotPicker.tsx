import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ChevronDown, Check } from 'lucide-react';
import { FilamentSwatch } from '../FilamentSwatch';

/**
 * One-line filament picker that can show a colour, which a `<select>` cannot.
 *
 * The Print / Schedule dialog is where the slicer's colour is compared against
 * what is actually in the machine, and until now the right-hand side of that
 * comparison was text only (#3159). Colour names are not a substitute: they are
 * resolved from the Color Catalog or from hue, so a third-party beige reads as
 * "Orange" and a "Color mismatch" warning gives no way to tell a real mismatch
 * from two names for the same hex. `<option>` renders text and nothing else, so
 * the swatch means this cannot be a native select.
 *
 * What is kept from the select it replaces: full keyboard operation (arrows,
 * Home/End, Enter, Escape), `role="listbox"` semantics, and the caller's own
 * border colouring, which encodes match / type-only / mismatch on the mapping
 * row and overridden / original on the override row.
 *
 * Deliberately not a shared "Select": the rows are a swatch, a name, a hex and
 * a badge, and this is the second such control in the app after
 * `spool-form/PresetPicker` — which documents the same reasoning. A shared
 * abstraction over two call sites with different row shapes would be guessing
 * at the third.
 */

export interface SlotPickerOption {
  /** The value handed back to `onChange`; mirrors the old `<option value>`. */
  value: string;
  /** Main text of the row. */
  label: string;
  /** RRGGBBAA (or RRGGBB) without `#`. Absent renders no swatch at all rather
   *  than a grey one, so "no colour known" never looks like a grey spool. */
  rgba?: string | null;
  /** Comma-separated extra hex stops for a multi-colour spool. */
  extraColors?: string | null;
  /** Effect overlay name, as `FilamentSwatch` understands it. */
  effectType?: string | null;
  /** Filament subtype; `Multicolor` renders the stops as a conic sweep. */
  subtype?: string | null;
  /** Muted suffix after the label — remaining grams, FTS inlet badge. */
  meta?: string;
  /** Shown in the dropdown row so two similar colours can be told apart by
   *  their hex rather than by two names that resolved the same way. */
  hex?: string | null;
  /** Marks the row whose colour is exactly the one the slice asked for. */
  exactMatch?: boolean;
  /** React key, when `value` is not unique. Model-mode pools filaments across
   *  every printer of a model, so two spools of the same type and colour
   *  collapse to one `TYPE|COLOR` value while still being two rows. */
  key?: string;
}

interface SlotPickerProps {
  /** Currently chosen option value, or '' for the placeholder row. */
  value: string;
  options: SlotPickerOption[];
  /** What the '' row reads as. */
  placeholder: string;
  /** Colour for the '' row, when it has one (the override panel's '' row is
   *  the 3MF's own filament, which does have a colour). */
  placeholderOption?: Omit<SlotPickerOption, 'value' | 'label'>;
  onChange: (value: string) => void;
  disabled?: boolean;
  ariaLabel: string;
  /** Native tooltip on the trigger. */
  title?: string;
  /** Border / text classes the caller uses to encode status. */
  className?: string;
  /** Tooltip for the exact-match marker. */
  exactMatchLabel?: string;
}

const SWATCH_CLASS = 'w-3 h-3';

export function SlotPicker({
  value,
  options,
  placeholder,
  placeholderOption,
  onChange,
  disabled = false,
  ariaLabel,
  title,
  className = '',
  exactMatchLabel,
}: SlotPickerProps) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [coords, setCoords] = useState<{ top: number; left: number; width: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // The placeholder is row 0, so an index addresses both it and the options
  // with one number — which is what the arrow keys and aria-activedescendant
  // need.
  const rows: SlotPickerOption[] = [
    { ...(placeholderOption ?? {}), value: '', label: placeholder },
    ...options,
  ];
  const selectedIndex = Math.max(
    0,
    rows.findIndex((r) => r.value === value),
  );
  const selected = rows[selectedIndex];

  const place = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    // Flip above when the row sits low enough that a downward list would run
    // off the viewport. The dialog body scrolls, so a mapping row near its
    // bottom is the ordinary case, not the edge one.
    const below = window.innerHeight - rect.bottom;
    const height = Math.min(listRef.current?.scrollHeight ?? 0, 240);
    const flip = below < height + 8 && rect.top > below;
    setCoords({
      top: flip ? Math.max(8, rect.top - height - 4) : rect.bottom + 4,
      left: rect.left,
      width: rect.width,
    });
  }, []);

  useLayoutEffect(() => {
    if (open) place();
  }, [open, place]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (triggerRef.current?.contains(target) || listRef.current?.contains(target)) return;
      setOpen(false);
    };
    // Escape closes this control WITHOUT closing the dialog around it, which a
    // bare document listener would otherwise let happen — same guard
    // `PresetPicker` documents.
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.stopPropagation();
      setOpen(false);
      triggerRef.current?.focus();
    };
    // The list is portaled to <body>, so it does not travel with the dialog's
    // own scroll container: follow it rather than leaving it behind.
    const reposition = () => place();
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown, true);
    window.addEventListener('scroll', reposition, true);
    window.addEventListener('resize', reposition);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown, true);
      window.removeEventListener('scroll', reposition, true);
      window.removeEventListener('resize', reposition);
    };
  }, [open, place]);

  const choose = (index: number) => {
    onChange(rows[index].value);
    setOpen(false);
    triggerRef.current?.focus();
  };

  const openAt = (index: number) => {
    setActiveIndex(index);
    setOpen(true);
  };

  const handleTriggerKey = (e: React.KeyboardEvent) => {
    if (disabled) return;
    if (!open) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openAt(selectedIndex);
      }
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex((i) => Math.min(rows.length - 1, i + 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex((i) => Math.max(0, i - 1));
    } else if (e.key === 'Home') {
      e.preventDefault();
      setActiveIndex(0);
    } else if (e.key === 'End') {
      e.preventDefault();
      setActiveIndex(rows.length - 1);
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      choose(activeIndex);
    } else if (e.key === 'Tab') {
      setOpen(false);
    }
  };

  const listId = `slot-picker-${ariaLabel.replace(/\W+/g, '-')}`;

  const renderSwatch = (row: SlotPickerOption) =>
    row.rgba ? (
      <FilamentSwatch
        rgba={row.rgba}
        extraColors={row.extraColors}
        effectType={row.effectType}
        subtype={row.subtype}
        className={SWATCH_CLASS}
        effectSize="table"
      />
    ) : null;

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        role="combobox"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        // On the combobox, not on the listbox: focus stays on the trigger
        // while the list is open, and this is what tells a screen reader which
        // row the arrow keys are on.
        aria-activedescendant={open ? `${listId}-${activeIndex}` : undefined}
        disabled={disabled}
        title={title}
        onClick={() => (open ? setOpen(false) : openAt(selectedIndex))}
        onKeyDown={handleTriggerKey}
        className={`flex-1 min-w-0 flex items-center gap-1.5 px-2 py-1 rounded border text-xs bg-bambu-dark-secondary text-left focus:outline-none focus:ring-1 focus:ring-bambu-green disabled:opacity-50 ${className}`}
      >
        {renderSwatch(selected)}
        <span className="flex-1 min-w-0 truncate">
          {selected.label}
          {selected.meta ? <span className="text-bambu-gray">{selected.meta}</span> : null}
        </span>
        <ChevronDown className="w-3 h-3 shrink-0 opacity-70" />
      </button>

      {open &&
        createPortal(
          <div
            ref={listRef}
            id={listId}
            role="listbox"
            aria-label={ariaLabel}
            className="fixed z-[60] max-h-60 overflow-y-auto rounded-lg border border-bambu-dark-tertiary bg-bambu-dark-secondary p-1 shadow-xl"
            style={{
              top: coords?.top ?? -9999,
              left: coords?.left ?? -9999,
              width: coords?.width,
              // Hide until placed, so there is no flash at (-9999,-9999) —
              // same trick FilamentHoverCard uses.
              visibility: coords ? 'visible' : 'hidden',
            }}
          >
            {rows.map((row, index) => (
              <button
                key={row.key ?? `${row.value}-${index}`}
                id={`${listId}-${index}`}
                type="button"
                role="option"
                // What `<option value>` carried before this was a listbox: the
                // identity of the choice, separate from how it reads.
                data-value={row.value}
                aria-selected={index === selectedIndex}
                onMouseEnter={() => setActiveIndex(index)}
                onClick={() => choose(index)}
                className={`w-full flex items-center gap-1.5 text-left px-2 py-1.5 rounded-md text-xs ${
                  index === selectedIndex
                    ? 'bg-bambu-green/15 text-bambu-green'
                    : index === activeIndex
                      ? 'bg-bambu-dark text-white'
                      : 'text-white'
                }`}
              >
                {renderSwatch(row)}
                <span className="flex-1 min-w-0 truncate" title={row.label}>
                  {row.label}
                  {row.meta ? <span className="text-bambu-gray">{row.meta}</span> : null}
                </span>
                {row.hex ? (
                  <span className="shrink-0 font-mono text-[10px] uppercase text-bambu-gray">{row.hex}</span>
                ) : null}
                {row.exactMatch ? (
                  <span title={exactMatchLabel} className="shrink-0 text-bambu-green">
                    <Check className="w-3 h-3" />
                  </span>
                ) : null}
              </button>
            ))}
          </div>,
          document.body,
        )}
    </>
  );
}

export default SlotPicker;

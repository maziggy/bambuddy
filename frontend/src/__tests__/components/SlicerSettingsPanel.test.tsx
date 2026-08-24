import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';

import { render } from '../utils';
import SlicerSettingsPanel, { type FilamentChoice } from '../../components/SlicerSettingsPanel';
import type { SettingValue } from '../../types/slicerSettings';
import type { DesignOverride } from '../../types/plates';
import type { SlicerPresetValuesReason } from '../../api/client';

/**
 * The panel is a controlled component: it renders from the `values` prop and
 * reports edits upward. Driving it with a bare spy would leave every input
 * frozen at its initial value, so the harness holds state the way SliceModal
 * does and forwards each call to the spy for assertions.
 */
function Harness({
  initial,
  onChange,
  sourceOverrides,
  initialSelected,
  filamentChoices,
  presetValues,
  presetValuesResolved,
  presetValuesReason,
}: {
  initial: Record<string, SettingValue>;
  onChange: (v: Record<string, SettingValue>, s: Record<string, string | string[]>) => void;
  sourceOverrides?: DesignOverride[];
  initialSelected?: string[];
  filamentChoices?: FilamentChoice[];
  presetValues?: Record<string, SettingValue>;
  presetValuesResolved?: boolean;
  presetValuesReason?: SlicerPresetValuesReason;
}) {
  const [values, setValues] = useState(initial);
  const [selected, setSelected] = useState(new Set(initialSelected ?? []));
  return (
    <SlicerSettingsPanel
      values={values}
      onChange={(v, s) => {
        setValues(v);
        onChange(v, s);
      }}
      filamentChoices={filamentChoices}
      presetValues={presetValues}
      presetValuesResolved={presetValuesResolved}
      presetValuesReason={presetValuesReason}
      sourceOverrides={sourceOverrides}
      sourceSelected={selected}
      onToggleSource={(key, on) =>
        setSelected((prev) => {
          const next = new Set(prev);
          if (on) next.add(key);
          else next.delete(key);
          return next;
        })
      }
    />
  );
}

/** Renders the panel and waits for its dynamically imported metadata. */
async function renderPanel(
  initial: Record<string, SettingValue> = {},
  extra: {
    sourceOverrides?: DesignOverride[];
    initialSelected?: string[];
    filamentChoices?: FilamentChoice[];
    presetValues?: Record<string, SettingValue>;
    presetValuesResolved?: boolean;
    presetValuesReason?: SlicerPresetValuesReason;
  } = {},
) {
  const onChange = vi.fn();
  render(<Harness initial={initial} onChange={onChange} {...extra} />);
  await waitFor(() => expect(screen.getByPlaceholderText('Search settings')).toBeInTheDocument());
  return { onChange };
}

/**
 * Brings one option on screen regardless of which page or visibility tier it
 * belongs to. Searching spans every page, which is how a user would reach a
 * setting they know the name of.
 */
async function showOption(user: ReturnType<typeof userEvent.setup>, label: string, search: string) {
  await user.click(screen.getByRole('button', { name: 'Expert' }));
  const box = screen.getByPlaceholderText('Search settings');
  await user.clear(box);
  await user.type(box, search);
  return waitFor(() => screen.getByLabelText(new RegExp(`^${label}`)));
}

describe('SlicerSettingsPanel', () => {
  it('opens on the first page of the slicer parameter tree', async () => {
    await renderPanel();
    expect(screen.getByRole('button', { name: 'Quality' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Strength' })).toBeInTheDocument();
    expect(screen.getByLabelText(/^Layer height/)).toBeInTheDocument();
  });

  it('reveals more options as the visibility tier widens', async () => {
    const user = userEvent.setup();
    await renderPanel();

    // "Slice gap closing radius" is an advanced-tier Quality option.
    expect(screen.queryByLabelText(/^Slice gap closing radius/)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Advanced' }));
    await waitFor(() => expect(screen.getByLabelText(/^Slice gap closing radius/)).toBeInTheDocument());
  });

  it('searches across every page rather than only the open one', async () => {
    const user = userEvent.setup();
    await renderPanel();

    // Enable support lives on the Support page, not the Quality page shown.
    await user.type(screen.getByPlaceholderText('Search settings'), 'enable support');
    await waitFor(() => expect(screen.getByLabelText(/^Enable support/)).toBeInTheDocument());
  });

  it('reports an edit serialised the way a process preset stores it', async () => {
    const user = userEvent.setup();
    const { onChange } = await renderPanel();

    const input = screen.getByLabelText(/^Layer height/);
    await user.clear(input);
    await user.type(input, '0.16');

    await waitFor(() => {
      const [values, serialized] = onChange.mock.calls.at(-1)!;
      expect(values.layer_height).toBe('0.16');
      expect(serialized.layer_height).toBe('0.16');
    });
  });

  it('puts the percent sign back on a percent option', async () => {
    const user = userEvent.setup();
    const { onChange } = await renderPanel();

    const input = await showOption(user, 'Sparse infill density', 'sparse infill density');
    await user.clear(input);
    await user.type(input, '35');

    // "35" and "35%" are different values to the slicer; the schema decides.
    await waitFor(() => {
      const [, serialized] = onChange.mock.calls.at(-1)!;
      expect(serialized.sparse_infill_density).toBe('35%');
    });
  });

  it('sends nothing for a value that equals the preset default', async () => {
    const user = userEvent.setup();
    const { onChange } = await renderPanel();

    // wall_loops defaults to 2 — typing it back is not an override.
    const input = await showOption(user, 'Wall loops', 'wall loops');
    await user.clear(input);
    await user.type(input, '2');

    await waitFor(() => {
      const [values, serialized] = onChange.mock.calls.at(-1)!;
      expect(values.wall_loops).toBe('2');
      expect(serialized).not.toHaveProperty('wall_loops');
    });
  });

  it('greys out options the slicer disables at the current settings', async () => {
    // sparse_infill_density at 0 turns off have_infill, which gates the infill
    // pattern — the same rule the desktop slicer applies.
    const user = userEvent.setup();
    await renderPanel({ sparse_infill_density: '0%' });
    const pattern = await showOption(user, 'Sparse infill pattern', 'sparse infill pattern');
    expect(pattern).toBeDisabled();
  });

  it('keeps an option editable while infill is on', async () => {
    const user = userEvent.setup();
    await renderPanel({ sparse_infill_density: '15%' });
    const pattern = await showOption(user, 'Sparse infill pattern', 'sparse infill pattern');
    expect(pattern).not.toBeDisabled();
  });

  it('lets a field be emptied without snapping back to the default', async () => {
    // Regression: dropping the key on an empty input made the control fall
    // straight back to the preset default, so clearing a value to retype it
    // appended to the old one ("0.2" + "0.16" = "0.2016").
    const user = userEvent.setup();
    await renderPanel();

    const input = screen.getByLabelText(/^Layer height/);
    await user.clear(input);
    expect(input).toHaveValue(null);
  });

  it('lets a free-text field be emptied too', async () => {
    // coFloatOrPercent / coString / vector options render as text rather than
    // number inputs, and the same drop-the-key-on-empty bug lived on that
    // branch after the number branch was fixed.
    const user = userEvent.setup();
    await renderPanel();

    const input = await showOption(user, 'Default', 'line_width');
    await user.clear(input);
    expect(input).toHaveValue('');
  });

  it('clears every override from the header reset', async () => {
    const user = userEvent.setup();
    const { onChange } = await renderPanel({ layer_height: '0.16' });

    await user.click(await screen.findByRole('button', { name: /Reset 1/ }));

    const [values, serialized] = onChange.mock.calls.at(-1)!;
    expect(values).toEqual({});
    expect(serialized).toEqual({});
  });

  it('reverts a single option without touching the others', async () => {
    const user = userEvent.setup();
    const { onChange } = await renderPanel({ layer_height: '0.16', wall_loops: 4 });

    const row = screen.getByLabelText(/^Layer height/).closest('div.group') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: 'Reset to default' }));

    const [values] = onChange.mock.calls.at(-1)!;
    expect(values).not.toHaveProperty('layer_height');
    expect(values.wall_loops).toBe(4);
  });
});

describe('SlicerSettingsPanel — search', () => {
  it('treats underscores and spaces alike so a key can be typed naturally', async () => {
    // outer_wall_speed's label is only "Outer wall" — the Speed page supplies
    // the rest — so the key is the only place the full phrase appears.
    const user = userEvent.setup();
    await renderPanel();
    await user.click(screen.getByRole('button', { name: 'Expert' }));
    await user.type(screen.getByPlaceholderText('Search settings'), 'outer wall speed');
    await waitFor(() => expect(screen.getByLabelText(/^Outer wall/)).toBeInTheDocument());
  });

  it('matches a page or group name, not just option labels', async () => {
    const user = userEvent.setup();
    await renderPanel();
    await user.click(screen.getByRole('button', { name: 'Expert' }));
    await user.type(screen.getByPlaceholderText('Search settings'), 'ironing');
    await waitFor(() => expect(screen.getByLabelText(/^Ironing type/)).toBeInTheDocument());
  });
});

describe("SlicerSettingsPanel — the source file's own settings", () => {
  const sourceOverrides: DesignOverride[] = [
    { key: 'wall_loops', value: '5', printer_coupled: false },
    { key: 'outer_wall_speed', value: '200', printer_coupled: true },
    // A key the vendored schema has no entry for. It still applies, so it must
    // not silently vanish from a panel that claims to show what will be used.
    { key: 'some_unlisted_key', value: '7', printer_coupled: false },
  ];

  it("shows the designer's value against the option once switched on", async () => {
    const user = userEvent.setup();
    await renderPanel({}, { sourceOverrides, initialSelected: ['wall_loops'] });
    const input = await showOption(user, 'Wall loops', 'wall loops');
    expect(input).toHaveValue(5);
    expect(screen.getByText('from file')).toBeInTheDocument();
  });

  it('falls back to the preset value when it is switched off', async () => {
    const user = userEvent.setup();
    await renderPanel({}, { sourceOverrides, initialSelected: [] });
    // wall_loops defaults to 2 in the schema.
    const input = await showOption(user, 'Wall loops', 'wall loops');
    expect(input).toHaveValue(2);
  });

  it("puts the file's tick before the control it qualifies", async () => {
    // A checkbox that gates a field belongs ahead of it. It used to render
    // after the unit, out at the row's right edge, reading as unrelated.
    const user = userEvent.setup();
    await renderPanel({}, { sourceOverrides, initialSelected: ['wall_loops'] });
    const control = await showOption(user, 'Wall loops', 'wall loops');

    const row = control.closest('div.group') as HTMLElement;
    const tick = within(row).getByRole('checkbox');
    const controlFollowsTick = tick.compareDocumentPosition(control) & Node.DOCUMENT_POSITION_FOLLOWING;
    expect(controlFollowsTick).toBeTruthy();
  });

  it('flags a machine-coupled setting rather than applying it quietly', async () => {
    const user = userEvent.setup();
    await renderPanel({}, { sourceOverrides, initialSelected: ['wall_loops'] });
    await user.click(screen.getByRole('button', { name: 'Expert' }));
    await user.type(screen.getByPlaceholderText('Search settings'), 'outer wall speed');
    await waitFor(() => expect(screen.getByText("designer's printer")).toBeInTheDocument());
  });

  it('lists source settings the schema has no entry for', async () => {
    await renderPanel({}, { sourceOverrides, initialSelected: ['some_unlisted_key'] });
    await waitFor(() => expect(screen.getByText('Other settings from this file')).toBeInTheDocument());
    expect(screen.getByText('some_unlisted_key')).toBeInTheDocument();
    expect(screen.getByText('7')).toBeInTheDocument();
  });

  it('keeps a typed value ahead of the file\'s', async () => {
    const user = userEvent.setup();
    const { onChange } = await renderPanel({}, { sourceOverrides, initialSelected: ['wall_loops'] });

    const input = await showOption(user, 'Wall loops', 'wall loops');
    expect(input).toHaveValue(5);
    await user.clear(input);
    await user.type(input, '3');

    // The typed value is what gets sent; the file's tick is unaffected and the
    // backend applies it first, so last-write-wins leaves 3 in the process JSON.
    await waitFor(() => {
      const [, serialized] = onChange.mock.calls.at(-1)!;
      expect(serialized.wall_loops).toBe('3');
    });
  });
});

describe('SlicerSettingsPanel — what the slicer\'s rules are read against (#2942)', () => {
  /** The designer's own support configuration, as a real file records it. */
  const supportSource: DesignOverride[] = [
    { key: 'support_type', value: 'normal(auto)', printer_coupled: false, preset_defining: false },
    { key: 'enable_support', value: '1', printer_coupled: false, preset_defining: false },
  ];

  it("honours the picked preset's value, not the compiled-in default", async () => {
    // The reporter's case: a process preset with supports on, nothing typed
    // into the panel. The rules used to be evaluated against the typed values
    // alone, which fell back to the schema's `enable_support: false` and
    // greyed out the whole Support page while the slice ran supports.
    const user = userEvent.setup();
    await renderPanel({}, { presetValues: { enable_support: '1' } });
    const type = await showOption(user, 'Type', 'support_type');
    expect(type).toBeEnabled();
  });

  it('still greys the page out when the preset really has supports off', async () => {
    const user = userEvent.setup();
    await renderPanel({}, { presetValues: { enable_support: '0' } });
    const type = await showOption(user, 'Type', 'support_type');
    expect(type).toBeDisabled();
  });

  it("counts a switched-on source setting as one of the slice's values", async () => {
    // Supports come from the file rather than the preset here. The rows they
    // gate are as live as if the preset had asked for them.
    const user = userEvent.setup();
    await renderPanel(
      {},
      { sourceOverrides: supportSource, initialSelected: ['enable_support'] },
    );
    const type = await showOption(user, 'Type', 'support_type');
    expect(type).toBeEnabled();
  });

  it('leaves the file\'s tick operable on a row the slicer has greyed out', async () => {
    // The two answer different questions -- "is this option in play" versus
    // "where does its value come from" -- and folding them together is what
    // left a ticked source setting applied to the slice with nothing on
    // screen able to clear it.
    const user = userEvent.setup();
    await renderPanel({}, { sourceOverrides: supportSource, initialSelected: ['support_type'] });
    const type = await showOption(user, 'Type', 'support_type');
    expect(type).toBeDisabled();

    const tick = screen.getByRole('checkbox', { name: /Use the source file's value for Type/ });
    expect(tick).toBeEnabled();
    expect(tick).toBeChecked();
    await user.click(tick);
    expect(tick).not.toBeChecked();
  });
});

describe('SlicerSettingsPanel — taking the file\'s settings in bulk (#2942)', () => {
  const mixed: DesignOverride[] = [
    { key: 'wall_loops', value: '5', printer_coupled: false, preset_defining: false },
    { key: 'outer_wall_speed', value: '200', printer_coupled: true, preset_defining: false },
    { key: 'layer_height', value: '0.2', printer_coupled: false, preset_defining: true },
  ];

  it('says how many the file changed', async () => {
    await renderPanel({}, { sourceOverrides: mixed });
    expect(screen.getByText(/The designer changed 3 process settings/)).toBeInTheDocument();
  });

  it('says nothing for a file that changed nothing', async () => {
    await renderPanel({});
    expect(screen.queryByText(/The designer changed/)).toBeNull();
  });

  it('ticks the ones that carry across printers, and only those', async () => {
    const user = userEvent.setup();
    await renderPanel({}, { sourceOverrides: mixed });
    await user.click(screen.getByRole('button', { name: /Use the designer's settings/ }));

    await showOption(user, 'Wall loops', 'wall_loops');
    expect(
      screen.getByRole('checkbox', { name: /Use the source file's value for Wall loops/ }),
    ).toBeChecked();
    // Tuned for the designer's machine, and the one that *is* the picked
    // preset: both stay a per-key decision.
    await showOption(user, 'Outer wall', 'outer_wall_speed');
    expect(
      screen.getByRole('checkbox', { name: /Use the source file's value for Outer wall/ }),
    ).not.toBeChecked();
    await showOption(user, 'Layer height', 'layer_height');
    expect(
      screen.getByRole('checkbox', { name: /Use the source file's value for Layer height/ }),
    ).not.toBeChecked();
  });

  it('clears every one of them, machine-coupled included', async () => {
    const user = userEvent.setup();
    await renderPanel({}, { sourceOverrides: mixed, initialSelected: ['wall_loops', 'outer_wall_speed'] });
    await user.click(screen.getByRole('button', { name: /Clear 2/ }));
    expect(screen.queryByRole('button', { name: /Clear/ })).toBeNull();

    await showOption(user, 'Wall loops', 'wall_loops');
    expect(
      screen.getByRole('checkbox', { name: /Use the source file's value for Wall loops/ }),
    ).not.toBeChecked();
  });
});

describe('SlicerSettingsPanel — filament-slot options', () => {
  const filamentChoices: FilamentChoice[] = [
    { index: 1, label: 'Bambu PLA Basic', color: '#FF0000' },
    { index: 2, label: 'Bambu Support for PLA', color: '#FFFFFF' },
  ];

  it("follows the slicer's own gating rather than being live regardless", async () => {
    // The interface picker sits behind have_support_material, so it greys out
    // with supports off — becoming a dropdown must not exempt it from the
    // rules every other option obeys.
    const user = userEvent.setup();
    await renderPanel({}, { filamentChoices });
    const off = await showOption(user, 'Support/raft interface', 'support_interface_filament');
    expect(off).toBeDisabled();
  });

  it('is operable once supports are switched on', async () => {
    const user = userEvent.setup();
    await renderPanel({ enable_support: true }, { filamentChoices });
    const on = await showOption(user, 'Support/raft interface', 'support_interface_filament');
    expect(on).toBeEnabled();
  });

  it('offers the picked filaments instead of a bare number field', async () => {
    const user = userEvent.setup();
    await renderPanel({}, { filamentChoices });
    const control = await showOption(user, 'Support/raft base', 'support_filament');

    expect(control.tagName).toBe('SELECT');
    const labels = Array.from((control as HTMLSelectElement).options).map((o) => o.textContent);
    expect(labels).toEqual(['Default', '1: Bambu PLA Basic', '2: Bambu Support for PLA']);
  });

  it("defaults to the slicer's 0, meaning no specific filament", async () => {
    const user = userEvent.setup();
    await renderPanel({}, { filamentChoices });
    const control = await showOption(user, 'Support/raft base', 'support_filament');
    expect(control).toHaveValue('0');
  });

  it('sends the slot index the slicer expects', async () => {
    const user = userEvent.setup();
    const { onChange } = await renderPanel({ enable_support: true }, { filamentChoices });
    const control = await showOption(user, 'Support/raft interface', 'support_interface_filament');

    await user.selectOptions(control, '2');
    await waitFor(() => {
      const [, serialized] = onChange.mock.calls.at(-1)!;
      expect(serialized.support_interface_filament).toBe('2');
    });
  });

  it('stays a plain number field when no filaments have been picked', async () => {
    // STL sources and the pre-plate-analysis window have no slot list yet;
    // an empty dropdown would be worse than the number input it replaced.
    const user = userEvent.setup();
    await renderPanel({}, { filamentChoices: [] });
    const control = await showOption(user, 'Support/raft base', 'support_filament');
    expect(control.tagName).toBe('INPUT');
  });

  it('leaves unrelated integer options alone', async () => {
    const user = userEvent.setup();
    await renderPanel({}, { filamentChoices });
    const control = await showOption(user, 'Wall loops', 'wall loops');
    expect(control.tagName).toBe('INPUT');
  });
});

describe('SlicerSettingsPanel — the picked preset\'s values', () => {
  it('shows the preset value rather than the compiled-in default', async () => {
    // The reported bug: line_width defaults to 0 in OrcaSlicer's C++ (meaning
    // "derive from the nozzle"), so every Line width field read 0 regardless
    // of what the chosen preset actually sets.
    const user = userEvent.setup();
    await renderPanel({}, { presetValues: { line_width: '0.42' } });
    const input = await showOption(user, 'Default', 'line_width');
    expect(input).toHaveValue('0.42');
  });

  it('does not mark a preset value as a user change', async () => {
    // Comparing against the schema default would flag every field the preset
    // moved off the C++ default as edited, and send values nobody typed.
    const { onChange } = await renderPanel({}, { presetValues: { line_width: '0.42' } });
    await waitFor(() => expect(screen.getByPlaceholderText('Search settings')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /Reset \d/ })).not.toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('sends an edit that differs from the preset', async () => {
    const user = userEvent.setup();
    const { onChange } = await renderPanel({}, { presetValues: { line_width: '0.42' } });
    const input = await showOption(user, 'Default', 'line_width');

    await user.clear(input);
    await user.type(input, '0.5');

    await waitFor(() => {
      const [, serialized] = onChange.mock.calls.at(-1)!;
      expect(serialized.line_width).toBe('0.5');
    });
  });

  it('sends nothing for a value retyped to match the preset', async () => {
    const user = userEvent.setup();
    const { onChange } = await renderPanel({}, { presetValues: { line_width: '0.42' } });
    const input = await showOption(user, 'Default', 'line_width');

    await user.clear(input);
    await user.type(input, '0.42');

    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const [, serialized] = onChange.mock.calls.at(-1)!;
    expect(serialized).not.toHaveProperty('line_width');
  });

  it('reverts to the preset value, not the schema default', async () => {
    const user = userEvent.setup();
    await renderPanel({ line_width: '0.5' }, { presetValues: { line_width: '0.42' } });
    const input = await showOption(user, 'Default', 'line_width');
    expect(input).toHaveValue('0.5');

    const row = input.closest('div.group') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: 'Reset to default' }));
    await waitFor(() => expect(screen.getByLabelText(/^Default/)).toHaveValue('0.42'));
  });

  it('says so when the preset values could not be read', async () => {
    await renderPanel({}, { presetValuesResolved: false });
    await waitFor(() => expect(screen.getByText(/Showing slicer defaults/)).toBeInTheDocument());
  });

  it('names the fix when the sidecar predates the endpoint', async () => {
    // The dominant case: an install pulls SIDECAR_TAG:-latest regardless of
    // its own release channel, so a current Bambuddy against an old sidecar is
    // normal. A generic "could not be read" sends that user hunting.
    await renderPanel({}, { presetValuesResolved: false, presetValuesReason: 'sidecar_outdated' });
    await waitFor(() => expect(screen.getByText(/Update the sidecar image/)).toBeInTheDocument());
  });

  it('distinguishes a sidecar that is missing from one that is merely old', async () => {
    await renderPanel({}, { presetValuesResolved: false, presetValuesReason: 'not_configured' });
    await waitFor(() => expect(screen.getByText(/no slicer sidecar is configured/)).toBeInTheDocument());
    expect(screen.queryByText(/Update the sidecar image/)).not.toBeInTheDocument();
  });

  it('distinguishes a sidecar that did not answer', async () => {
    await renderPanel({}, { presetValuesResolved: false, presetValuesReason: 'sidecar_unavailable' });
    await waitFor(() => expect(screen.getByText(/did not answer/)).toBeInTheDocument());
    expect(screen.queryByText(/Update the sidecar image/)).not.toBeInTheDocument();
  });

  it('shows no such notice when they resolved', async () => {
    await renderPanel({}, { presetValues: { line_width: '0.42' }, presetValuesResolved: true });
    await waitFor(() => expect(screen.getByPlaceholderText('Search settings')).toBeInTheDocument());
    expect(screen.queryByText(/Showing slicer defaults/)).not.toBeInTheDocument();
  });
});

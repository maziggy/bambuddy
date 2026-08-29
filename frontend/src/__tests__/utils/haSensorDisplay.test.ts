/**
 * describeHASensorReading is the single formatter shared by the printer row
 * and both location-sensor display sites (InventoryPage, SettingsPage).
 * Before this, all three carried their own near-identical copy, and the
 * printer's copy (no decimals) had quietly drifted from the location copies
 * (fixed to two decimals, a deliberate width-alignment choice). Locking both
 * behaviors down here as the one place either could change.
 */

import { describe, it, expect } from 'vitest';
import { describeHASensorReading } from '../../utils/haSensorDisplay';

const t = (key: string, opts?: Record<string, unknown>) =>
  (opts?.defaultValue as string | undefined) ?? key;

describe('describeHASensorReading', () => {
  it('shows the raw value with no decimals option — the printer row behavior', () => {
    const reading = { kind: 'numeric' as const, value: 23.4, unit: '°C', state: '23.4', reachable: true, device_class: 'temperature' };
    expect(describeHASensorReading(reading, t)).toBe('23.4 °C');
  });

  it('pads to a fixed decimal count when asked — the location sensor behavior', () => {
    const reading = { kind: 'numeric' as const, value: 23.4, unit: '°C', state: '23.4', reachable: true, device_class: 'temperature' };
    expect(describeHASensorReading(reading, t, { decimals: 2 })).toBe('23.40 °C');
  });

  it('pads a whole-number value too, e.g. battery at 87%', () => {
    const reading = { kind: 'numeric' as const, value: 87, unit: '%', state: '87', reachable: true, device_class: 'battery' };
    expect(describeHASensorReading(reading, t, { decimals: 2 })).toBe('87.00 %');
  });

  it('omits the unit when the reading has none', () => {
    const reading = { kind: 'numeric' as const, value: 23.4, unit: null, state: '23.4', reachable: true, device_class: 'temperature' };
    expect(describeHASensorReading(reading, t)).toBe('23.4');
  });

  it('falls back to the raw state string when value is null', () => {
    const reading = { kind: 'numeric' as const, value: null, unit: '°C', state: 'unknown', reachable: true, device_class: 'temperature' };
    expect(describeHASensorReading(reading, t)).toBe('unknown');
  });

  it('shows unavailable text for an unreachable sensor, regardless of kind', () => {
    const reading = { kind: 'numeric' as const, value: 23.4, unit: '°C', state: '23.4', reachable: false, device_class: 'temperature' };
    expect(describeHASensorReading(reading, t)).toBe('haSensors.unavailable');
  });

  it('shows unavailable text when state is null even if marked reachable', () => {
    const reading = { kind: 'numeric' as const, value: null, unit: '°C', state: null, reachable: true, device_class: 'temperature' };
    expect(describeHASensorReading(reading, t)).toBe('haSensors.unavailable');
  });

  it('translates a binary reading through its device-class label', () => {
    const reading = { kind: 'binary' as const, value: null, unit: null, state: 'on', reachable: true, device_class: 'door' };
    expect(describeHASensorReading(reading, t)).toBe('open');
  });

  it('falls back to the raw on/off state for a binary reading with no known device class', () => {
    const reading = { kind: 'binary' as const, value: null, unit: null, state: 'on', reachable: true, device_class: 'unmapped_class' };
    expect(describeHASensorReading(reading, t)).toBe('on');
  });
});

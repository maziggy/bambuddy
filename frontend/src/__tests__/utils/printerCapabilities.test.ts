import { describe, it, expect } from 'vitest';
import { getPrinterControlCapabilities, isA1Family, isDualNozzleModel, supportsChamberFan } from '../../utils/printerCapabilities';
import type { Printer, PrinterStatus } from '../../api/client';

const basePrinter: Printer = {
  id: 1,
  name: 'Test',
  serial_number: 'SN',
  ip_address: '192.168.1.1',
  access_code: '12345678',
  model: 'A1',
  is_active: true,
  nozzle_count: 1,
  auto_archive: true,
  created_at: '',
  updated_at: '',
};

const connectedStatus = {
  connected: true,
  cooling_fan_speed: 33,
  big_fan1_speed: 33,
  big_fan2_speed: 33,
  temperatures: { nozzle: 28, nozzle_2: 0, bed: 21 },
  nozzles: [{ nozzle_diameter: '0.4', nozzle_type: 'stainless_steel' }],
} as unknown as PrinterStatus;

describe('printerCapabilities', () => {
  it('A1 is not dual nozzle even when MQTT reports nozzle_2', () => {
    expect(isDualNozzleModel('A1')).toBe(false);
    expect(isA1Family('A1')).toBe(true);
    const caps = getPrinterControlCapabilities(basePrinter, connectedStatus);
    expect(caps.showDualNozzle).toBe(false);
  });

  it('A1 has part fan only, no chamber or aux control', () => {
    const caps = getPrinterControlCapabilities(basePrinter, connectedStatus);
    expect(caps.showPartFan).toBe(true);
    expect(caps.showAuxFan).toBe(false);
    expect(caps.showChamberFan).toBe(false);
    expect(caps.showChamberTemp).toBe(false);
  });

  it('H2D shows dual nozzle and chamber fan', () => {
    expect(isDualNozzleModel('H2D')).toBe(true);
    expect(supportsChamberFan('H2D')).toBe(true);
    const caps = getPrinterControlCapabilities(
      { ...basePrinter, model: 'H2D', nozzle_count: 2 },
      connectedStatus
    );
    expect(caps.showDualNozzle).toBe(true);
    expect(caps.showChamberFan).toBe(true);
  });

  it('P1S has chamber fan but not chamber temp', () => {
    expect(supportsChamberFan('P1S')).toBe(true);
    const caps = getPrinterControlCapabilities(
      { ...basePrinter, model: 'P1S' },
      connectedStatus
    );
    expect(caps.showChamberFan).toBe(true);
    expect(caps.showChamberTemp).toBe(false);
  });
});

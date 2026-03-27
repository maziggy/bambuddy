import { describe, it, expect } from 'vitest';
import { getCurrencySymbol, SUPPORTED_CURRENCIES } from '../../utils/currency';

describe('getCurrencySymbol', () => {
  it('returns $ for USD', () => {
    expect(getCurrencySymbol('USD')).toBe('$');
  });

  it('returns € for EUR', () => {
    expect(getCurrencySymbol('EUR')).toBe('€');
  });

  it('returns £ for GBP', () => {
    expect(getCurrencySymbol('GBP')).toBe('£');
  });

  it('returns ₹ for INR', () => {
    expect(getCurrencySymbol('INR')).toBe('₹');
  });

  it('returns HK$ for HKD', () => {
    expect(getCurrencySymbol('HKD')).toBe('HK$');
  });

  it('returns RM for MYR', () => {
    expect(getCurrencySymbol('MYR')).toBe('RM');
  });

  it('returns ₴ for UAH', () => {
    expect(getCurrencySymbol('UAH')).toBe('₴');
  });

  it('returns the code itself for unknown currencies', () => {
    expect(getCurrencySymbol('XYZ')).toBe('XYZ');
  });

  it('is case-insensitive', () => {
    expect(getCurrencySymbol('usd')).toBe('$');
    expect(getCurrencySymbol('eur')).toBe('€');
  });
});

describe('SUPPORTED_CURRENCIES', () => {
  it('contains INR', () => {
    expect(SUPPORTED_CURRENCIES.find((c) => c.code === 'INR')).toBeDefined();
  });

  it('contains MYR', () => {
    expect(SUPPORTED_CURRENCIES.find((c) => c.code === 'MYR')).toBeDefined();
  });

  it('has 28 entries', () => {
    expect(SUPPORTED_CURRENCIES).toHaveLength(29);
  });
});

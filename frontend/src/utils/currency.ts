const CURRENCY_SYMBOLS: Record<string, string> = {
  USD: '$',
  EUR: '€',
  GBP: '£',
  CHF: 'Fr.',
  JPY: '¥',
  CNY: '¥',
  CAD: '$',
  AUD: '$',
  INR: '₹',
  HKD: 'HK$',
  KRW: '₩',
  SEK: 'kr',
  NOK: 'kr',
  DKK: 'kr',
  PLN: 'zł',
  BRL: 'R$',
  TWD: 'NT$',
  SGD: 'S$',
  NZD: 'NZ$',
  MXN: 'MX$',
  MYR: 'RM',
  CZK: 'Kč',
  THB: '฿',
  ZAR: 'R',
  TRY: '₺',
  RUB: '₽',
  HUF: 'Ft',
  ILS: '₪',
  UAH: '₴',
};

export function getCurrencySymbol(currencyCode: string): string {
  return CURRENCY_SYMBOLS[currencyCode.toUpperCase()] || currencyCode;
}

export const SUPPORTED_CURRENCIES = Object.entries(CURRENCY_SYMBOLS).map(([code, symbol]) => ({
  code,
  label: `${code} (${symbol})`,
}));

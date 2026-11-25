/** A manufacturer as brands are compared (#3727): trimmed, any case. */
export function brandKey(brand: string | null | undefined): string {
  return (brand ?? '').trim().toLowerCase();
}

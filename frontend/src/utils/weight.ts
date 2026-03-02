export function formatWeight(grams: number): string {
  if (grams >= 1_000_000) {
    const tonnes = grams / 1_000_000;
    return `${tonnes % 1 === 0 ? tonnes.toFixed(0) : tonnes.toFixed(1)}t`;
  }
  if (grams >= 1000) {
    const kg = grams / 1000;
    return `${kg % 1 === 0 ? kg.toFixed(0) : kg.toFixed(1)}kg`;
  }
  return `${Math.round(grams)}g`;
}

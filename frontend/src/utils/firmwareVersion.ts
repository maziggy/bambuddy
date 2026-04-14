/**
 * Compare two Bambu Lab firmware version strings (format: "XX.XX.XX.XX").
 *
 * Returns a negative number if `a` < `b`, zero if equal, positive if `a` > `b`.
 * Missing trailing segments are treated as 0.
 */
export function compareFwVersions(a: string, b: string): number {
  const pa = a.split('.').map((n) => parseInt(n, 10) || 0);
  const pb = b.split('.').map((n) => parseInt(n, 10) || 0);
  while (pa.length < 4) pa.push(0);
  while (pb.length < 4) pb.push(0);
  for (let i = 0; i < 4; i++) {
    if (pa[i] !== pb[i]) return pa[i] - pb[i];
  }
  return 0;
}

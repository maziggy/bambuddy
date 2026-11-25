// Small heading row used to label the "Suggested" / "All" groups of the
// spool form dropdowns (#1905, #3619).
export function GroupHeading({ label }: { label: string }) {
  return (
    <div className="px-3 py-1 text-[11px] font-semibold uppercase tracking-wide text-bambu-gray/70 bg-bambu-dark-tertiary/40">
      {label}
    </div>
  );
}

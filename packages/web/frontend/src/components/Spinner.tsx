export function Spinner({ label }: { label?: string }) {
  return (
    <span
      role="status"
      aria-live="polite"
      className="inline-flex items-center gap-1.5 text-sm text-primary"
    >
      <span className="supernova-spinner" aria-hidden="true" />
      {label}
    </span>
  );
}

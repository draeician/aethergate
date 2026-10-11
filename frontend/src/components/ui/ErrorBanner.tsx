export default function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div
      role="alert"
      className="text-sm px-3 py-2 rounded-lg border border-[var(--ag-danger)]/40 bg-[var(--ag-danger)]/10 text-[var(--ag-danger)]"
    >
      {message}
    </div>
  );
}

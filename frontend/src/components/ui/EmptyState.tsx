export default function EmptyState({ message = "Nothing to show." }: { message?: string }) {
  return <p className="text-sm text-[var(--ag-text-muted)] py-8 text-center">{message}</p>;
}

import { ChevronLeft, ChevronRight } from "lucide-react";

interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
}

export default function Pagination({ page, pageSize, total, onPageChange }: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="flex items-center justify-between">
      <button
        type="button"
        disabled={page === 0}
        onClick={() => onPageChange(Math.max(0, page - 1))}
        className="flex items-center gap-1 text-xs px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] disabled:opacity-40 transition-colors"
      >
        <ChevronLeft size={14} />
        Prev
      </button>
      <span className="text-xs text-[var(--ag-text-muted)]">
        Page {page + 1} of {totalPages} · {total.toLocaleString()} total
      </span>
      <button
        type="button"
        disabled={page + 1 >= totalPages}
        onClick={() => onPageChange(page + 1)}
        className="flex items-center gap-1 text-xs px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] disabled:opacity-40 transition-colors"
      >
        Next
        <ChevronRight size={14} />
      </button>
    </div>
  );
}

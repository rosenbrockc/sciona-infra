interface PaginationProps {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
}

export default function Pagination({ total, limit, offset, onChange }: PaginationProps) {
  const page = Math.floor(offset / limit) + 1;
  const totalPages = Math.ceil(total / limit);
  if (totalPages <= 1) return null;

  return (
    <div className="flex items-center gap-3 mt-4 text-sm">
      <button
        disabled={page <= 1}
        onClick={() => onChange(offset - limit)}
        className="px-3 py-1 rounded bg-panel-soft border border-border text-muted hover:text-gray-200 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        Prev
      </button>
      <span className="text-muted">
        Page {page} of {totalPages}
      </span>
      <button
        disabled={page >= totalPages}
        onClick={() => onChange(offset + limit)}
        className="px-3 py-1 rounded bg-panel-soft border border-border text-muted hover:text-gray-200 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        Next
      </button>
    </div>
  );
}

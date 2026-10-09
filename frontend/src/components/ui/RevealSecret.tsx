import { useState } from "react";
import { Check, Copy } from "lucide-react";
import Modal from "./Modal";

interface RevealSecretProps {
  open: boolean;
  value: string;
  title: string;
  onDismiss: () => void;
}

/**
 * One-time raw-key reveal. The raw value exists only in the parent's transient
 * state; `onDismiss` must clear that state so the value is destroyed on close.
 * It is never written to storage, the URL, or logs.
 */
export default function RevealSecret({ open, value, title, onDismiss }: RevealSecretProps) {
  const [copied, setCopied] = useState(false);

  if (!open) return null;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard may be blocked; the value remains visible for manual copy.
    }
  };

  return (
    <Modal title={title} onClose={onDismiss}>
      <p className="text-sm text-[var(--ag-text-muted)]">
        This value is shown only once. Copy it now — it cannot be retrieved again.
      </p>
      <div className="mt-3 flex items-center gap-2">
        <code className="flex-1 break-all text-xs font-mono bg-[var(--ag-surface-2)] border border-[var(--ag-border)] rounded-lg px-3 py-2 text-[var(--ag-text)]">
          {value}
        </code>
        <button
          type="button"
          onClick={() => void copy()}
          aria-label="Copy value"
          className="shrink-0 p-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] transition-colors"
        >
          {copied ? <Check size={16} /> : <Copy size={16} />}
        </button>
      </div>
      <div className="mt-5 flex justify-end">
        <button
          type="button"
          onClick={onDismiss}
          className="text-sm px-3 py-2 rounded-lg bg-[var(--ag-accent)] hover:bg-[var(--ag-accent-hover)] text-white transition-colors"
        >
          Done
        </button>
      </div>
    </Modal>
  );
}

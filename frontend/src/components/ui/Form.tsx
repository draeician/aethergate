import type {
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

export const inputClass =
  "text-sm bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-lg px-3 py-2 text-[var(--ag-text)] placeholder:text-[var(--ag-text-muted)] focus:outline-none focus:border-[var(--ag-accent)] w-full";

/** Labelled form control wrapper (label always rendered for accessibility). */
export function Field({
  label,
  htmlFor,
  hint,
  children,
}: {
  label: string;
  htmlFor: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={htmlFor} className="text-xs text-[var(--ag-text-muted)] uppercase tracking-wider">
        {label}
      </label>
      {children}
      {hint ? <p className="text-xs text-[var(--ag-text-muted)]">{hint}</p> : null}
    </div>
  );
}

export function TextInput({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`${inputClass} ${className ?? ""}`} />;
}

export function NumberInput({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input type="number" {...props} className={`${inputClass} ${className ?? ""}`} />;
}

export function Select({ className, children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select {...props} className={`${inputClass} ${className ?? ""}`}>
      {children}
    </select>
  );
}

export function TextArea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={`${inputClass} ${className ?? ""}`} />;
}

export function Toggle({
  id,
  label,
  checked,
  onChange,
}: {
  id: string;
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label htmlFor={id} className="flex items-center gap-2 text-sm text-[var(--ag-text)] cursor-pointer select-none">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="h-4 w-4 accent-[var(--ag-accent)]"
      />
      {label}
    </label>
  );
}

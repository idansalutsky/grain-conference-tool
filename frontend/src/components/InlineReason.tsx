import { useEffect, useRef, useState } from "react";

/**
 * InlineReason — an on-brand replacement for window.prompt().
 *
 * A native prompt screams "prototype". This renders a small inline popover with
 * a controlled text field + Confirm / Cancel, styled with the app's own ink/paper
 * `input` and `btn-*` classes. It captures a free-text reason for a
 * human-in-the-loop decision (score adjustment, persona override) and hands it
 * back via onConfirm — the caller still owns the mutation/payload.
 */
export function InlineReason({
  open,
  title,
  placeholder,
  defaultValue = "",
  confirmLabel = "Save",
  pending = false,
  align = "left",
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  placeholder?: string;
  defaultValue?: string;
  confirmLabel?: string;
  pending?: boolean;
  align?: "left" | "right";
  onConfirm: (reason: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(defaultValue);
  const inputRef = useRef<HTMLInputElement>(null);

  // Reset to the prefilled default each time it opens, and focus for fast entry.
  useEffect(() => {
    if (open) {
      setValue(defaultValue);
      // focus on next tick so the element is mounted/visible
      const t = setTimeout(() => inputRef.current?.focus(), 0);
      return () => clearTimeout(t);
    }
  }, [open, defaultValue]);

  if (!open) return null;

  const submit = () => {
    const reason = value.trim();
    if (reason) onConfirm(reason);
  };

  return (
    <div
      className={
        "mt-2 rounded-md border border-ink-200 bg-surface p-2.5 shadow-card w-full max-w-[16rem] " +
        (align === "right" ? "ml-auto" : "")
      }
      onClick={(e) => e.stopPropagation()}
    >
      <div className="text-[10px] uppercase tracking-wider text-ink-500 mb-1.5">{title}</div>
      <input
        ref={inputRef}
        type="text"
        className="input w-full text-xs"
        placeholder={placeholder}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            submit();
          } else if (e.key === "Escape") {
            e.preventDefault();
            onCancel();
          }
        }}
      />
      <div className="flex justify-end gap-1.5 mt-2">
        <button type="button" className="btn-ghost h-7 text-xs" onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className="btn-primary h-7 text-xs"
          disabled={pending || !value.trim()}
          onClick={submit}
        >
          {pending ? "Saving…" : confirmLabel}
        </button>
      </div>
    </div>
  );
}

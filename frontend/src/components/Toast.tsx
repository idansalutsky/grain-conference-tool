import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";

type ToastKind = "info" | "success" | "error";

interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
}

interface ToastCtx {
  push: (kind: ToastKind, message: string) => void;
}

const Ctx = createContext<ToastCtx | null>(null);

let _nextId = 1;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const push = useCallback((kind: ToastKind, message: string) => {
    const id = _nextId++;
    setItems((prev) => [...prev, { id, kind, message }]);
    setTimeout(() => {
      setItems((prev) => prev.filter((t) => t.id !== id));
    }, 5000);
  }, []);

  const value = useMemo(() => ({ push }), [push]);

  return (
    <Ctx.Provider value={value}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 space-y-2 max-w-sm">
        {items.map((t) => (
          <div
            key={t.id}
            className={
              "px-3 py-2 rounded-md shadow-lg text-sm font-medium border " +
              (t.kind === "error"
                ? "bg-red-50 text-red-900 border-red-200"
                : t.kind === "success"
                ? "bg-emerald-50 text-emerald-900 border-emerald-200"
                : "bg-blue-50 text-blue-900 border-blue-200")
            }
          >
            <div className="flex items-start gap-2">
              <span aria-hidden="true">
                {t.kind === "error" ? "⚠️" : t.kind === "success" ? "✓" : "ℹ"}
              </span>
              <span className="flex-1">{t.message}</span>
            </div>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useToast() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>");
  return ctx;
}

/**
 * Convert any error (string, Error, FetchError) into a human-readable string.
 * Used to replace `String(error)` everywhere — those produced "[object Object]"
 * or stack-trace junk when the server returned a structured detail.
 */
export function toastErrorMessage(error: unknown): string {
  if (!error) return "Something went wrong.";
  if (error instanceof Error) return error.message || "Unknown error.";
  if (typeof error === "string") return error;
  if (typeof error === "object" && error !== null) {
    const e = error as Record<string, unknown>;
    if (typeof e.message === "string") return e.message;
    if (typeof e.detail === "string") return e.detail;
  }
  return "Unexpected error.";
}

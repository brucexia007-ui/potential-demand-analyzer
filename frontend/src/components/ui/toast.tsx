"use client";

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  type ReactNode,
} from "react";

type ToastType = "success" | "error" | "warning" | "info";

interface Toast {
  id: number;
  type: ToastType;
  message: string;
  exiting: boolean;
}

interface ToastContextValue {
  toast: (type: ToastType, message: string) => void;
  success: (message: string) => void;
  error: (message: string) => void;
  warning: (message: string) => void;
  info: (message: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

let toastId = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const addToast = useCallback((type: ToastType, message: string) => {
    const id = ++toastId;
    setToasts((prev) => [...prev.slice(-4), { id, type, message, exiting: false }]);
    setTimeout(() => {
      setToasts((prev) =>
        prev.map((t) => (t.id === id ? { ...t, exiting: true } : t))
      );
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
      }, 300);
    }, 5000);
  }, []);

  const value: ToastContextValue = {
    toast: addToast,
    success: useCallback((msg: string) => addToast("success", msg), [addToast]),
    error: useCallback((msg: string) => addToast("error", msg), [addToast]),
    warning: useCallback((msg: string) => addToast("warning", msg), [addToast]),
    info: useCallback((msg: string) => addToast("info", msg), [addToast]),
  };

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            role="alert"
            className={`pointer-events-auto max-w-sm rounded-lg border px-4 py-3 text-sm font-medium shadow-[var(--shadow-soft)] transition-all duration-300 ${
              t.exiting ? "opacity-0 translate-x-4" : "opacity-100 translate-x-0"
            } ${
              t.type === "success"
                ? "border-green-200 bg-green-50 text-green-800"
                : t.type === "error"
                ? "border-red-200 bg-red-50 text-red-800"
                : t.type === "warning"
                ? "border-amber-200 bg-amber-50 text-amber-800"
                : "border-cyan-200 bg-cyan-50 text-cyan-800"
            }`}
          >
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold leading-none">
                {t.type === "success" ? "OK" : t.type === "error" ? "ERR" : t.type === "warning" ? "WARN" : "INFO"}
              </span>
              <span className="flex-1">{t.message}</span>
              <button
                onClick={() => {
                  setToasts((prev) =>
                    prev.map((x) => (x.id === t.id ? { ...x, exiting: true } : x))
                  );
                  setTimeout(() => {
                    setToasts((prev) => prev.filter((x) => x.id !== t.id));
                  }, 300);
                }}
                className="ml-2 text-current opacity-60 hover:opacity-100"
              >
                X
              </button>
            </div>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}

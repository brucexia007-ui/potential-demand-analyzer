import type { HTMLAttributes, ReactNode } from "react";

type PageShellProps = HTMLAttributes<HTMLElement> & {
  children: ReactNode;
};

export function PageShell({ children, className = "", ...props }: PageShellProps) {
  return (
    <main
      className={`min-h-screen pb-12 pt-8 sm:pt-10 ${className}`}
      {...props}
    >
      <div className="mx-auto w-full max-w-6xl px-4 sm:px-6 lg:px-8">
        {children}
      </div>
    </main>
  );
}

type PageHeaderProps = {
  eyebrow?: string;
  title: string;
  description?: ReactNode;
  meta?: ReactNode;
  action?: ReactNode;
  className?: string;
};

export function PageHeader({
  eyebrow,
  title,
  description,
  meta,
  action,
  className = "",
}: PageHeaderProps) {
  return (
    <header className={`mb-6 sm:mb-8 ${className}`}>
      <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div className="min-w-0">
          {eyebrow && (
            <p className="mb-2 text-xs font-semibold uppercase text-neutral-500">
              {eyebrow}
            </p>
          )}
          <h1 className="text-2xl font-semibold text-neutral-950 sm:text-3xl">
            {title}
          </h1>
          {description && (
            <p className="mt-2 max-w-2xl text-sm leading-6 text-neutral-600">
              {description}
            </p>
          )}
          {meta && <div className="mt-3">{meta}</div>}
        </div>
        {action && <div className="flex shrink-0 flex-wrap gap-2">{action}</div>}
      </div>
    </header>
  );
}

type DataPanelProps = HTMLAttributes<HTMLDivElement> & {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  tone?: "default" | "lime" | "cyan" | "danger" | "success" | "warning";
};

const panelTone: Record<NonNullable<DataPanelProps["tone"]>, string> = {
  default: "border-neutral-950/10 bg-white/90",
  lime: "border-lime-300/80 bg-lime-50/70",
  cyan: "border-cyan-300/80 bg-cyan-50/70",
  danger: "border-red-200 bg-red-50/80",
  success: "border-green-200 bg-green-50/80",
  warning: "border-amber-200 bg-amber-50/80",
};

export function DataPanel({
  label,
  value,
  detail,
  tone = "default",
  className = "",
  ...props
}: DataPanelProps) {
  return (
    <div
      className={`rounded-lg border p-4 shadow-[var(--shadow-panel)] ${panelTone[tone]} ${className}`}
      {...props}
    >
      <p className="text-xs font-semibold uppercase text-neutral-500">
        {label}
      </p>
      <div className="mt-2 text-2xl font-semibold text-neutral-950">{value}</div>
      {detail && <div className="mt-1 text-xs text-neutral-500">{detail}</div>}
    </div>
  );
}

type SegmentOption = {
  value: string;
  label: string;
};

type SegmentedControlProps = {
  options: SegmentOption[];
  value: string;
  onChange: (value: string) => void;
  className?: string;
  size?: "sm" | "md";
};

export function SegmentedControl({
  options,
  value,
  onChange,
  className = "",
  size = "md",
}: SegmentedControlProps) {
  const sizing = size === "sm" ? "px-3 py-1.5 text-xs" : "px-4 py-2 text-sm";

  return (
    <div
      className={`hide-scrollbar flex max-w-full gap-1 overflow-x-auto rounded-full border border-neutral-950/10 bg-white/80 p-1 ${className}`}
    >
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            onClick={() => onChange(option.value)}
            className={`${sizing} shrink-0 rounded-full font-medium transition-all ${
              active
                ? "bg-neutral-950 text-white shadow-sm"
                : "text-neutral-600 hover:bg-neutral-950/5 hover:text-neutral-950"
            }`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

type StatusBadgeProps = {
  status: string;
  label?: string;
  className?: string;
};

const statusTone: Record<string, string> = {
  COMPLETED: "border-green-200 bg-green-50 text-green-700",
  RUNNING: "border-cyan-200 bg-cyan-50 text-cyan-700",
  FAILED: "border-red-200 bg-red-50 text-red-700",
  PENDING: "border-neutral-200 bg-neutral-100 text-neutral-700",
  CANCELLED: "border-neutral-200 bg-neutral-100 text-neutral-500",
  PARTIAL: "border-amber-200 bg-amber-50 text-amber-700",
  healthy: "border-green-200 bg-green-50 text-green-700",
  degraded: "border-amber-200 bg-amber-50 text-amber-700",
  open: "border-red-200 bg-red-50 text-red-700",
  half_open: "border-cyan-200 bg-cyan-50 text-cyan-700",
};

export function StatusBadge({ status, label, className = "" }: StatusBadgeProps) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium ${
        statusTone[status] ?? "border-neutral-200 bg-neutral-100 text-neutral-700"
      } ${className}`}
    >
      {label ?? status}
    </span>
  );
}

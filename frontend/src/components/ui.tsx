import clsx from "clsx";
import {
  BookOpen,
  Brain,
  CheckCircle2,
  CircleDashed,
  Compass,
  FileText,
  Loader2,
  Map,
  PenLine,
  Scale,
  Search,
  ShieldAlert,
  Wrench,
  XCircle,
} from "lucide-react";
import type { ReactNode } from "react";

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={clsx("rounded-xl border border-slate-200/80 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.04)]", className)}>{children}</div>;
}

export function CardHeader({ title, subtitle, right, icon }: { title: ReactNode; subtitle?: ReactNode; right?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-3.5">
      <div className="flex min-w-0 items-start gap-2.5">
        {icon && <div className="mt-0.5 text-slate-400">{icon}</div>}
        <div className="min-w-0">
          <div className="text-[13px] font-semibold text-slate-900">{title}</div>
          {subtitle && <div className="mt-0.5 text-xs text-slate-500">{subtitle}</div>}
        </div>
      </div>
      {right}
    </div>
  );
}

const TONES = {
  slate: "bg-slate-100 text-slate-700 ring-slate-200",
  brand: "bg-brand-50 text-brand-700 ring-brand-200",
  green: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  red: "bg-rose-50 text-rose-700 ring-rose-200",
  amber: "bg-amber-50 text-amber-800 ring-amber-200",
  blue: "bg-sky-50 text-sky-700 ring-sky-200",
  violet: "bg-violet-50 text-violet-700 ring-violet-200",
  teal: "bg-teal-50 text-teal-700 ring-teal-200",
} as const;
export type Tone = keyof typeof TONES;

export function Badge({ tone = "slate", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span className={clsx("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset", TONES[tone], className)}>
      {children}
    </span>
  );
}

export function Button({
  children,
  onClick,
  variant = "primary",
  size = "md",
  disabled,
  type = "button",
  className,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "secondary" | "ghost" | "danger" | "success";
  size?: "sm" | "md" | "lg";
  disabled?: boolean;
  type?: "button" | "submit";
  className?: string;
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500 disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" && "h-7 px-2.5 text-xs",
        size === "md" && "h-9 px-3.5 text-sm",
        size === "lg" && "h-11 px-5 text-sm",
        variant === "primary" && "bg-brand-600 text-white shadow-sm hover:bg-brand-700",
        variant === "secondary" && "border border-slate-200 bg-white text-slate-700 shadow-sm hover:bg-slate-50",
        variant === "ghost" && "text-slate-600 hover:bg-slate-100",
        variant === "danger" && "border border-rose-200 bg-white text-rose-700 hover:bg-rose-50",
        variant === "success" && "bg-emerald-600 text-white shadow-sm hover:bg-emerald-700",
        className,
      )}
    >
      {children}
    </button>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={clsx("animate-spin", className ?? "h-4 w-4")} />;
}

export function LiveDot({ tone = "blue" }: { tone?: "blue" | "green" | "amber" | "red" }) {
  const c = { blue: "bg-sky-500", green: "bg-emerald-500", amber: "bg-amber-500", red: "bg-rose-500" }[tone];
  return (
    <span className="relative inline-flex h-2 w-2">
      {tone === "blue" && <span className={clsx("absolute inline-flex h-full w-full animate-ping rounded-full opacity-60", c)} />}
      <span className={clsx("relative inline-flex h-2 w-2 rounded-full", c)} />
    </span>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-xl font-semibold tracking-tight text-slate-900 tabular-nums">{value}</div>
      {hint && <div className="mt-0.5 truncate text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

export function EmptyState({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      {icon && <div className="mb-3 rounded-full bg-slate-100 p-3 text-slate-400">{icon}</div>}
      <div className="text-sm font-semibold text-slate-800">{title}</div>
      {children && <div className="mt-1 max-w-md text-sm text-slate-500">{children}</div>}
    </div>
  );
}

// ---------------------------------------------------------------- verdicts
export const VERDICT: Record<string, { label: string; tone: Tone; icon: ReactNode }> = {
  confirmed: { label: "Confirmed", tone: "green", icon: <CheckCircle2 className="h-3 w-3" /> },
  rejected: { label: "Ruled out", tone: "slate", icon: <XCircle className="h-3 w-3" /> },
  needs_more: { label: "Needs more", tone: "amber", icon: <CircleDashed className="h-3 w-3" /> },
};

export function VerdictBadge({ status, running, queued }: { status?: string; running?: boolean; queued?: boolean }) {
  if (status && VERDICT[status]) {
    const v = VERDICT[status];
    return (
      <Badge tone={v.tone}>
        {v.icon}
        {v.label}
      </Badge>
    );
  }
  if (running)
    return (
      <Badge tone="blue">
        <Spinner className="h-3 w-3" />
        Investigating
      </Badge>
    );
  if (queued) return <Badge tone="slate">Queued</Badge>;
  return <Badge tone="violet">Awaiting review</Badge>;
}

// ---------------------------------------------------------------- agents
const AGENTS: { match: (a: string) => boolean; icon: ReactNode; color: string; label?: string }[] = [
  { match: (a) => a === "Planner", icon: <Compass className="h-3.5 w-3.5" />, color: "bg-violet-100 text-violet-700" },
  { match: (a) => a.startsWith("Investigator"), icon: <Search className="h-3.5 w-3.5" />, color: "bg-sky-100 text-sky-700" },
  { match: (a) => a.startsWith("Doc Analyst"), icon: <FileText className="h-3.5 w-3.5" />, color: "bg-teal-100 text-teal-700" },
  { match: (a) => a === "Evaluator", icon: <Scale className="h-3.5 w-3.5" />, color: "bg-fuchsia-100 text-fuchsia-700" },
  { match: (a) => a === "Data Scout", icon: <Map className="h-3.5 w-3.5" />, color: "bg-indigo-100 text-indigo-700" },
  { match: (a) => a === "Risk & Action", icon: <Wrench className="h-3.5 w-3.5" />, color: "bg-amber-100 text-amber-800" },
  { match: (a) => a === "Reporter", icon: <PenLine className="h-3.5 w-3.5" />, color: "bg-slate-200 text-slate-700" },
  { match: (a) => a === "Memory", icon: <Brain className="h-3.5 w-3.5" />, color: "bg-emerald-100 text-emerald-700" },
  { match: (a) => a === "Manager", icon: <ShieldAlert className="h-3.5 w-3.5" />, color: "bg-rose-100 text-rose-700" },
];

export function AgentAvatar({ agent, size = "md" }: { agent: string; size?: "sm" | "md" }) {
  const a = AGENTS.find((x) => x.match(agent));
  return (
    <span
      className={clsx(
        "inline-flex shrink-0 items-center justify-center rounded-full",
        size === "md" ? "h-7 w-7" : "h-5 w-5 [&>svg]:h-3 [&>svg]:w-3",
        a?.color ?? "bg-slate-100 text-slate-500",
      )}
    >
      {a?.icon ?? <BookOpen className="h-3.5 w-3.5" />}
    </span>
  );
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: { key: T; label: string; count?: number | string; disabled?: boolean }[];
  value: T;
  onChange: (k: T) => void;
}) {
  return (
    <div className="flex gap-1 border-b border-slate-200">
      {tabs.map((t) => (
        <button
          key={t.key}
          disabled={t.disabled}
          onClick={() => onChange(t.key)}
          className={clsx(
            "relative -mb-px flex items-center gap-1.5 border-b-2 px-3 py-2.5 text-[13px] font-medium transition disabled:cursor-not-allowed disabled:opacity-40",
            value === t.key ? "border-brand-600 text-brand-700" : "border-transparent text-slate-500 hover:text-slate-800",
          )}
        >
          {t.label}
          {t.count !== undefined && (
            <span className={clsx("rounded-full px-1.5 text-[10px] tabular-nums", value === t.key ? "bg-brand-100 text-brand-700" : "bg-slate-100 text-slate-500")}>
              {t.count}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}

export function Bar({ value, tone = "brand" }: { value: number; tone?: "brand" | "green" | "amber" }) {
  const c = { brand: "bg-brand-500", green: "bg-emerald-500", amber: "bg-amber-500" }[tone];
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
      <div className={clsx("h-full rounded-full transition-all", c)} style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} />
    </div>
  );
}

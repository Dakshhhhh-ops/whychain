import clsx from "clsx";
import { Brain, Database, History, LayoutDashboard } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { api, type Status } from "../lib/api";
import { useDataset } from "../lib/dataset";
import { Badge, LiveDot } from "./ui";

const NAV = [
  { to: "/", label: "Control tower", icon: <LayoutDashboard className="h-4 w-4" />, end: true },
  { to: "/runs", label: "Investigations", icon: <History className="h-4 w-4" /> },
  { to: "/memory", label: "Memory", icon: <Brain className="h-4 w-4" /> },
  { to: "/sources", label: "Data sources", icon: <Database className="h-4 w-4" /> },
];

export function Logo() {
  return (
    <div className="flex items-center gap-2.5">
      <img src="/favicon.svg" alt="" className="h-8 w-8" />
      <div>
        <div className="text-[15px] font-semibold tracking-tight text-slate-900">WhyChain</div>
        <div className="text-[11px] text-slate-500">Supply chain investigator</div>
      </div>
    </div>
  );
}

function StatusRow({ ok, label, detail, below }: { ok: boolean | null; label: string; detail?: ReactNode; below?: boolean }) {
  if (below) {
    // a longer detail goes on its own line under the label, so neither is cut off in the narrow sidebar
    return (
      <div className="py-1">
        <div className="flex items-center gap-2 text-[12px] text-slate-700">
          <span className="flex shrink-0"><LiveDot tone={ok === null ? "amber" : ok ? "green" : "red"} /></span>
          <span className="whitespace-nowrap">{label}</span>
        </div>
        {detail && <div className="pl-4 text-[11px] text-slate-400">{detail}</div>}
      </div>
    );
  }
  return (
    <div className="flex items-center justify-between gap-2 py-1">
      <div className="flex min-w-0 items-center gap-2 text-[12px] text-slate-700">
        <span className="flex shrink-0"><LiveDot tone={ok === null ? "amber" : ok ? "green" : "red"} /></span>
        <span className="truncate whitespace-nowrap">{label}</span>
      </div>
      {detail && <div className="shrink-0 whitespace-nowrap text-[11px] text-slate-400">{detail}</div>}
    </div>
  );
}

export function useStatus() {
  const { key } = useDataset();
  const [s, setS] = useState<Status | null>(null);
  useEffect(() => {
    if (!key) return;
    let alive = true;
    const load = () => api.status(key).then((v) => alive && setS(v)).catch(() => {});
    load();
    const t = setInterval(load, 30000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [key]);
  return s;
}

function DatasetSwitch() {
  const { key, all, setKey } = useDataset();
  const nav = useNavigate();
  if (!all.length) return null;
  return (
    <div className="px-3 pb-3">
      <div className="mb-1.5 px-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Dataset</div>
      <div className="space-y-1.5">
        {all.map((d) => (
          <button
            key={d.key}
            disabled={!d.available}
            onClick={() => {
              setKey(d.key);
              nav("/");
            }}
            className={clsx(
              "w-full rounded-lg border px-2.5 py-2 text-left transition",
              key === d.key ? "border-brand-300 bg-brand-50/70 ring-1 ring-brand-200" : "border-slate-200 hover:bg-slate-50",
              !d.available && "cursor-not-allowed opacity-50",
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-[12.5px] font-semibold text-slate-800">{d.name.split(" (")[0]}</span>
              <Badge tone={d.real ? "green" : "amber"}>{d.real ? "Real data" : "Simulated"}</Badge>
            </div>
            <div className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-slate-500">{d.description}</div>
          </button>
        ))}
      </div>
    </div>
  );
}

const MODEL_LABEL: Record<string, string> = { xai: "Grok", nim: "Nemotron (fast)", "nim-reason": "Nemotron (reasoning)", "nim-backup": "Nemotron Ultra (backup)", nightshift: "Meterless Nightshift (this PC)", ollama: "Ollama" };

export function Layout() {
  const s = useStatus();
  const zetaris = s?.harness.name === "zetaris";
  return (
    <div className="flex h-full">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-slate-200 bg-white md:flex print:hidden">
        <div className="px-5 py-5">
          <Logo />
        </div>
        <DatasetSwitch />
        <nav className="space-y-0.5 px-3">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) =>
                clsx(
                  "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] font-medium transition",
                  isActive ? "bg-brand-50 text-brand-700" : "text-slate-600 hover:bg-slate-50 hover:text-slate-900",
                )
              }
            >
              {n.icon}
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto space-y-3 p-4">
          <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-3">
            <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Systems</div>
            {s ? (
              <>
                <StatusRow ok={zetaris ? s.harness.ok : null} label={zetaris ? "Zetaris" : "Local data (fallback)"} detail={zetaris ? "federated" : s.harness.erp} />
                <StatusRow ok={s.memory.online ? true : null} label={s.memory.online ? "Meterless memory" : "Memory (local file)"}
                  detail={s.memory.embedder?.includes("nemotron") ? "Nemotron embeddings" : undefined} below />
                {s.memory.scout && <StatusRow ok={true} label="Meterless Scout" detail="routes every question" below />}
                {/* only the model providers this install actually uses; optional ones that aren't set are not listed */}
                {Object.entries(s.models)
                  .filter(([, m]) => m.configured)
                  .map(([k]) => (
                    <StatusRow key={k} ok={true} label={MODEL_LABEL[k] ?? k} />
                  ))}
              </>
            ) : (
              <div className="py-2 text-[12px] text-slate-400">Connecting…</div>
            )}
          </div>
          {s && (
            <div className="px-1 text-[11px] leading-relaxed text-slate-400">
              {s.company?.replace(" (fictional)", "")}
              <br />
              Data as of {new Date(s.as_of).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}
            </div>
          )}
        </div>
      </aside>
      <main className="min-w-0 flex-1 overflow-y-auto">
        <div className="border-b border-slate-200 bg-white px-4 pt-3 md:hidden print:hidden">
          <Logo />
          <nav className="-mx-1 mt-2 flex gap-1 overflow-x-auto pb-2">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.end}
                className={({ isActive }) =>
                  clsx(
                    "flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12.5px] font-medium",
                    isActive ? "bg-brand-50 text-brand-700" : "text-slate-600",
                  )
                }
              >
                {n.icon}
                {n.label}
              </NavLink>
            ))}
          </nav>
        </div>
        <Outlet />
      </main>
    </div>
  );
}

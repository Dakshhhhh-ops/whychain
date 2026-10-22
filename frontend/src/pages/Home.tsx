import clsx from "clsx";
import {
  Anchor,
  ArrowRight,
  Boxes,
  Database,
  Factory,
  Info,
  KeyRound,
  Network as NetworkIcon,
  PackageX,
  Search,
  Sparkles,
  TrendingUp,
  Truck,
} from "lucide-react";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useStatus } from "../components/Layout";
import { MapLegend, NetworkMap } from "../components/NetworkMap";
import { EventDetail, Timeline, TimelineLegend } from "../components/Timeline";
import { AgentAvatar, Badge, Bar, Button, Card, CardHeader, Spinner } from "../components/ui";
import { api, getOverview, type ExternalEvent, type KpiCard, type Overview, type Problem, type RunSummary } from "../lib/api";
import { useDataset } from "../lib/dataset";
import { runDate } from "../lib/format";

const KIND_ICON: Record<string, ReactNode> = {
  transport: <Truck className="h-4 w-4" />,
  fulfilment: <PackageX className="h-4 w-4" />,
  external: <Anchor className="h-4 w-4" />,
  inventory: <Boxes className="h-4 w-4" />,
  supply: <Factory className="h-4 w-4" />,
  demand: <TrendingUp className="h-4 w-4" />,
};
const SEV = {
  critical: { bar: "bg-rose-500", icon: "bg-rose-50 text-rose-600", badge: "red" as const, label: "Critical" },
  high: { bar: "bg-amber-500", icon: "bg-amber-50 text-amber-600", badge: "amber" as const, label: "High" },
  medium: { bar: "bg-sky-500", icon: "bg-sky-50 text-sky-600", badge: "blue" as const, label: "Medium" },
};

const STEPS = [
  { agent: "Data Scout", title: "Map", text: "Learns the data model from the Zetaris catalog and tests every join." },
  { agent: "Planner", title: "Plan", text: "Measures the symptom, then sets up competing explanations." },
  { agent: "Investigator 1", title: "Investigate", text: "Investigators query the data; the Doc Analyst reads documents and customer reviews." },
  { agent: "Evaluator", title: "Challenge", text: "Re-runs every query and sends weak findings back for another round." },
  { agent: "Risk & Action", title: "Act", text: "Prices the fixes, and escalates expensive ones for a manager's approval." },
];

function Kpi({ c }: { c: KpiCard }) {
  return (
    <Card className="px-5 py-4">
      <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{c.label}</div>
      <div className={clsx("mt-1.5 text-[26px] font-semibold tracking-tight tabular-nums", c.tone === "bad" ? "text-rose-600" : "text-slate-900")}>
        {c.value}
      </div>
      {c.sub && <div className="mt-0.5 text-[12px] text-slate-500">{c.sub}</div>}
    </Card>
  );
}

export function Home() {
  const nav = useNavigate();
  const { key, info } = useDataset();
  const status = useStatus();
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const [recent, setRecent] = useState<RunSummary[]>([]);
  const [ov, setOv] = useState<Overview | null>(null);
  const [hover, setHover] = useState<Problem | null>(null);
  const [ev, setEv] = useState<ExternalEvent | null>(null);
  const [showAll, setShowAll] = useState(false);
  useEffect(() => {
    if (!key) return;
    setOv(null);
    setRecent([]);
    setQ("");
    getOverview(key).then(setOv).catch(() => {});
    api.runs(key).then((r) => setRecent(r.slice(0, 5))).catch(() => {});
  }, [key]);

  const start = async (question: string, id = "ask") => {
    if (question.trim().length < 8) return;
    setBusy(id);
    setErr("");
    try {
      const { run_id } = await api.start(question.trim(), key);
      nav(`/runs/${run_id}`);
    } catch (e) {
      setErr(String(e));
      setBusy(null);
    }
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    start(q);
  };
  const real = info?.real ?? false;
  const asOf = info ? new Date(info.as_of).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "";
  const samples = info?.samples ?? [];
  const highlight = hover?.when ? new Set([hover.when]) : undefined;

  return (
    <div className="mx-auto max-w-[1320px] px-6 py-7">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 text-[12px] font-medium text-slate-500">
            <span>Control tower · {info?.company ?? info?.name ?? "…"}</span>
            {info && <Badge tone={real ? "green" : "amber"}>{real ? "Real data" : "Simulated data"}</Badge>}
          </div>
          <h1 className="mt-1 text-[24px] font-semibold tracking-tight text-slate-900">
            {real ? "What went wrong, and why" : "What needs explaining today"}
          </h1>
          <div className="mt-0.5 text-[12.5px] text-slate-500">Data as of {asOf}</div>
        </div>
        <Badge tone={ov?.harness === "zetaris" ? "brand" : "slate"} className="px-2.5 py-1 text-[12px]">
          <NetworkIcon className="h-3.5 w-3.5" />
          {ov ? (ov.harness === "zetaris" ? "Live via Zetaris" : "Local fallback harness") : "Connecting…"}
        </Badge>
      </div>

      {status?.preview ? (
        <div className="mt-4 flex items-start gap-2.5 rounded-xl border border-sky-200 bg-sky-50/70 px-4 py-3 text-[13px] text-sky-900">
          <Info className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <span className="font-semibold">Preview mode.</span> Investigations here use a scripted stand-in model to show how the app works. The
            control-tower numbers are live; the investigation results are not real AI analysis.
          </div>
        </div>
      ) : status && !status.models_ready ? (
        <div className="mt-4 flex items-start gap-2.5 rounded-xl border border-amber-200 bg-amber-50/70 px-4 py-3 text-[13px] text-amber-900">
          <KeyRound className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <span className="font-semibold">No AI model is set up yet.</span> Add <span className="font-mono text-[12px]">NVIDIA_API_KEY</span> or{" "}
            <span className="font-mono text-[12px]">XAI_API_KEY</span> to the <span className="font-mono text-[12px]">.env</span> file and restart to run
            investigations. Everything else on this page is live.
          </div>
        </div>
      ) : null}

      {real && info?.source && (
        <div className="mt-4 flex items-start gap-2.5 rounded-xl border border-emerald-200 bg-emerald-50/50 px-4 py-2.5 text-[12.5px] text-emerald-900">
          <Database className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            Real commercial data: {info.source}. {Object.entries(info.row_counts).filter(([, v]) => v > 0).map(([k, v]) => `${v.toLocaleString()} ${k.replace("_", " ")}`).join(" · ")}.
          </div>
        </div>
      )}

      <form onSubmit={submit} className="mt-5">
        <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white p-1.5 pl-4 shadow-[0_1px_2px_rgba(15,23,42,0.04)] focus-within:border-brand-300 focus-within:ring-4 focus-within:ring-brand-100">
          <Search className="h-4 w-4 shrink-0 text-slate-400" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={samples[0] ? `Ask why something happened, e.g. ${samples[0].question}` : "Ask why something happened"}
            className="h-10 min-w-0 flex-1 bg-transparent text-[14.5px] text-slate-900 outline-none placeholder:text-slate-400"
          />
          <Button type="submit" size="lg" disabled={!!busy || q.trim().length < 8}>
            {busy === "ask" ? <Spinner /> : <Sparkles className="h-4 w-4" />}
            Investigate
          </Button>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="text-[11.5px] text-slate-400">Try:</span>
          {samples.map((s) => (
            <button
              key={s.title}
              type="button"
              onClick={() => setQ(s.question)}
              className="rounded-full border border-slate-200 bg-white px-2.5 py-0.5 text-[11.5px] text-slate-600 transition hover:border-brand-300 hover:text-brand-700"
            >
              {s.title}
            </button>
          ))}
        </div>
        {err && <div className="mt-2 text-sm text-rose-600">{err}</div>}
      </form>

      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {(ov?.kpi_cards ?? [0, 1, 2, 3].map(() => ({ label: "…", value: "—" }))).map((c, i) => (
          <Kpi key={i} c={c as KpiCard} />
        ))}
      </div>

      {ov?.timeline && (
        <Card className="mt-6">
          <CardHeader
            title="Delivery performance by month"
            subtitle={hover?.when ? `Highlighting: ${hover.title}` : "Orders delivered and the share delivered after the promised date. Hover a bar for details, or an incident to locate it."}
            right={<TimelineLegend events={!!ov.events?.length} />}
          />
          <div className="px-4 py-3">
            <Timeline points={ov.timeline} highlight={highlight} events={ov.events} activeEvent={ev?.id} onEvent={setEv} />
          </div>
          {!!ov.events?.length && (
            <div className="border-t border-slate-100 px-5 py-3">
              <EventDetail events={ov.events} active={ev} />
            </div>
          )}
        </Card>
      )}

      <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,1fr)_400px]">
        <Card>
          <CardHeader
            title={real ? "Incidents found in the data" : "Needs attention"}
            subtitle="Found by rules running on the data. Each one can be investigated."
            right={ov ? <Badge tone="red">{ov.problems.length} found</Badge> : undefined}
          />
          {!ov ? (
            <div className="flex justify-center p-10">
              <Spinner />
            </div>
          ) : (
            <ul className="divide-y divide-slate-100">
              {ov.problems.slice(0, showAll ? undefined : 6).map((p) => {
                const s = SEV[p.severity];
                return (
                  <li
                    key={p.id}
                    className="group relative flex gap-3.5 px-5 py-3.5 transition hover:bg-slate-50/70"
                    onMouseEnter={() => setHover(p)}
                    onMouseLeave={() => setHover(null)}
                  >
                    <span className={clsx("absolute inset-y-3 left-0 w-0.5 rounded-r", s.bar)} />
                    <div className={clsx("mt-0.5 rounded-lg p-2", s.icon)}>{KIND_ICON[p.kind] ?? <Boxes className="h-4 w-4" />}</div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[13.5px] font-semibold text-slate-900">{p.title}</span>
                        <Badge tone={s.badge}>{s.label}</Badge>
                      </div>
                      <div className="mt-0.5 text-[12.5px] text-slate-600">
                        <span className="font-semibold text-slate-800">{p.metric}</span>
                        <span className="text-slate-400"> · </span>
                        {p.baseline}
                      </div>
                      <div className="mt-0.5 text-[11.5px] text-slate-400">{p.detail}</div>
                    </div>
                    <div className="flex shrink-0 items-center">
                      <Button size="sm" variant="secondary" disabled={!!busy} onClick={() => start(p.question, p.id)} className="group-hover:border-brand-300 group-hover:text-brand-700">
                        {busy === p.id ? <Spinner className="h-3.5 w-3.5" /> : null}
                        Investigate why
                        <ArrowRight className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </li>
                );
              })}
              {ov.problems.length > 6 && (
                <li className="px-5 py-2.5">
                  <button onClick={() => setShowAll(!showAll)} className="text-[12.5px] font-medium text-brand-700 hover:underline">
                    {showAll ? "Show fewer" : `Show all ${ov.problems.length}`}
                  </button>
                </li>
              )}
            </ul>
          )}
        </Card>

        <div className="space-y-6">
          {ov?.states && ov.states.length > 0 && (
            <Card>
              <CardHeader title="Late deliveries by state" subtitle="All delivered orders, states with 300+ orders" />
              <ul className="divide-y divide-slate-100">
                {ov.states.slice(0, 8).map((s) => (
                  <li key={s.state} className="flex items-center gap-3 px-5 py-2">
                    <span className="w-8 font-mono text-[11px] font-semibold text-slate-500">{s.state}</span>
                    <span className="min-w-0 flex-1 truncate text-[12.5px] text-slate-700">{s.name}</span>
                    <div className="flex w-36 items-center gap-2 text-[11px] tabular-nums text-slate-500">
                      <Bar value={s.late_pct / 30} tone={s.late_pct >= 15 ? "amber" : "brand"} />
                      {s.late_pct}%
                    </div>
                  </li>
                ))}
              </ul>
            </Card>
          )}
          <Card>
            <CardHeader title="Recent investigations" right={<Link to="/runs" className="text-[12px] font-medium text-brand-700 hover:underline">All</Link>} />
            {recent.length === 0 ? (
              <div className="px-5 py-6 text-[12.5px] text-slate-500">No investigations on this dataset yet. Pick an incident to start one.</div>
            ) : (
              <ul className="divide-y divide-slate-100">
                {recent.map((r) => (
                  <li key={r.run_id}>
                    <Link to={`/runs/${r.run_id}`} className="block px-5 py-3 hover:bg-slate-50">
                      <div className="line-clamp-2 text-[12.5px] font-medium text-slate-800">{r.headline || r.question}</div>
                      <div className="mt-1 flex items-center gap-2 text-[11px] text-slate-500">
                        {r.status === "running" ? <Badge tone="blue">Running</Badge> : <span>{runDate(r.run_id)}</span>}
                        {!!r.causes && <span>· {r.causes} cause{r.causes === 1 ? "" : "s"}</span>}
                        {!!r.needs_approval && <Badge tone="amber">{r.needs_approval} to approve</Badge>}
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card>
            <CardHeader title="How an investigation works" subtitle="Agents check and challenge each other; it isn't a fixed pipeline." />
            <ol className="space-y-3 px-5 py-4">
              {STEPS.map((s) => (
                <li key={s.title} className="flex gap-3">
                  <AgentAvatar agent={s.agent} />
                  <div>
                    <div className="text-[12.5px] font-semibold text-slate-900">{s.title}</div>
                    <div className="text-[12px] leading-snug text-slate-500">{s.text}</div>
                  </div>
                </li>
              ))}
            </ol>
          </Card>
        </div>
      </div>

      {ov?.network && (
        <Card className="mt-6">
          <CardHeader
            title="Supply network"
            subtitle={hover ? `Highlighting: ${hover.title}` : "Suppliers → entry ports → DCs → customer cities, coloured by live status. Hover a problem to locate it."}
            right={<MapLegend showPath />}
          />
          <div className="px-4 py-3">
            <NetworkMap net={ov.network} highlight={hover ? new Set(hover.entities.flatMap((e) => expand(e, ov))) : undefined} />
          </div>
        </Card>
      )}
    </div>
  );
}

/** A problem's entity plus whatever it connects to (a lane -> its DC and city). */
function expand(id: string, ov: Overview): string[] {
  const out = [id];
  for (const e of ov.network?.edges ?? []) {
    if (e.id === id) out.push(e.from, e.to);
    if (id.startsWith("S-") && e.from === id) out.push(e.to);
  }
  return out;
}

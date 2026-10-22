import clsx from "clsx";
import { ArrowLeft, Brain, Calculator, Database, Download, FileText, GitBranch, Play, Radio } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ActivityFeed, describe, Stepper } from "../components/Activity";
import { Board } from "../components/Board";
import { BriefView } from "../components/Brief";
import { EvidenceBody, EvidenceDrawer } from "../components/Evidence";
import { AgentAvatar, Badge, Bar, Button, Card, CardHeader, EmptyState, LiveDot, Spinner, Stat, Tabs } from "../components/ui";
import { highlightFromText, MapLegend, NetworkMap } from "../components/NetworkMap";
import { api, getOverview, subscribe, type Evidence, type Overview, type RunResult, type TraceEvent, type WorldState } from "../lib/api";
import { duration, num, pct, runDate } from "../lib/format";
import { applyEvent, emptyState, foldEvents, type LiveState } from "../lib/runState";

type Tab = "brief" | "board" | "network" | "evidence" | "memory" | "trace";

export function Investigation() {
  const { id = "" } = useParams();
  const [state, setState] = useState<LiveState>(emptyState());
  const [result, setResult] = useState<RunResult | undefined>();
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<"loading" | "live" | "replay" | "static">("loading");
  const [tab, setTab] = useState<Tab>("board");
  const [openEv, setOpenEv] = useState<string | null>(null);
  const [error, setError] = useState("");
  const unsub = useRef<(() => void) | null>(null);
  const startedAt = useRef<number>(Date.now());
  const [now, setNow] = useState(Date.now());

  const loadResult = useCallback(async () => {
    const r = await api.run(id);
    if (r.status !== "running") {
      setResult(r);
      return r;
    }
    return undefined;
  }, [id]);

  const stream = useCallback(
    (replay: number) => {
      unsub.current?.();
      setState(emptyState());
      startedAt.current = Date.now();
      setMode(replay ? "replay" : "live");
      if (replay) {
        setResult(undefined); // don't reveal the verdicts before the replay reaches them
        setTab("board");
      }
      unsub.current = subscribe(
        id,
        (e) => setState((s) => applyEvent(s, e)),
        async () => {
          const r = await loadResult().catch(() => undefined);
          setMode("static");
          if (r) setTab("brief");
        },
        replay,
      );
    },
    [id, loadResult],
  );

  useEffect(() => {
    let cancelled = false;
    setResult(undefined);
    setError("");
    setMode("loading");
    api
      .run(id)
      .then(async (r) => {
        if (cancelled) return;
        setQuestion(r.question);
        if (r.status === "running") {
          setTab("board");
          stream(0);
        } else {
          const ev = await api.trace(id);
          if (cancelled) return;
          setResult(r);
          setState(foldEvents(ev));
          setMode("static");
          setTab(r.status === "ok" ? "brief" : "board");
        }
      })
      .catch((e) => setError(String(e)));
    return () => {
      cancelled = true;
      unsub.current?.();
    };
  }, [id, stream]);

  const evidence: Record<string, Evidence> = result?.evidence ?? {};
  const running = mode === "live" || mode === "replay";
  useEffect(() => {
    if (mode !== "live") return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [mode]);
  const elapsedShown = mode === "live" ? Math.max(state.elapsed, (now - startedAt.current) / 1000) : state.elapsed;
  const failed = state.status === "error" || result?.status === "error";
  const failure = state.error || (result?.status === "error" ? result.brief : "") || "";
  const q = question || state.question || "";
  const causes = result ? (result.findings ?? []).filter((f) => result.verdicts?.[f.task_id]?.status === "confirmed" && result.verdicts?.[f.task_id]?.is_cause) : [];
  const ruledOut = result ? (result.findings ?? []).filter((f) => result.verdicts?.[f.task_id]?.status === "rejected") : [];

  if (error)
    return (
      <div className="p-10">
        <EmptyState title="Couldn't load this investigation">{error}</EmptyState>
      </div>
    );

  return (
    <div className="mx-auto max-w-[1400px] px-6 py-6">
      <Link to="/runs" className="inline-flex items-center gap-1 text-[12px] font-medium text-slate-500 hover:text-slate-800">
        <ArrowLeft className="h-3.5 w-3.5" />
        Investigations
      </Link>
      <div className="mt-2 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 max-w-4xl">
          <h1 className="text-[22px] font-semibold leading-snug tracking-tight text-slate-900">{q || <span className="text-slate-400">Loading…</span>}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[12px] text-slate-500">
            {running ? (
              <Badge tone="blue">
                <LiveDot />
                {mode === "replay" ? "Replaying" : "Investigating"}
              </Badge>
            ) : state.status === "error" || result?.status === "error" ? (
              <Badge tone="red">Failed</Badge>
            ) : mode === "loading" ? (
              <Badge>Loading</Badge>
            ) : (
              <Badge tone="green">Complete</Badge>
            )}
            <span className="font-mono">{id}</span>
            <span>· {runDate(id)}</span>
            <span className="tabular-nums">· {duration(elapsedShown)}</span>
            <Badge tone={state.harness === "zetaris" || result?.harness === "zetaris" ? "brand" : "slate"}>
              <Database className="h-3 w-3" />
              {(result?.harness ?? state.harness) === "zetaris" ? "Zetaris" : result?.harness ?? state.harness ?? "…"}
            </Badge>
            <Badge tone={(result?.memory?.backend ?? state.memoryBackend) === "meterless" ? "green" : "slate"}>
              <Brain className="h-3 w-3" />
              {(result?.memory?.backend ?? state.memoryBackend) === "meterless" ? "Meterless memory" : "Local memory"}
            </Badge>
          </div>
        </div>
        <div className="flex gap-2 print:hidden">
          {mode === "static" && (
            <Button variant="secondary" onClick={() => stream(6)}>
              <Play className="h-3.5 w-3.5" />
              Replay
            </Button>
          )}
          <a
            href={`/api/runs/${id}/trace.jsonl`}
            className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3.5 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50"
          >
            <Download className="h-3.5 w-3.5" />
            Trace
          </a>
        </div>
      </div>

      <div className="mt-5 print:hidden">
        <Stepper state={state} />
      </div>

      {failed && (
        <div className="mt-5 rounded-xl border border-rose-200 bg-rose-50/70 px-5 py-4">
          <div className="text-[14px] font-semibold text-rose-900">This investigation couldn't finish</div>
          <div className="mt-1 break-words font-mono text-[12px] text-rose-800">{failure.slice(0, 400)}</div>
          {/provider|model|api key|connection/i.test(failure) && (
            <div className="mt-2 text-[13px] text-rose-900">
              No AI model could be reached. Add <span className="font-mono">NVIDIA_API_KEY</span> or <span className="font-mono">XAI_API_KEY</span> to the{" "}
              <span className="font-mono">.env</span> file, restart the app, then run the investigation again.
            </div>
          )}
        </div>
      )}

      <Card className="mt-5 print:hidden">
        <div className="grid grid-cols-2 gap-5 px-5 py-4 sm:grid-cols-3 lg:grid-cols-6">
          <Stat label="Root causes" value={result ? causes.length : "—"} hint={result ? `${ruledOut.length} ruled out` : undefined} />
          <Stat label="Hypotheses" value={Object.keys(state.tasks).length} hint={`${state.rounds.length} round${state.rounds.length === 1 ? "" : "s"}`} />
          <Stat label="SQL queries" value={num(state.queries)} hint={state.sqlErrors ? `${state.sqlErrors} repaired` : "via data harness"} />
          <Stat label="Evidence" value={result ? Object.keys(evidence).length : "—"} hint={`${Object.values(state.reverified).filter(Boolean).length} re-verified`} />
          <Stat label="Model calls" value={num(state.llmCalls)} hint={state.fallbacks ? `${state.fallbacks} fallbacks` : "no fallbacks"} />
          <Stat label="Escalations" value={state.escalations.length} hint="need approval" />
        </div>
      </Card>

      <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0">
          <div className="print:hidden">
          <Tabs<Tab>
            value={tab}
            onChange={setTab}
            tabs={[
              { key: "brief", label: "Brief", disabled: !result || result.status !== "ok" },
              { key: "board", label: "Investigation", count: Object.keys(state.tasks).length },
              { key: "network", label: "Network", disabled: (result?.dataset ?? state.dataset) === "olist" },
              { key: "evidence", label: "Evidence", count: result ? Object.keys(evidence).length : undefined, disabled: !result },
              { key: "memory", label: "Memory & graph", disabled: !result },
              { key: "trace", label: "Trace", count: state.events.length },
            ]}
          />
          </div>
          <div className="mt-4">
            {tab === "brief" && result && <BriefView result={result} onOpen={setOpenEv} onDecided={() => loadResult()} />}
            {tab === "board" && <Board state={state} result={result} onOpen={setOpenEv} />}
            {tab === "network" && <NetworkTab state={state} result={result} />}
            {tab === "evidence" && result && <EvidenceTab result={result} />}
            {tab === "memory" && result && <MemoryTab result={result} />}
            {tab === "trace" && <TraceTab events={state.events} onOpen={setOpenEv} />}
          </div>
        </div>
        <div className="xl:sticky xl:top-6 xl:self-start print:hidden">
          <Card>
            <CardHeader
              title="Agent activity"
              icon={<Radio className="h-4 w-4" />}
              right={running ? <LiveDot /> : <span className="text-[11px] text-slate-400">{state.events.length} events</span>}
            />
            <ActivityFeed events={state.events} live={running} onOpen={setOpenEv} />
          </Card>
        </div>
      </div>
      <EvidenceDrawer ev={openEv ? evidence[openEv] ?? null : null} onClose={() => setOpenEv(null)} />
    </div>
  );
}

function NetworkTab({ state, result }: { state: LiveState; result?: RunResult }) {
  const [ov, setOv] = useState<Overview | null>(null);
  useEffect(() => {
    getOverview(result?.dataset ?? state.dataset ?? "kaveri").then(setOv).catch(() => {});
  }, []);
  // text of every confirmed cause (final verdicts if we have them, live verdicts otherwise)
  const causeText = Object.values(state.tasks)
    .filter((t) => {
      const v = result?.verdicts?.[t.id];
      return v ? v.status === "confirmed" && v.is_cause !== false && t.type !== "measure" : t.evalStatus === "confirmed" && t.type !== "measure";
    })
    .map((t) => {
      const f = result?.findings?.find((x) => x.task_id === t.id);
      const quotes = (f?.evidence_ids ?? []).map((id) => result?.evidence?.[id]).filter((e) => e?.type === "doc").map((e) => (e as { quote: string }).quote);
      return [t.statement, f?.claim ?? t.claim ?? "", ...quotes].join(" ");
    })
    .join(" ");
  if (!ov)
    return (
      <Card className="flex justify-center p-10">
        <Spinner />
      </Card>
    );
  if (!ov.network)
    return (
      <Card>
        <EmptyState title="No supply network for this dataset">The marketplace data has no warehouses or supplier network to map.</EmptyState>
      </Card>
    );
  const net = ov.network;
  const hl = highlightFromText(causeText, net);
  const named = net.nodes.filter((n) => hl.has(n.id));
  return (
    <Card>
      <CardHeader
        title="Where the cause sits in the network"
        subtitle={causeText ? "Nodes and lanes named in the confirmed causes are highlighted." : "Highlights appear once the Evaluator confirms a cause."}
        right={<MapLegend showPath />}
      />
      <div className="px-4 py-3">
        <NetworkMap net={net} highlight={hl} />
      </div>
      {named.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 border-t border-slate-100 px-5 py-3">
          <span className="text-[12px] text-slate-500">Implicated:</span>
          {named.map((n) => (
            <Badge key={n.id} tone="brand">
              {n.label}
              <span className="text-brand-500/70">{n.type}</span>
            </Badge>
          ))}
        </div>
      )}
    </Card>
  );
}

function EvidenceTab({ result }: { result: RunResult }) {
  const [filter, setFilter] = useState<"all" | "sql" | "doc" | "optimization">("all");
  const items = Object.values(result.evidence ?? {}).filter((e) => filter === "all" || e.type === filter);
  const task = (id: string) => result.tasks?.find((t) => t.id === id);
  return (
    <div className="space-y-3">
      <div className="flex gap-1.5">
        {(["all", "sql", "doc", "optimization"] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={clsx(
              "rounded-full px-3 py-1 text-[12px] font-medium ring-1 ring-inset",
              filter === f ? "bg-slate-900 text-white ring-slate-900" : "bg-white text-slate-600 ring-slate-200 hover:bg-slate-50",
            )}
          >
            {f === "all" ? "All" : f === "sql" ? "SQL results" : f === "doc" ? "Documents" : "Optimal plans"}
          </button>
        ))}
      </div>
      {items.map((ev) => (
        <details key={ev.id} className="group rounded-xl border border-slate-200 bg-white open:shadow-sm" open={ev.type === "doc"}>
          <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-3">
            <span
              className={clsx(
                "rounded-lg p-1.5",
                ev.type === "doc" ? "bg-teal-50 text-teal-600" : ev.type === "optimization" ? "bg-violet-50 text-violet-600" : "bg-brand-50 text-brand-600",
              )}
            >
              {ev.type === "doc" ? <FileText className="h-4 w-4" /> : ev.type === "optimization" ? <Calculator className="h-4 w-4" /> : <Database className="h-4 w-4" />}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="font-mono text-[12px] font-semibold text-slate-800">{ev.id}</span>
                <span className="truncate text-[12px] text-slate-500">{ev.agent}</span>
              </div>
              <div className="truncate text-[12.5px] text-slate-600">
                {ev.type === "doc" ? ev.title ?? ev.doc_id : ev.purpose || task(ev.task_id)?.statement}
                {ev.type === "optimization" ? " · proven optimal" : ""}
              </div>
            </div>
            {ev.type === "sql" && ev.reproduced !== undefined && (
              <Badge tone={ev.reproduced ? "green" : "red"}>{ev.reproduced ? "Re-verified" : "Not reproduced"}</Badge>
            )}
          </summary>
          <div className="border-t border-slate-100 px-4 py-4">
            <EvidenceBody ev={ev} />
          </div>
        </details>
      ))}
    </div>
  );
}

function MemoryTab({ result }: { result: RunResult }) {
  const [world, setWorld] = useState<WorldState | null>(null);
  useEffect(() => {
    api.world(result.run_id).then(setWorld).catch(() => setWorld({}));
  }, [result.run_id]);
  const joins = result.data_map?.joins ?? [];
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Recalled at the start" subtitle="Earlier conclusions from Meterless H-MEM, given to the Planner" icon={<Brain className="h-4 w-4" />} />
        {(result.memories ?? []).length ? (
          <ul className="divide-y divide-slate-100">
            {(result.memories ?? []).map((m, i) => (
              <li key={m.id ?? i} className="flex gap-3 px-5 py-3">
                <Badge tone={m.kind === "lesson" ? "violet" : m.kind === "finding" ? "green" : "slate"}>{m.kind}</Badge>
                <div className="min-w-0 flex-1 text-[12.5px] leading-snug text-slate-700">{m.content}</div>
                {m.tier && <span className="shrink-0 text-[11px] text-slate-400">{m.tier.replace("_", "-")}</span>}
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState title="Nothing recalled">This was the first investigation on this topic.</EmptyState>
        )}
      </Card>
      <Card>
        <CardHeader title="Cause graph" subtitle="Relations written to the Meterless World Model" icon={<GitBranch className="h-4 w-4" />} />
        {world === null ? (
          <div className="p-5">
            <Spinner />
          </div>
        ) : (world.relations ?? []).length ? (
          <ul className="divide-y divide-slate-100">
            {(world.relations ?? []).map((r, i) => (
              <li key={i} className="grid grid-cols-[1fr_auto_1fr] items-center gap-3 px-5 py-2.5 text-[12.5px]">
                <span className="text-slate-800">{r.from_name}</span>
                <Badge tone={r.type === "causes" ? "red" : "slate"}>{r.type}</Badge>
                <span className="text-slate-600">{r.to_name}</span>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState title="No graph yet">Start the Meterless bridge to record the cause graph.</EmptyState>
        )}
      </Card>
      {joins.length > 0 && (
        <Card>
          <CardHeader title="Data map used" subtitle="Joins the Data Scout verified against the data" icon={<Database className="h-4 w-4" />} />
          <ul className="divide-y divide-slate-100">
            {joins.map((j, i) => (
              <li key={i} className="flex items-center gap-4 px-5 py-2.5">
                <div className="min-w-0 flex-1 font-mono text-[11.5px] text-slate-700">
                  {j.left} <span className="text-slate-400">=</span> {j.right}
                </div>
                <div className="flex w-36 items-center gap-2 text-[11px] text-slate-500">
                  <Bar value={j.match_rate ?? 0} tone="green" />
                  {pct(j.match_rate)}
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

function TraceTab({ events, onOpen }: { events: TraceEvent[]; onOpen: (id: string) => void }) {
  const [agent, setAgent] = useState("all");
  const agents = useMemo(() => Array.from(new Set(events.map((e) => e.agent))), [events]);
  const rows = events.filter((e) => agent === "all" || e.agent === agent);
  return (
    <Card>
      <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
        <select value={agent} onChange={(e) => setAgent(e.target.value)} className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-[12px]">
          <option value="all">All agents</option>
          {agents.map((a) => (
            <option key={a}>{a}</option>
          ))}
        </select>
        <span className="text-[11px] text-slate-400">{rows.length} events · every model call, query and decision</span>
      </div>
      <div className="max-h-[640px] overflow-auto">
        <table className="w-full text-left text-[12px]">
          <tbody>
            {rows.map((e) => (
              <tr key={e.seq} className="border-b border-slate-100 align-top">
                <td className="whitespace-nowrap px-3 py-1.5 font-mono text-[11px] tabular-nums text-slate-400">{Number(e.t).toFixed(1)}s</td>
                <td className="whitespace-nowrap px-2 py-1.5">
                  <span className="inline-flex items-center gap-1.5">
                    <AgentAvatar agent={e.agent} size="sm" />
                    <span className="text-slate-700">{e.agent}</span>
                  </span>
                </td>
                <td className="whitespace-nowrap px-2 py-1.5 font-mono text-[11px] text-slate-500">{e.kind}</td>
                <td className="px-2 py-1.5 text-slate-600">
                  {describe(e, onOpen) ??
                    (e.kind === "llm_call" ? `${e.provider} · ${e.model} · ${e.ms} ms` : <span className="text-slate-400">{JSON.stringify(e).slice(0, 120)}</span>)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

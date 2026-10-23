import clsx from "clsx";
import { Check } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { TraceEvent } from "../lib/api";
import { plural } from "../lib/format";
import { STAGES, type LiveState } from "../lib/runState";
import { AgentAvatar, Spinner } from "./ui";

const code = (s: ReactNode) => <span className="rounded bg-slate-100 px-1 font-mono text-[11px] text-slate-700">{s}</span>;

/** One readable line per meaningful event. Returns null for events we don't show.
 *  `plain` leaves out what only an engineer needs (evidence ids, intent codes, timings, join tests). */
export function describe(e: TraceEvent, onOpen?: (id: string) => void, plain = false): ReactNode | null {
  // in plain mode a link reads as words, not as an evidence id
  const link = (id: string | undefined, text: ReactNode) =>
    id ? (
      <button onClick={() => onOpen?.(id)} className="text-left text-brand-700 hover:underline">
        {text}
      </button>
    ) : (
      text
    );
  if (plain)
    switch (e.kind) {
      case "join_test":
        return null;
      case "query":
        return e.ok ? <>Ran {link(e.evidence_id, "a query")} · {plural(e.rows as number, "row", "rows")}</> : <span className="text-amber-700">A query failed; rewriting it</span>;
      case "read_doc":
        return <>Read a document from the written record</>;
      case "analysis":
        return (
          <>
            Computed {link(e.evidence_id as string, String(e.title ?? "a result"))}
            {e.verdict && e.verdict !== "measured" ? <span className="font-medium text-slate-800"> · {String(e.verdict).replace(/_/g, " ")}</span> : null}
          </>
        );
      case "optimize":
        if (!e.ok) break;
        return (
          <>
            Found the {link(e.evidence_id, "best stock transfer")} (proven optimal) · {e.truck_units ?? 0} units by truck, {e.air_units ?? 0} by air
            {e.unfilled_units ? `, ${e.unfilled_units} can't arrive in time` : ""}
          </>
        );
      case "reverify":
        return <>Re-checked {link(e.evidence_id, "a result")}: <span className={e.reproduced ? "text-emerald-700" : "text-rose-600"}>{e.reproduced ? "same answer" : "did not reproduce"}</span></>;
      case "scout":
        if (e.route === "refuse") return <span className="text-amber-700">Refused: the question contained instructions aimed at the agents</span>;
        if (e.route === "off_topic") return <>This question isn't about this company's data</>;
        return <>Read the question and sent it to {e.route_label}</>;
      case "llm_fallback":
        return <span className="text-amber-700">A model was busy; switched to the backup</span>;
      case "run_end":
        return <>Finished</>;
    }
  const ev = (id?: string) =>
    id ? (
      <button onClick={() => onOpen?.(id)} className="font-mono text-[11px] font-medium text-brand-700 hover:underline">
        {id}
      </button>
    ) : null;
  switch (e.kind) {
    case "run_start":
      return <>Started investigation on {code(e.harness === "zetaris" ? "Zetaris" : e.harness)}</>;
    case "agent_start":
      if (e.agent === "Memory") return <>Recalling earlier investigations from Meterless memory</>;
      if (e.agent === "Data Scout") return <>Mapping the company's data sources</>;
      if (e.agent === "Planner") return <>Planning round {e.round}</>;
      if (e.agent === "Evaluator") return <>Reviewing round {e.round} findings</>;
      if (e.agent === "Risk & Action") return <>Working out actions for {(e.causes ?? []).length} confirmed cause(s)</>;
      if (e.agent === "Reporter") return <>Writing the brief</>;
      if (e.task_id) return <span className="text-slate-600">{e.statement}</span>;
      return null;
    case "agent_end":
      if (e.agent === "Memory") return <>Recalled {plural(e.memories as number, "memory", "memories")}{e.data_map_cached ? ", plus a cached data map" : ""}</>;
      if (e.agent === "Data Scout") return <>Mapped {e.sources} sources, verified {e.joins_verified} joins</>;
      return null;
    case "agent_skip":
      return <span className="text-slate-500">{e.note}</span>;
    case "join_test":
      return (
        <>
          Join {code(e.left)} = {code(e.right)}{" "}
          <span className={e.kept ? "text-emerald-700" : "text-rose-600"}>
            {e.match_rate === null ? "failed" : `${Math.round(e.match_rate * 100)}% match`} · {e.kept ? "kept" : "dropped"}
          </span>
        </>
      );
    case "plan":
      return <>Planned {plural((e.tasks ?? []).length, "task", "tasks")} for round {e.round}</>;
    case "query":
      return e.ok ? (
        <>
          Ran SQL {ev(e.evidence_id)} · {e.rows} rows · {e.ms} ms
        </>
      ) : (
        <span className="text-rose-600">SQL error, repairing: {String(e.error ?? "").slice(0, 90)}</span>
      );
    case "read_doc":
      return <>Read {code(e.doc_id)}</>;
    case "analysis":
      return (
        <>
          Computed {ev(e.evidence_id as string)}: {String(e.title ?? "")}
          {e.verdict && e.verdict !== "measured" ? <span className="font-medium text-slate-800"> · {String(e.verdict)}</span> : null}
        </>
      );
    case "optimize":
      return e.ok ? (
        <>
          Solved {ev(e.evidence_id)} with Optiagent: <span className="font-medium text-slate-800">proven optimal</span> ·{" "}
          {e.truck_units ?? 0} units by truck, {e.air_units ?? 0} by air
          {e.unfilled_units ? `, ${e.unfilled_units} can't arrive in time` : ""}
        </>
      ) : (
        <span className="text-rose-600">Optimizer: {String(e.error ?? e.outcome ?? "not verified").slice(0, 120)}</span>
      );
    case "finding":
      return (
        <>
          <span className="font-medium text-slate-800">{e.verdict}</span>: {String(e.claim ?? "").slice(0, 160)}
        </>
      );
    case "quote_rejected":
      return <span className="text-rose-600">Discarded a quote not found in {code(e.doc_id)}</span>;
    case "reverify":
      return (
        <>
          Re-ran {ev(e.evidence_id)}: <span className={e.reproduced ? "text-emerald-700" : "text-rose-600"}>{e.reproduced ? "reproduced" : "did not reproduce"}</span>
        </>
      );
    case "evaluation":
      return (
        <>
          Decision: <span className={clsx("font-semibold", e.decision === "revise" ? "text-amber-700" : "text-emerald-700")}>{e.decision}</span>
          {e.decision === "revise" ? ` · ${(e.gaps ?? []).length} gap(s) sent back` : ""}
        </>
      );
    case "escalation":
      return <span className="text-amber-800">Escalated {e.action_id} for approval: {(e.reasons ?? []).join("; ")}</span>;
    case "scout":
      return e.available ? (
        <>
          Meterless Scout: {code(String(e.intent))} ({e.confidence}, {e.band}) → {e.route_label}
          <span className="text-slate-400"> · decided by {e.decided_by}{e.contract_id ? ` · signed contract ${String(e.contract_id).slice(-8)}` : ""}</span>
        </>
      ) : (
        <span className="text-slate-500">Routed by WhyChain's rules to {e.route_label} (Meterless Scout not reachable)</span>
      );
    case "preliminary":
      return <span className="font-medium text-brand-700">Preliminary answer ready, computed from the data in {e.seconds} s</span>;
    case "llm_fallback":
      return <span className="text-amber-700">Model {code(e.provider)} failed; fell back to the next provider</span>;
    case "memory_write":
      return <>Saved {plural(e.items as number, "memory", "memories")}{e.consolidation?.promoted ? `, promoted ${e.consolidation.promoted} to long-term` : ""}</>;
    case "run_error":
    case "agent_error":
      return <span className="text-rose-600">{String(e.error ?? "").slice(0, 200)}</span>;
    case "run_end":
      return <>Finished · {e.calls} model calls</>;
    default:
      return null;
  }
}

const TECH_KEY = "whychain.activity.technical";

export function ActivityFeed({ events, live, onOpen }: { events: TraceEvent[]; live: boolean; onOpen: (id: string) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [technical, setTechnical] = useState(() => {
    try {
      return localStorage.getItem(TECH_KEY) === "1";
    } catch {
      return false;
    }
  });
  const toggle = () => {
    setTechnical(!technical);
    try {
      localStorage.setItem(TECH_KEY, technical ? "0" : "1");
    } catch {
      /* not stored; fine */
    }
  };
  const items = events.map((e) => ({ e, node: describe(e, onOpen, !technical) })).filter((x) => x.node !== null);
  useEffect(() => {
    if (live && ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [items.length, live]);
  return (
    <div ref={ref} className="max-h-[calc(100vh-260px)] min-h-64 overflow-y-auto px-4 py-3">
      <label className="mb-2 flex cursor-pointer items-center justify-end gap-1.5 text-[11px] text-slate-500">
        <input type="checkbox" checked={technical} onChange={toggle} className="h-3 w-3 accent-brand-600" />
        Technical detail
      </label>
      {items.length === 0 && <div className="py-8 text-center text-xs text-slate-400">Waiting for agents…</div>}
      <ol className="relative space-y-3 before:absolute before:bottom-2 before:left-[13px] before:top-2 before:w-px before:bg-slate-200">
        {items.map(({ e, node }) => (
          <li key={e.seq} className="relative flex animate-fade-in gap-2.5">
            <AgentAvatar agent={e.agent} />
            <div className="min-w-0 flex-1 pt-0.5">
              <div className="flex items-baseline justify-between gap-2">
                <span className="truncate text-[12px] font-semibold text-slate-800">{technical ? e.agent : String(e.agent ?? "").replace(/ T\d+$/, "").replace(/^Scout$/, "Meterless Scout")}</span>
                <span className="shrink-0 font-mono text-[10px] tabular-nums text-slate-400">{Number(e.t).toFixed(1)}s</span>
              </div>
              <div className="mt-0.5 break-words text-[12px] leading-snug text-slate-600">{node}</div>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function Stepper({ state }: { state: LiveState }) {
  const order = STAGES.map((s) => s.key);
  const cur = state.stage === "remember" || state.stage === "done" ? order.length : order.indexOf(state.stage);
  const evalRounds = state.rounds.filter((r) => r.evaluation).length;
  return (
    <div className="flex items-center gap-1 overflow-x-auto">
      {STAGES.map((s, i) => {
        const done = i < cur || state.status === "done";
        const active = i === cur && state.status === "running";
        return (
          <div key={s.key} className="flex items-center gap-1">
            <div
              className={clsx(
                "flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-[12px] font-medium transition",
                done && "bg-emerald-50 text-emerald-700",
                active && "bg-brand-600 text-white shadow-sm",
                !done && !active && "text-slate-400",
              )}
            >
              {done ? <Check className="h-3.5 w-3.5" /> : active ? <Spinner className="h-3.5 w-3.5" /> : <span className="h-1.5 w-1.5 rounded-full bg-slate-300" />}
              {s.label}
              {s.key === "evaluate" && evalRounds > 1 && <span className="opacity-80">×{evalRounds}</span>}
              {s.key === "scout" && state.scout.skipped && <span className="opacity-80">(memory)</span>}
            </div>
            {i < STAGES.length - 1 && <div className={clsx("h-px w-4", i < cur ? "bg-emerald-300" : "bg-slate-200")} />}
          </div>
        );
      })}
    </div>
  );
}

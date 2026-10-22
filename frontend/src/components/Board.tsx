import clsx from "clsx";
import { AlertTriangle, ArrowDown, CornerDownLeft, Database, FileText, Ruler, Scale } from "lucide-react";
import type { RunResult } from "../lib/api";
import { pct } from "../lib/format";
import type { LiveState, LiveTask } from "../lib/runState";
import { EvidenceChip } from "./Evidence";
import { AgentAvatar, Badge, Bar, Card, Spinner, VerdictBadge } from "./ui";

function TaskCard({ t, result, onOpen }: { t: LiveTask; result?: RunResult; onOpen: (id: string) => void }) {
  const verdict = result?.verdicts?.[t.id];
  const finding = result?.findings?.find((f) => f.task_id === t.id);
  const status = verdict?.status ?? t.evalStatus;
  const claim = finding?.claim ?? t.claim;
  const evidence = finding?.evidence_ids ?? t.evidence;
  const isDocs = t.agent === "docs";
  return (
    <div
      className={clsx(
        "flex flex-col rounded-xl border bg-white p-4 transition",
        t.state === "running" && !status ? "border-sky-300 shadow-[0_0_0_3px_rgba(14,165,233,0.08)]" : "border-slate-200",
        status === "rejected" && "bg-slate-50/60",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-[11px] font-semibold text-slate-500">{t.id}</span>
          <Badge tone={isDocs ? "teal" : "blue"}>
            {isDocs ? <FileText className="h-3 w-3" /> : <Database className="h-3 w-3" />}
            {isDocs ? "Documents" : "Data"}
          </Badge>
          {t.type === "measure" && (
            <Badge tone="violet">
              <Ruler className="h-3 w-3" />
              Measure
            </Badge>
          )}
          {verdict?.is_cause && status === "confirmed" && <Badge tone="green">Root cause</Badge>}
        </div>
        <VerdictBadge status={status} running={t.state === "running"} queued={t.state === "queued"} />
      </div>
      <div className={clsx("mt-2.5 text-[13px] font-medium leading-snug", status === "rejected" ? "text-slate-500" : "text-slate-900")}>{t.statement}</div>
      {!claim && t.state === "running" && t.lastThought && (
        <div className="mt-2 flex animate-fade-in items-start gap-1.5 text-[12px] italic leading-snug text-sky-800" key={t.lastThought}>
          <Spinner className="mt-0.5 h-3 w-3 shrink-0" />
          {t.lastThought}
        </div>
      )}
      {claim && <div className="mt-2 text-[12.5px] leading-relaxed text-slate-600">{claim}</div>}
      {verdict?.reason && (
        <div className="mt-2.5 flex gap-1.5 rounded-lg bg-fuchsia-50/60 px-2.5 py-2 text-[12px] leading-snug text-fuchsia-900">
          <Scale className="mt-0.5 h-3.5 w-3.5 shrink-0 text-fuchsia-500" />
          <span>{verdict.reason}</span>
        </div>
      )}
      <div className="mt-auto flex flex-wrap items-center gap-1.5 pt-3">
        {evidence.map((id) => (
          <EvidenceChip key={id} id={id} onOpen={onOpen} missing={!!result && !result.evidence?.[id]} />
        ))}
        <span className="ml-auto text-[11px] tabular-nums text-slate-400">
          {t.queries > 0 && `${t.queries} quer${t.queries === 1 ? "y" : "ies"}`}
          {t.docs > 0 && `${t.queries ? " · " : ""}${t.docs} doc${t.docs === 1 ? "" : "s"} read`}
          {t.sqlErrors > 0 && ` · ${t.sqlErrors} SQL repaired`}
        </span>
      </div>
    </div>
  );
}

export function Board({ state, result, onOpen }: { state: LiveState; result?: RunResult; onOpen: (id: string) => void }) {
  if (!state.rounds.length)
    return (
      <Card className="p-8 text-center text-sm text-slate-500">
        {state.scout.skipped ? "Data map recalled from memory. " : ""}The Planner is preparing hypotheses…
      </Card>
    );
  return (
    <div className="space-y-4">
      {state.rounds.map((r, idx) => {
        const evalFull = result?.evaluations?.find((e) => e.round === r.round);
        const ev = r.evaluation;
        return (
          <div key={r.round} className="space-y-3">
            <div className="flex items-start gap-3">
              <AgentAvatar agent="Planner" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-[13px] font-semibold text-slate-900">Round {r.round}</span>
                  <span className="text-xs text-slate-500">Planner · {r.taskIds.length} task{r.taskIds.length === 1 ? "" : "s"}</span>
                </div>
                {r.symptom && (
                  <div className="mt-1 text-[12.5px] text-slate-700">
                    <span className="font-medium">Symptom:</span> {r.symptom}
                  </div>
                )}
                {r.reasoning && <div className="mt-0.5 text-[12.5px] leading-relaxed text-slate-500">{r.reasoning}</div>}
              </div>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              {r.taskIds.map((id) => state.tasks[id] && <TaskCard key={id} t={state.tasks[id]} result={result} onOpen={onOpen} />)}
            </div>
            {ev && (
              <div
                className={clsx(
                  "rounded-xl border px-4 py-3",
                  ev.decision === "revise" ? "border-amber-200 bg-amber-50/50" : "border-emerald-200 bg-emerald-50/40",
                )}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <AgentAvatar agent="Evaluator" size="sm" />
                  <span className="text-[13px] font-semibold text-slate-900">Evaluator</span>
                  <Badge tone={ev.decision === "revise" ? "amber" : "green"}>
                    {ev.decision === "revise" ? <CornerDownLeft className="h-3 w-3" /> : null}
                    {ev.decision === "revise" ? "Sent back for another round" : ev.forced ? "Accepted (round limit)" : "Accepted"}
                  </Badge>
                  {typeof ev.explained === "number" && (
                    <div className="ml-auto flex w-56 items-center gap-2 text-[11px] text-slate-500">
                      <span className="whitespace-nowrap">Explains {pct(ev.explained)} of symptom</span>
                      <Bar value={ev.explained} tone={ev.explained >= 0.7 ? "green" : "amber"} />
                    </div>
                  )}
                </div>
                {(evalFull?.summary ?? ev.summary) && <div className="mt-2 text-[12.5px] leading-relaxed text-slate-700">{evalFull?.summary ?? ev.summary}</div>}
                {(ev.gaps ?? []).length > 0 && (
                  <ul className="mt-2 space-y-1">
                    {(ev.gaps ?? []).map((g, i) => (
                      <li key={i} className="flex gap-1.5 text-[12px] text-amber-900">
                        <CornerDownLeft className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                        <span>
                          <span className="font-medium">Gap:</span> {g.what}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                {(ev.red_flags ?? []).length > 0 && (
                  <ul className="mt-2 space-y-1">
                    {(ev.red_flags ?? []).map((f, i) => (
                      <li key={i} className="flex gap-1.5 text-[12px] text-rose-800">
                        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                        {f}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
            {idx < state.rounds.length - 1 && (
              <div className="flex justify-center text-slate-300">
                <ArrowDown className="h-4 w-4" />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

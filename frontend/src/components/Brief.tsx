import clsx from "clsx";
import { AlertTriangle, Check, CheckCircle2, Clock, Copy, Download, Lightbulb, Printer, ShieldAlert, ShieldCheck, Target, XCircle } from "lucide-react";
import { DeliverablesPanel } from "./Deliverables";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type Action, type RunResult } from "../lib/api";
import { money } from "../lib/dataset";
import { CITATION } from "../lib/format";
import { EvidenceChip } from "./Evidence";
import { Badge, Button, Card, CardHeader } from "./ui";

export function Markdown({ text, onOpen, known }: { text: string; onOpen: (id: string) => void; known: Record<string, unknown> }) {
  const linked = text.replace(CITATION, (_m, id: string) => `[${id}](#ev-${id})`);
  return (
    <div className="prose prose-slate prose-sm max-w-none prose-headings:font-semibold prose-headings:text-slate-900 prose-p:leading-relaxed prose-li:my-0.5 prose-table:my-3 prose-td:py-1.5 prose-th:py-1.5">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => {
            if (href?.startsWith("#ev-")) {
              const id = href.slice(4);
              return <EvidenceChip id={id} onOpen={onOpen} missing={!known[id]} />;
            }
            return (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          },
        }}
      >
        {linked}
      </ReactMarkdown>
    </div>
  );
}

export function ActionCard({ a, runId, onOpen, onDecided }: { a: Action; runId: string; onOpen: (id: string) => void; onDecided: () => void }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const decide = async (d: "approved" | "rejected") => {
    setBusy(true);
    try {
      await api.decide(runId, a.id, d, note || undefined);
      onDecided();
    } finally {
      setBusy(false);
    }
  };
  const decided = a.decision === "approved" || a.decision === "rejected";
  return (
    <div
      className={clsx(
        "rounded-xl border p-4",
        a.needs_approval && !decided ? "border-amber-300 bg-amber-50/30" : "border-slate-200 bg-white",
        a.decision === "approved" && "border-emerald-300 bg-emerald-50/30",
        a.decision === "rejected" && "border-slate-200 bg-slate-50 opacity-80",
      )}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[11px] font-semibold text-slate-500">{a.id}</span>
            {a.needs_approval ? (
              <Badge tone="amber">
                <ShieldAlert className="h-3 w-3" />
                Needs approval
              </Badge>
            ) : (
              <Badge tone="green">
                <ShieldCheck className="h-3 w-3" />
                Within limits
              </Badge>
            )}
          </div>
          <div className="mt-1.5 text-[14px] font-semibold text-slate-900">{a.title}</div>
        </div>
        <div className="shrink-0 text-right">
          <div className="text-[11px] uppercase tracking-wide text-slate-500">Est. cost</div>
          {a.est_cost_inr === null || a.est_cost_inr === undefined ? (
            <div className="max-w-[140px] text-[12px] font-medium text-slate-500">Not computed: no cost data</div>
          ) : (
            <div className="text-lg font-semibold tabular-nums text-slate-900">{money(a.est_cost_inr, a.currency)}</div>
          )}
        </div>
      </div>
      {a.steps && <p className="mt-2 text-[13px] leading-relaxed text-slate-600">{a.steps}</p>}
      {!!a.unsupported_figures?.length && (
        <p className="mt-1.5 text-[11.5px] text-amber-700">
          Removed figures the agent stated but its cited data does not contain: {a.unsupported_figures.join(", ")}
        </p>
      )}
      <div className="mt-3 grid grid-cols-2 gap-3 border-t border-slate-100 pt-3 text-[12px] sm:grid-cols-3">
        <div>
          <div className="text-slate-500">Protects</div>
          <div className="font-medium tabular-nums text-slate-800">
            {a.orders_protected ? `${a.orders_protected} orders` : "—"}
            {a.value_protected_inr ? ` · ${money(Number(a.value_protected_inr), a.currency)}` : ""}
          </div>
        </div>
        <div>
          <div className="text-slate-500">Takes effect</div>
          <div className="flex items-center gap-1 font-medium text-slate-800">
            <Clock className="h-3 w-3 text-slate-400" />
            {a.time_to_effect || "—"}
          </div>
        </div>
        <div>
          <div className="text-slate-500">Evidence</div>
          <div className="mt-0.5 flex flex-wrap gap-1">
            {a.evidence.length ? a.evidence.map((id) => <EvidenceChip key={id} id={id} onOpen={onOpen} />) : <span className="text-slate-400">—</span>}
          </div>
        </div>
      </div>
      {a.approval_reasons.length > 0 && (
        <div className="mt-3 flex gap-1.5 text-[12px] text-amber-900">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600" />
          Escalated because {a.approval_reasons.join("; ")}.
        </div>
      )}
      {a.policy_notes && <div className="mt-1.5 text-[12px] text-slate-500">Policy: {a.policy_notes}</div>}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {decided ? (
          <div className={clsx("flex items-center gap-1.5 text-[13px] font-medium", a.decision === "approved" ? "text-emerald-700" : "text-slate-600")}>
            {a.decision === "approved" ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
            {a.decision === "approved" ? "Approved" : "Rejected"}
            {a.decision_note ? <span className="font-normal text-slate-500">· “{a.decision_note}”</span> : null}
            <span className="font-normal text-slate-400">· saved to memory</span>
          </div>
        ) : (
          <>
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Add a note (optional)"
              className="h-8 min-w-48 flex-1 rounded-lg border border-slate-200 bg-white px-2.5 text-[12px] outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-100"
            />
            <Button size="sm" variant="success" disabled={busy} onClick={() => decide("approved")}>
              <CheckCircle2 className="h-3.5 w-3.5" />
              Approve
            </Button>
            <Button size="sm" variant="danger" disabled={busy} onClick={() => decide("rejected")}>
              <XCircle className="h-3.5 w-3.5" />
              Reject
            </Button>
          </>
        )}
      </div>
      {a.decision === "approved" && <DeliverablesPanel runId={runId} actionId={a.id} />}
    </div>
  );
}

function briefMarkdown(result: RunResult) {
  const acts = (result.actions ?? [])
    .map((a) => `- **${a.id}. ${a.title}** · est. ${a.est_cost_inr === null ? "not computed" : money(a.est_cost_inr, a.currency)} · ${a.needs_approval ? "needs approval" : "within limits"} · ${a.decision}\n  ${a.steps ?? ""}`)
    .join("\n");
  return `# ${result.headline ?? "Investigation"}\n\n_Question:_ ${result.question}\n\n${result.brief ?? ""}\n\n## Actions\n${acts}\n\n_WhyChain run ${result.run_id}_\n`;
}

function ExportBar({ result }: { result: RunResult }) {
  const [copied, setCopied] = useState(false);
  const md = briefMarkdown(result);
  const download = () => {
    const url = URL.createObjectURL(new Blob([md], { type: "text/markdown" }));
    const el = document.createElement("a");
    el.href = url;
    el.download = `whychain-brief-${result.run_id}.md`;
    el.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="flex gap-1.5 print:hidden">
      <Button
        size="sm"
        variant="secondary"
        onClick={() => {
          navigator.clipboard?.writeText(md);
          setCopied(true);
          setTimeout(() => setCopied(false), 1200);
        }}
      >
        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
        {copied ? "Copied" : "Copy"}
      </Button>
      <Button size="sm" variant="secondary" onClick={download}>
        <Download className="h-3.5 w-3.5" />
        Markdown
      </Button>
      <Button size="sm" variant="secondary" onClick={() => window.print()}>
        <Printer className="h-3.5 w-3.5" />
        Print / PDF
      </Button>
    </div>
  );
}

export function BriefView({ result, onOpen, onDecided }: { result: RunResult; onOpen: (id: string) => void; onDecided: () => void }) {
  const known = result.evidence ?? {};
  const actions = result.actions ?? [];
  const currency = actions[0]?.currency ?? (result.dataset === "olist" ? "BRL" : "INR");
  return (
    <div className="space-y-4">
      <Card>
        <div className="px-6 py-5">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-brand-600">
              <Target className="h-3.5 w-3.5" />
              Answer
            </div>
            <ExportBar result={result} />
          </div>
          <h2 className="mt-1.5 text-xl font-semibold leading-snug tracking-tight text-slate-900">{result.headline || "Investigation complete"}</h2>
        </div>
        <div className="border-t border-slate-100 px-6 py-5">
          <Markdown text={result.brief ?? ""} onOpen={onOpen} known={known} />
        </div>
        {result.brief_check && result.brief_check.checked > 0 && (
          <div
            className={`border-t px-6 py-2.5 text-[12px] ${
              result.brief_check.unverified.length ? "border-amber-100 bg-amber-50/60 text-amber-800" : "border-slate-100 text-slate-500"
            }`}
          >
            Numbers check: {result.brief_check.checked - result.brief_check.unverified.length} of {result.brief_check.checked} numbers in this
            brief trace to cited evidence.
            {result.brief_check.unverified.length > 0 && ` Marked "(unverified)": ${result.brief_check.unverified.join(", ")}.`}
          </div>
        )}
      </Card>

      {actions.length > 0 && (
        <Card>
          <CardHeader
            title="Recommended actions"
            subtitle="Grounded in the data and policies. Anything over the approval threshold needs a decision."
            right={
              <Badge tone={actions.some((a) => a.needs_approval && a.decision === "pending") ? "amber" : "slate"}>
                {actions.filter((a) => a.decision === "pending").length} pending
              </Badge>
            }
          />
          <div className="space-y-3 p-4">
            {actions.map((a) => (
              <ActionCard key={a.id} a={a} runId={result.run_id} onOpen={onOpen} onDecided={onDecided} />
            ))}
          </div>
        </Card>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        {(result.risks ?? []).length > 0 && (
          <Card>
            <CardHeader title="Risks if nothing changes" icon={<AlertTriangle className="h-4 w-4" />} />
            <ul className="space-y-2.5 p-4">
              {(result.risks ?? []).map((r, i) => (
                <li key={i} className="text-[13px] text-slate-700">
                  <div className="flex items-start justify-between gap-3">
                    <span>{r.risk}</span>
                    {r.exposure ?? r.exposure_inr ? (
                      <span className="shrink-0 font-semibold tabular-nums text-rose-700">{money(Number(r.exposure ?? r.exposure_inr), currency)}</span>
                    ) : null}
                  </div>
                  {(r.evidence ?? []).length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {(r.evidence ?? []).map((id) => (
                        <EvidenceChip key={id} id={id} onOpen={onOpen} missing={!known[id]} />
                      ))}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </Card>
        )}
        {(result.lessons ?? []).length > 0 && (
          <Card>
            <CardHeader title="Lessons saved to memory" subtitle="Meterless H-MEM · used by future investigations" icon={<Lightbulb className="h-4 w-4" />} />
            <ul className="space-y-2 p-4">
              {(result.lessons ?? []).map((l, i) => (
                <li key={i} className="flex gap-2 text-[13px] text-slate-700">
                  <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" />
                  {l}
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>
    </div>
  );
}

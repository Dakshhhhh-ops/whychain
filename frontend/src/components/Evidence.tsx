import clsx from "clsx";
import { Calculator, Check, Copy, Database, FileText, Mail, ShieldCheck, ShieldX, Sigma, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import type { AnalysisEvidence, Evidence, OptimizationEvidence, SqlEvidence } from "../lib/api";
import { inr, num, pct } from "../lib/format";
import { Badge } from "./ui";

const KW =
  /\b(SELECT|FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|JOIN|LEFT JOIN|INNER JOIN|ON|AS|AND|OR|NOT|IN|IS|NULL|CASE|WHEN|THEN|ELSE|END|WITH|DISTINCT|BETWEEN|LIKE|DESC|ASC|UNION|ALL|CAST|DATE|INTERVAL|OVER|PARTITION BY)\b/gi;

/** Lightweight SQL highlighter (keywords, functions, strings, numbers). */
export function SqlBlock({ sql, className }: { sql: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  const pretty = sql
    .replace(/\s+/g, " ")
    .replace(/\s(FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|LEFT JOIN|INNER JOIN|JOIN|UNION)\s/gi, "\n$1 ")
    .trim();
  const parts: ReactNode[] = [];
  // the last alternative catches any single character, so nothing is ever dropped
  const re = /('[^']*')|(\b\d+(?:\.\d+)?\b)|(\b[a-z_]+(?=\())|([A-Za-z_]+(?:\s+BY)?)|(\s+|[^\sA-Za-z_'\d]+|[\s\S])/gi;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(pretty))) {
    const [tok] = m;
    if (m[1]) parts.push(<span key={i++} className="sql-str">{tok}</span>);
    else if (m[2]) parts.push(<span key={i++} className="sql-num">{tok}</span>);
    else if (m[3]) parts.push(<span key={i++} className="sql-fn">{tok}</span>);
    else if (m[4] && new RegExp(`^${KW.source}$`, "i").test(tok)) parts.push(<span key={i++} className="sql-kw">{tok.toUpperCase()}</span>);
    else parts.push(tok);
  }
  return (
    <div className={clsx("group relative rounded-lg border border-slate-200 bg-slate-50/70", className)}>
      <pre className="overflow-x-auto whitespace-pre-wrap break-words px-3.5 py-3 font-mono text-[12px] leading-relaxed text-slate-800">{parts}</pre>
      <button
        onClick={() => {
          navigator.clipboard?.writeText(sql);
          setCopied(true);
          setTimeout(() => setCopied(false), 1200);
        }}
        className="absolute right-2 top-2 rounded-md border border-slate-200 bg-white p-1 text-slate-400 opacity-0 transition group-hover:opacity-100 hover:text-slate-700"
        title="Copy SQL"
      >
        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      </button>
    </div>
  );
}

export function DataTable({ columns, rows, max = 30 }: { columns: string[]; rows: unknown[][]; max?: number }) {
  if (!rows.length) return <div className="text-xs text-slate-500">No rows returned.</div>;
  return (
    <div className="max-h-80 overflow-auto rounded-lg border border-slate-200">
      <table className="w-full border-collapse text-left text-[12px]">
        <thead className="sticky top-0 bg-slate-50">
          <tr>
            {columns.map((c) => (
              <th key={c} className="whitespace-nowrap border-b border-slate-200 px-3 py-1.5 font-semibold text-slate-600">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="font-mono tabular-nums">
          {rows.slice(0, max).map((r, i) => (
            <tr key={i} className="odd:bg-white even:bg-slate-50/50">
              {r.map((v, j) => (
                <td key={j} className="whitespace-nowrap border-b border-slate-100 px-3 py-1 text-slate-700">
                  {v === null || v === undefined ? <span className="text-slate-300">null</span> : Array.isArray(v) ? v.join(", ") : String(v)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SqlMeta({ ev }: { ev: SqlEvidence }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Badge tone="brand">
        <Database className="h-3 w-3" />
        {ev.harness === "zetaris" ? "Zetaris" : ev.harness}
      </Badge>
      <Badge>
        {ev.row_count}
        {ev.truncated ? "+" : ""} rows · {ev.ms} ms
      </Badge>
      {ev.reproduced === true && (
        <Badge tone="green">
          <ShieldCheck className="h-3 w-3" />
          Re-verified
        </Badge>
      )}
      {ev.reproduced === false && (
        <Badge tone="red">
          <ShieldX className="h-3 w-3" />
          Did not reproduce
        </Badge>
      )}
    </div>
  );
}

function OptimizationBody({ ev }: { ev: OptimizationEvidence }) {
  const [showInputs, setShowInputs] = useState(false);
  const s = ev.summary;
  const scope = Object.entries(ev.scope)
    .map(([k, v]) => `${k} ${Array.isArray(v) ? v.join(", ") : String(v)}`)
    .join(" · ");
  return (
    <div className="space-y-3">
      {ev.purpose && <div className="text-[13px] text-slate-600">{ev.purpose}</div>}
      <div className="rounded-lg border border-violet-200 bg-violet-50/60 px-3 py-2.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone="violet">
            <Calculator className="h-3 w-3" />
            Optimal plan · Optiagent
          </Badge>
          <Badge tone="green">
            <ShieldCheck className="h-3 w-3" />
            Proven optimal
          </Badge>
          <Badge>
            {ev.stats.variables} variables · {ev.stats.constraints} constraints
          </Badge>
        </div>
        <div className="mt-1.5 text-[12px] leading-snug text-violet-900">
          Every number comes from the data (backorders, spare stock after {ev.cover_days} days of policy cover, lane rates,
          air-freight terms). The plan passed {ev.checks.filter((c) => c.passed).length}/{ev.checks.length} checks against
          the data, and a duality certificate ({ev.certified_with}) proves no cheaper plan exists.
        </div>
        <div className="mt-1 text-[11.5px] text-violet-700">
          Scope: {scope}
          {ev.to_warehouses.length ? ` · to ${ev.to_warehouses.join(", ")}` : ""} · must arrive within {ev.deadline_days} days
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[
          ["Units needed", num(s.need_units), `${s.skus} SKUs`],
          ["By truck", num(s.truck_units), s.trucks.map((t) => `${t.lane} ×${t.trucks}`).join(", ") || "none"],
          ["By air", num(s.air_units), inr(s.air_cost_inr)],
          ["Can't arrive in time", num(s.unfilled_units), `${pct(s.covered_share)} covered`],
        ].map(([label, value, hint]) => (
          <div key={label} className="rounded-lg border border-slate-200 bg-white px-3 py-2">
            <div className="text-[10.5px] font-medium uppercase tracking-wide text-slate-500">{label}</div>
            <div className="text-[17px] font-semibold tabular-nums text-slate-900">{value}</div>
            <div className="truncate text-[11px] text-slate-500">{hint}</div>
          </div>
        ))}
      </div>
      {s.trucks.length > 0 && (
        <div className="text-[12px] text-slate-600">
          Trucks:{" "}
          {s.trucks.map((t) => `${t.lane} ${num(t.units)} units on ${t.trucks} truck${t.trucks === 1 ? "" : "s"} (${inr(t.cost_inr)}, ${t.transit_days} day${t.transit_days === 1 ? "" : "s"})`).join(" · ")}
        </div>
      )}
      {(ev.notes ?? []).map((n, i) => (
        <div key={i} className="text-[12px] text-amber-800">
          {n}
        </div>
      ))}
      <DataTable columns={ev.columns} rows={ev.rows} max={60} />
      {ev.insights.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2.5">
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">What drives the cost</div>
          <ul className="space-y-1">
            {ev.insights.slice(0, 4).map((t, i) => (
              <li key={i} className="text-[12px] leading-snug text-slate-700">
                {t}
              </li>
            ))}
          </ul>
        </div>
      )}
      <button onClick={() => setShowInputs(!showInputs)} className="text-[12px] font-medium text-brand-700 hover:underline">
        {showInputs ? "Hide" : "Show"} the data queries behind the model
      </button>
      {showInputs && (
        <div className="space-y-2">
          {Object.entries(ev.queries).map(([k, q]) => (
            <div key={k}>
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">{k}</div>
              <SqlBlock sql={q} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const VERDICT_LABEL: Record<string, { text: string; tone: "green" | "red" | "amber" | "slate" }> = {
  supports: { text: "Supports", tone: "green" },
  partial: { text: "Partial: added to an existing problem", tone: "amber" },
  refutes: { text: "Refutes", tone: "red" },
  measured: { text: "Measured", tone: "slate" },
};

function AnalysisBody({ ev }: { ev: AnalysisEvidence }) {
  const v = VERDICT_LABEL[ev.verdict] ?? VERDICT_LABEL.measured;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[14px] font-semibold text-slate-900">{ev.title}</span>
        <Badge tone={v.tone}>{v.text}</Badge>
        {ev.harness && (
          <Badge tone="brand">
            <Database className="h-3 w-3" />
            {ev.harness === "zetaris" ? "Data via Zetaris" : `Data via ${ev.harness}`}
          </Badge>
        )}
      </div>
      <div className="flex items-start gap-1.5 rounded-lg bg-indigo-50/70 px-3 py-2 text-[12px] leading-relaxed text-indigo-900">
        <Sigma className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>
          <span className="font-semibold">Computed by the analysis engine, not a language model.</span> {ev.method}
        </span>
      </div>
      <DataTable columns={ev.columns} rows={ev.rows} />
      {ev.sql && (
        <details className="text-[12px] text-slate-500">
          <summary className="cursor-pointer select-none hover:text-slate-700">The federated query these numbers come from</summary>
          <div className="mt-2">
            <SqlBlock sql={ev.sql} />
          </div>
        </details>
      )}
    </div>
  );
}

export function EvidenceBody({ ev }: { ev: Evidence }) {
  if (ev.type === "optimization") return <OptimizationBody ev={ev} />;
  if (ev.type === "analysis") return <AnalysisBody ev={ev} />;
  if (ev.type === "doc") {
    const isEmail = ev.doc_type === "email";
    return (
      <div className="space-y-3">
        <div className="flex items-start gap-2.5 rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2.5">
          <div className="mt-0.5 text-teal-600">{isEmail ? <Mail className="h-4 w-4" /> : <FileText className="h-4 w-4" />}</div>
          <div className="min-w-0 text-xs">
            <div className="font-semibold text-slate-800">{ev.title ?? ev.doc_id}</div>
            <div className="mt-0.5 text-slate-500">
              {ev.doc_type}
              {ev.sent_at ? ` · ${ev.sent_at}` : ""}
            </div>
            {isEmail && (
              <div className="mt-1 text-slate-500">
                <span className="text-slate-400">From</span> {ev.sender} <span className="text-slate-400">to</span> {ev.recipients}
              </div>
            )}
          </div>
        </div>
        <blockquote className="border-l-2 border-teal-400 pl-3 text-[13px] leading-relaxed text-slate-700">"{ev.quote}"</blockquote>
        <div className="flex items-center gap-1.5 text-[11px] text-emerald-700">
          <ShieldCheck className="h-3.5 w-3.5" />
          Quote verified word-for-word against the document
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      {ev.purpose && <div className="text-[13px] text-slate-600">{ev.purpose}</div>}
      <SqlMeta ev={ev} />
      <SqlBlock sql={ev.sql} />
      <DataTable columns={ev.columns} rows={ev.rows} />
    </div>
  );
}

export function EvidenceChip({ id, onOpen, missing, label, hint }: { id: string; onOpen: (id: string) => void; missing?: boolean; label?: string; hint?: string }) {
  const isDoc = /\.D\d+$/.test(id);
  const isOpt = /\.P\d+$/.test(id);
  const isAna = /\.A\d+$/.test(id);
  return (
    <button
      onClick={() => onOpen(id)}
      disabled={missing}
      className={clsx(
        "inline-flex items-center gap-1 rounded-md py-0.5 align-baseline text-[11px] font-medium ring-1 ring-inset transition",
        label ? "min-w-[1.4rem] justify-center px-1.5 tabular-nums" : "px-1.5 font-mono",
        isOpt
          ? "bg-violet-50 text-violet-700 ring-violet-200 hover:bg-violet-100"
          : isAna
            ? "bg-indigo-50 text-indigo-700 ring-indigo-200 hover:bg-indigo-100"
          : isDoc
            ? "bg-teal-50 text-teal-700 ring-teal-200 hover:bg-teal-100"
            : "bg-brand-50 text-brand-700 ring-brand-200 hover:bg-brand-100",
        missing && "cursor-default opacity-50",
      )}
      title={missing ? "Evidence not available yet" : `${label ? `Source ${label}: ` : ""}${hint || (isDoc ? "a document" : isOpt ? "an optimisation" : isAna ? "a computed result" : "a query result")} · click to see the data and how it was computed`}
    >
      {/* a numbered source is just its number; the type icon stays on raw ids (evidence page, trace) */}
      {!label && (isOpt ? <Calculator className="h-3 w-3" /> : isAna ? <Sigma className="h-3 w-3" /> : isDoc ? <FileText className="h-3 w-3" /> : <Database className="h-3 w-3" />)}
      {label ?? id}
    </button>
  );
}

export function EvidenceDrawer({ ev, onClose }: { ev: Evidence | null; onClose: () => void }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  if (!ev) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-slate-900/20 backdrop-blur-[1px]" onClick={onClose} />
      <aside className="relative flex h-full w-full max-w-2xl animate-slide-in flex-col bg-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3.5">
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm font-semibold text-slate-900">{ev.id}</span>
            <Badge tone={ev.type === "doc" ? "teal" : ev.type === "optimization" ? "violet" : ev.type === "analysis" ? "indigo" : "brand"}>
              {ev.type === "doc" ? "Document" : ev.type === "optimization" ? "Optimal plan" : ev.type === "analysis" ? "Analysis" : "SQL query"}
            </Badge>
            <span className="text-xs text-slate-500">{ev.agent}</span>
          </div>
          <button onClick={onClose} className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-5">
          <EvidenceBody ev={ev} />
        </div>
      </aside>
    </div>
  );
}

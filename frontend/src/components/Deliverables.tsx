import { Check, Copy, Database, Download, FileText, Mail, Wrench, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { getDeliverables, type Deliverable, type Deliverables } from "../lib/api";
import { DataTable, SqlBlock } from "./Evidence";
import { Badge, Spinner } from "./ui";

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      onClick={() => {
        navigator.clipboard?.writeText(text);
        setDone(true);
        setTimeout(() => setDone(false), 1200);
      }}
      className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] font-medium text-slate-600 hover:bg-slate-50"
    >
      {done ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
      {done ? "Copied" : "Copy"}
    </button>
  );
}

function DownloadLink({ href }: { href: string }) {
  return (
    <a href={href} className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] font-medium text-slate-600 hover:bg-slate-50">
      <Download className="h-3 w-3" />
      Download
    </a>
  );
}

function Item({ d, href }: { d: Deliverable; href: string }) {
  const [showSql, setShowSql] = useState(false);
  if (d.kind === "table")
    return (
      <div className="rounded-lg border border-slate-200 bg-white">
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-3 py-2">
          <Database className="h-3.5 w-3.5 text-brand-600" />
          <span className="text-[12.5px] font-semibold text-slate-800">{d.title}</span>
          <Badge>{d.row_count} rows</Badge>
          {/\.P\d+$/.test(d.source) && <Badge tone="violet">Proven-optimal plan (Optiagent)</Badge>}
          <button onClick={() => setShowSql(!showSql)} className="text-[11px] font-medium text-brand-700 hover:underline">
            computed by {d.source}
          </button>
          <span className="ml-auto flex gap-1.5">
            <DownloadLink href={href} />
          </span>
        </div>
        <div className="space-y-2 p-3">
          {d.note && <div className="text-[12px] text-slate-500">{d.note}</div>}
          {showSql && <SqlBlock sql={d.sql} />}
          <DataTable columns={d.columns} rows={d.rows} max={50} />
        </div>
      </div>
    );
  if (d.kind === "email")
    return (
      <div className="rounded-lg border border-slate-200 bg-white">
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-3 py-2">
          <Mail className="h-3.5 w-3.5 text-teal-600" />
          <span className="text-[12.5px] font-semibold text-slate-800">{d.title}</span>
          <Badge tone="teal">Draft email</Badge>
          <span className="ml-auto flex gap-1.5">
            <CopyButton text={`To: ${d.to}\nSubject: ${d.subject}\n\n${d.body}`} />
            <DownloadLink href={href} />
          </span>
        </div>
        <div className="px-3 py-2.5 text-[12.5px]">
          <div className="text-slate-500">
            <span className="text-slate-400">To:</span> {d.to}
          </div>
          <div className="text-slate-500">
            <span className="text-slate-400">Subject:</span> <span className="font-medium text-slate-800">{d.subject}</span>
          </div>
          <pre className="mt-2 whitespace-pre-wrap font-sans text-[12.5px] leading-relaxed text-slate-700">{d.body}</pre>
        </div>
      </div>
    );
  return (
    <div className="rounded-lg border border-slate-200 bg-white">
      <div className="flex items-center gap-2 border-b border-slate-100 px-3 py-2">
        <FileText className="h-3.5 w-3.5 text-slate-500" />
        <span className="text-[12.5px] font-semibold text-slate-800">{d.title}</span>
        <span className="ml-auto flex gap-1.5">
          <CopyButton text={d.body} />
          <DownloadLink href={href} />
        </span>
      </div>
      <pre className="whitespace-pre-wrap px-3 py-2.5 font-sans text-[12.5px] leading-relaxed text-slate-700">{d.body}</pre>
    </div>
  );
}

/** Deliverables the Action Executor produced for an approved action. Polls while it works. */
export function DeliverablesPanel({ runId, actionId }: { runId: string; actionId: string }) {
  const [d, setD] = useState<Deliverables | null>(null);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const v = await getDeliverables(runId, actionId);
        if (!alive) return;
        setD(v);
        if (v.status === "running" || v.status === "none") timer = setTimeout(load, 1500);
      } catch {
        if (alive) timer = setTimeout(load, 3000);
      }
    };
    load();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [runId, actionId]);

  if (!d || d.status === "none" || d.status === "running")
    return (
      <div className="mt-3 flex items-center gap-2 rounded-lg border border-sky-200 bg-sky-50/60 px-3 py-2.5 text-[12.5px] text-sky-900">
        <Spinner className="h-3.5 w-3.5" />
        <Wrench className="h-3.5 w-3.5" />
        <span>
          <span className="font-semibold">Action Executor</span> is preparing the deliverables
          {d?.steps ? ` · ${d.steps} step${d.steps === 1 ? "" : "s"}` : ""}
          {d?.last ? <span className="text-sky-700"> · {d.last}</span> : "…"}
        </span>
      </div>
    );
  if (d.status === "error")
    return (
      <div className="mt-3 flex items-center gap-2 rounded-lg border border-rose-200 bg-rose-50/60 px-3 py-2.5 text-[12.5px] text-rose-800">
        <XCircle className="h-3.5 w-3.5" />
        The Action Executor couldn't finish: {d.error}
      </div>
    );
  return (
    <div className="mt-3 space-y-2.5">
      <div className="text-[12px] text-slate-600">
        <div className="flex items-center gap-1.5 font-semibold text-slate-800">
          <Wrench className="h-3.5 w-3.5 text-amber-600" />
          Prepared by the Action Executor
        </div>
        {d.summary && <div className="mt-0.5 pl-5">{d.summary}</div>}
      </div>
      {d.deliverables.map((x, i) => (
        <Item key={i} d={x} href={`/api/runs/${runId}/actions/${actionId}/deliverables/${i}`} />
      ))}
      {(d.rejected ?? []).length > 0 && (
        <div className="text-[11.5px] text-slate-400">
          {(d.rejected ?? []).length} draft table(s) discarded because they weren't backed by a query.
        </div>
      )}
    </div>
  );
}

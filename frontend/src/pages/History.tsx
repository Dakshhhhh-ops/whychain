import { History as HistoryIcon, ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Badge, Button, Card, EmptyState, Spinner } from "../components/ui";
import { api, type RunSummary } from "../lib/api";
import { useDataset } from "../lib/dataset";
import { duration, runDate } from "../lib/format";

export function PageHeader({ title, subtitle, right }: { title: string; subtitle?: string; right?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-[22px] font-semibold tracking-tight text-slate-900">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-[14px] text-slate-500">{subtitle}</p>}
      </div>
      {right}
    </div>
  );
}

export function History() {
  const nav = useNavigate();
  const { key, info } = useDataset();
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  useEffect(() => {
    if (!key) return;
    setRuns(null);
    api.runs(key).then(setRuns).catch(() => setRuns([]));
  }, [key]);
  return (
    <div className="mx-auto max-w-6xl px-6 py-8">
      <PageHeader
        title="Investigations"
        subtitle={`${info?.name ?? ""}: every investigation keeps its full trace, evidence and decisions.`}
        right={
          <Link to="/">
            <Button>New investigation</Button>
          </Link>
        }
      />
      <Card className="mt-6 overflow-hidden">
        {runs === null ? (
          <div className="flex justify-center p-10">
            <Spinner />
          </div>
        ) : runs.length === 0 ? (
          <EmptyState icon={<HistoryIcon className="h-5 w-5" />} title="No investigations yet">
            Ask a question on the home page to start one.
          </EmptyState>
        ) : (
          <table className="w-full text-left text-[13px]">
            <thead className="border-b border-slate-200 bg-slate-50/70 text-[11px] uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-5 py-2.5 font-semibold">Question and answer</th>
                <th className="px-3 py-2.5 font-semibold">Causes</th>
                <th className="px-3 py-2.5 font-semibold">Evidence</th>
                <th className="px-3 py-2.5 font-semibold">Actions</th>
                <th className="px-3 py-2.5 font-semibold">Duration</th>
                <th className="px-5 py-2.5 font-semibold">When</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {runs.map((r) => (
                <tr key={r.run_id} onClick={() => nav(`/runs/${r.run_id}`)} className="cursor-pointer align-top transition hover:bg-slate-50">
                  <td className="max-w-xl px-5 py-3.5">
                    <div className="font-medium text-slate-900">{r.question}</div>
                    {r.headline && <div className="mt-1 line-clamp-2 text-[12.5px] text-slate-500">{r.headline}</div>}
                  </td>
                  <td className="px-3 py-3.5 tabular-nums">
                    {r.status === "running" ? <Badge tone="blue">Running</Badge> : r.status === "error" ? <Badge tone="red">Failed</Badge> : r.status === "incomplete" ? <Badge tone="amber">No brief</Badge> : r.causes ?? "—"}
                  </td>
                  <td className="px-3 py-3.5 tabular-nums text-slate-600">{r.evidence ?? "—"}</td>
                  <td className="px-3 py-3.5">
                    <span className="tabular-nums text-slate-600">{r.actions ?? "—"}</span>
                    {!!r.needs_approval && (
                      <Badge tone="amber" className="ml-2">
                        <ShieldAlert className="h-3 w-3" />
                        {r.needs_approval}
                      </Badge>
                    )}
                  </td>
                  <td className="px-3 py-3.5 tabular-nums text-slate-600">{duration(r.duration_s)}</td>
                  <td className="whitespace-nowrap px-5 py-3.5 text-slate-500">{runDate(r.run_id)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

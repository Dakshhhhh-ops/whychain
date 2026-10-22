import { AlertTriangle, Boxes, ChevronRight, Database, GitMerge, Globe, Mail, Package, Truck } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Badge, Bar, Card, CardHeader, Spinner } from "../components/ui";
import { api, type SourcesResponse } from "../lib/api";
import { useDataset } from "../lib/dataset";
import { num, pct } from "../lib/format";
import { PageHeader } from "./History";

const META: Record<string, { name: string; system: string; icon: ReactNode }> = {
  oms: { name: "Order management", system: "Postgres database", icon: <Database className="h-4 w-4" /> },
  sellers: { name: "Seller platform", system: "CSV export", icon: <Package className="h-4 w-4" /> },
  payments: { name: "Payment gateway", system: "JSON", icon: <Boxes className="h-4 w-4" /> },
  feedback: { name: "Customer reviews", system: "JSON (real text)", icon: <Mail className="h-4 w-4" /> },
  erp: { name: "ERP", system: "Postgres database", icon: <Database className="h-4 w-4" /> },
  wms: { name: "Warehouse system", system: "CSV export", icon: <Boxes className="h-4 w-4" /> },
  carrier: { name: "Carrier feed", system: "JSON", icon: <Truck className="h-4 w-4" /> },
  procurement: { name: "Procurement", system: "CSV export", icon: <Package className="h-4 w-4" /> },
  signals: { name: "External signals", system: "JSON", icon: <Globe className="h-4 w-4" /> },
  docs: { name: "Contracts, policies & email", system: "Documents", icon: <Mail className="h-4 w-4" /> },
};

export function Sources() {
  const [d, setD] = useState<SourcesResponse | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const { key } = useDataset();
  useEffect(() => {
    if (!key) return;
    setD(null);
    api.sources(key).then(setD).catch(() => {});
  }, [key]);
  if (!d)
    return (
      <div className="flex justify-center p-16">
        <Spinner />
      </div>
    );
  const bySource = d.tables.reduce<Record<string, SourcesResponse["tables"]>>((acc, t) => {
    (acc[t.source] ??= []).push(t);
    return acc;
  }, {});
  const zetaris = d.harness === "zetaris";
  return (
    <div className="mx-auto max-w-6xl px-6 py-8">
      <PageHeader
        title="Data sources"
        subtitle="Separate systems, queried in place as one. Agents write one SQL query that joins the live database with file exports, feeds and documents; no data is copied."
        right={
          <Badge tone={zetaris ? "brand" : "amber"} className="px-2.5 py-1 text-[12px]">
            <GitMerge className="h-3.5 w-3.5" />
            {zetaris ? "Federated by Zetaris" : "Local fallback harness"}
          </Badge>
        }
      />
      {d.notes.length > 0 && !zetaris && (
        <div className="mt-4 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50/60 px-4 py-2.5 text-[12.5px] text-amber-900">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          {d.notes.join(" ")}
        </div>
      )}
      <div className="mt-6 grid gap-4 md:grid-cols-2">
        {Object.entries(bySource).map(([src, tables]) => {
          const m = META[src] ?? { name: src, system: "", icon: <Database className="h-4 w-4" /> };
          return (
            <Card key={src}>
              <div className="flex items-start gap-3 border-b border-slate-100 px-5 py-4">
                <div className="rounded-lg bg-brand-50 p-2 text-brand-600">{m.icon}</div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-[14px] font-semibold text-slate-900">{m.name}</span>
                    <Badge>{m.system}</Badge>
                  </div>
                  <div className="mt-0.5 text-[12px] leading-snug text-slate-500">{d.source_notes[src]}</div>
                </div>
              </div>
              <ul className="divide-y divide-slate-100">
                {tables.map((t) => (
                  <li key={t.fqn}>
                    <button onClick={() => setOpen(open === t.fqn ? null : t.fqn)} className="flex w-full items-center gap-2 px-5 py-2.5 text-left hover:bg-slate-50">
                      <ChevronRight className={`h-3.5 w-3.5 text-slate-400 transition ${open === t.fqn ? "rotate-90" : ""}`} />
                      <span className="font-mono text-[12px] text-slate-800">{t.fqn}</span>
                      <span className="ml-auto text-[11px] tabular-nums text-slate-500">
                        {t.row_count !== null ? `${num(t.row_count)} rows` : ""} · {t.columns.length} cols
                      </span>
                    </button>
                    {open === t.fqn && (
                      <div className="flex flex-wrap gap-1 px-5 pb-3 pl-10">
                        {t.columns.map((c) => (
                          <span key={c.name} className="rounded-md bg-slate-100 px-1.5 py-0.5 font-mono text-[10.5px] text-slate-600">
                            {c.name} <span className="text-slate-400">{c.type.toLowerCase()}</span>
                          </span>
                        ))}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </Card>
          );
        })}
      </div>
      <Card className="mt-6">
        <CardHeader
          title="Verified joins"
          subtitle="Proposed by the Data Scout, then tested against the data. Joins below 50% match are dropped."
          icon={<GitMerge className="h-4 w-4" />}
        />
        {d.data_map?.joins?.length ? (
          <ul className="divide-y divide-slate-100">
            {d.data_map.joins.map((j, i) => (
              <li key={i} className="grid items-center gap-3 px-5 py-2.5 md:grid-cols-[1fr_1fr_160px]">
                <span className="font-mono text-[11.5px] text-slate-700">
                  {j.left} <span className="text-slate-400">=</span> {j.right}
                </span>
                <span className="text-[12px] text-slate-500">{j.meaning}</span>
                <span className="flex items-center gap-2 text-[11px] text-slate-500">
                  <Bar value={j.match_rate ?? 0} tone="green" />
                  {pct(j.match_rate)}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <div className="px-5 py-6 text-[13px] text-slate-500">The data map appears after the first investigation.</div>
        )}
        {(d.data_map?.caveats ?? []).length > 0 && (
          <div className="border-t border-slate-100 px-5 py-3">
            {(d.data_map?.caveats ?? []).map((c, i) => (
              <div key={i} className="flex gap-1.5 py-0.5 text-[12px] text-slate-600">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
                {c}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

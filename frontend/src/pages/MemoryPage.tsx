import clsx from "clsx";
import { Brain, Database, FileText } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Bar, Card, EmptyState, Spinner, Stat } from "../components/ui";
import { api, type MemoryItem } from "../lib/api";
import { useDataset } from "../lib/dataset";
import { when } from "../lib/format";
import { PageHeader } from "./History";

const KINDS = ["all", "finding", "fact", "lesson", "hypothesis"] as const;
const KIND_TONE: Record<string, "green" | "slate" | "violet" | "amber"> = { finding: "green", fact: "slate", lesson: "violet", hypothesis: "amber" };

export function MemoryPage() {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.memory>> | null>(null);
  const [kind, setKind] = useState<(typeof KINDS)[number]>("all");
  const { key } = useDataset();
  useEffect(() => {
    if (!key) return;
    api.memory(key).then(setData).catch(() => setData(null));
  }, [key]);
  const items = useMemo(
    () => (data?.items ?? []).filter((m) => !(m.tags ?? []).includes("data_map")).filter((m) => kind === "all" || m.kind === kind),
    [data, kind],
  );
  const tiers = data?.tiers ?? {};
  return (
    <div className="mx-auto max-w-6xl px-6 py-8">
      <PageHeader
        title="Memory"
        subtitle="What WhyChain has learned across investigations: confirmed causes, ruled-out explanations, lessons and manager decisions. Stored in Meterless H-MEM with provenance, and recalled by later investigations."
      />
      {!data ? (
        <div className="flex justify-center p-10">
          <Spinner />
        </div>
      ) : (
        <>
          <Card className="mt-6">
            <div className="grid grid-cols-2 gap-5 px-5 py-4 sm:grid-cols-4">
              <Stat label="Memories" value={data.count} hint={data.status.online ? "Meterless H-MEM" : "local file (bridge offline)"} />
              <Stat label="Long-term" value={tiers.long_term ?? "—"} hint="promoted by consolidation" />
              <Stat label="Working" value={tiers.working ?? "—"} hint="recent findings" />
              <Stat label="Short-term" value={tiers.short_term ?? "—"} hint="unconfirmed hypotheses" />
            </div>
          </Card>
          <div className="mt-5 flex gap-1.5">
            {KINDS.map((k) => (
              <button
                key={k}
                onClick={() => setKind(k)}
                className={clsx(
                  "rounded-full px-3 py-1 text-[12px] font-medium capitalize ring-1 ring-inset",
                  kind === k ? "bg-slate-900 text-white ring-slate-900" : "bg-white text-slate-600 ring-slate-200 hover:bg-slate-50",
                )}
              >
                {k === "all" ? "All" : `${k}s`}
              </button>
            ))}
          </div>
          <div className="mt-4 space-y-3">
            {items.length === 0 && (
              <Card>
                <EmptyState icon={<Brain className="h-5 w-5" />} title="Nothing here yet">
                  Memories appear after the first investigation finishes.
                </EmptyState>
              </Card>
            )}
            {items.map((m, i) => (
              <MemoryCard key={m.id ?? i} m={m} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function MemoryCard({ m }: { m: MemoryItem }) {
  return (
    <Card className="px-5 py-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={KIND_TONE[m.kind ?? ""] ?? "slate"}>{m.kind ?? "memory"}</Badge>
        {m.tier && <Badge>{m.tier.replace("_", "-")}</Badge>}
        {(m.tags ?? [])
          .filter((t) => !["investigation", "confirmed", "rejected", "lesson"].includes(t))
          .slice(0, 4)
          .map((t) => (
            <Badge key={t} tone="brand">
              {t}
            </Badge>
          ))}
        <span className="ml-auto text-[11px] text-slate-400">
          {m.agent} · {when(m.created_at)}
        </span>
      </div>
      <p className="mt-2 text-[13px] leading-relaxed text-slate-800">{m.content}</p>
      <div className="mt-3 flex flex-wrap items-center gap-4 text-[11.5px] text-slate-500">
        {typeof m.confidence === "number" && (
          <div className="flex w-44 items-center gap-2">
            <span>Confidence</span>
            <Bar value={m.confidence} tone={m.confidence >= 0.7 ? "green" : "amber"} />
          </div>
        )}
        {m.provenance?.query && (
          <span className="flex min-w-0 items-center gap-1">
            <Database className="h-3 w-3 shrink-0" />
            <span className="truncate font-mono">{m.provenance.query.slice(0, 80)}</span>
          </span>
        )}
        {m.provenance?.doc && (
          <span className="flex items-center gap-1">
            <FileText className="h-3 w-3" />
            {m.provenance.doc}
          </span>
        )}
        {m.run_id && /^\d{8}-/.test(m.run_id) && (
          <Link to={`/runs/${m.run_id}`} className="font-medium text-brand-700 hover:underline">
            From investigation {m.run_id}
          </Link>
        )}
      </div>
    </Card>
  );
}

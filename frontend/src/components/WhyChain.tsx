import { ArrowLeft, GitBranch } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type RunResult } from "../lib/api";

type Rel = { from_name?: string; type?: string; to_name?: string; attrs?: Record<string, unknown> };
type Chain = {
  symptom: string;
  causes: { name: string; share: number | null }[];
  explains: Record<string, string[]>;
  where: string[];
  ruled: string[];
};

/** The chain as the Meterless World Model holds it for this run (relations written by the Reporter). */
function fromWorld(rels: Rel[]): Chain | null {
  // the World Model keeps one relation per cause and symptom, with its own attributes (confidence = the share)
  const causeRels = rels.filter((r) => r.type === "causes");
  const ruledRels = rels.filter((r) => r.type === "ruled_out_for");
  if (!causeRels.length && !ruledRels.length) return null;
  const symptom = causeRels[0]?.to_name ?? ruledRels[0]?.to_name ?? "";
  const seen = new Set<string>();
  const causes = causeRels
    .filter((r) => r.from_name && !seen.has(r.from_name) && seen.add(r.from_name))
    .map((r) => {
      const v = r.attrs?.share_of_symptom ?? r.attrs?.confidence;
      return { name: r.from_name!, share: typeof v === "number" ? v : null };
    })
    .sort((a, b) => (b.share ?? 0) - (a.share ?? 0));
  const explains: Record<string, string[]> = {};
  for (const r of rels.filter((r) => r.type === "explains" && r.from_name && r.to_name)) {
    (explains[r.to_name!] ??= []).includes(r.from_name!) || explains[r.to_name!].push(r.from_name!);
  }
  const uniq = (xs: (string | undefined)[]) => [...new Set(xs.filter(Boolean) as string[])];
  return {
    symptom,
    causes,
    explains,
    where: uniq(rels.filter((r) => r.type === "concentrated_in").map((r) => r.to_name)),
    ruled: uniq(ruledRels.map((r) => r.from_name)),
  };
}

/** The same chain from this run's own verdicts, when the World Model that holds the run isn't connected. */
function fromResult(result: RunResult): Chain {
  const v = result.verdicts ?? {};
  const fs = result.findings ?? [];
  return {
    symptom: result.question,
    causes: fs
      .filter((f) => v[f.task_id]?.is_cause && v[f.task_id]?.status === "confirmed")
      .map((f) => ({ name: f.statement, share: typeof v[f.task_id]?.share_of_symptom === "number" ? (v[f.task_id].share_of_symptom as number) : null }))
      .sort((a, b) => (b.share ?? 0) - (a.share ?? 0)),
    explains: {},
    where: [],
    ruled: fs.filter((f) => f.agent_kind === "tool" && v[f.task_id]?.status === "rejected").map((f) => f.statement),
  };
}

const short = (s: string) => s.replace(/\s*\(\d{4}-\d\d-\d\d to \d{4}-\d\d-\d\d[^)]*\)/, "").replace(/ delayed deliveries$/, "");

function Node({ title, sub, tone }: { title: string; sub?: string; tone: "rose" | "brand" | "amber" }) {
  const cls = {
    rose: "border-rose-200 bg-rose-50 text-rose-900",
    brand: "border-brand-200 bg-brand-50 text-brand-900",
    amber: "border-amber-200 bg-amber-50 text-amber-900",
  }[tone];
  return (
    <div className={`min-w-0 rounded-lg border px-3 py-2 ${cls}`}>
      <div className="text-[13px] font-semibold leading-snug">{title}</div>
      {sub && <div className="mt-0.5 text-[11.5px] opacity-75">{sub}</div>}
    </div>
  );
}

const Arrow = () => <ArrowLeft className="h-4 w-4 shrink-0 self-center text-slate-400" aria-label="because" />;

/** "Why", drawn as a chain: the symptom, the causes that explain it (with their share), what explains a cause, where
 * it sits, and what was ruled out. Read right to left: each box is why the one to its left happened. */
export function WhyChain({ result }: { result: RunResult }) {
  const [rels, setRels] = useState<Rel[] | null>(null);
  useEffect(() => {
    let live = true;
    api
      .world(result.run_id)
      .then((w) => live && setRels((w.relations ?? []) as Rel[]))
      .catch(() => live && setRels([]));
    return () => {
      live = false;
    };
  }, [result.run_id]);
  if (rels === null) return null;
  const world = fromWorld(rels);
  const chain = world ?? fromResult(result);
  if (!chain.causes.length && !chain.ruled.length) return null;
  // what a share is a share of: short units (stock questions), a gap between groups, or a rise over time
  const a = (result as unknown as { analysis?: { kind?: string; primary?: { mode?: string } } }).analysis;
  const of = a?.kind === "supply" ? "of the short units" : a?.primary?.mode === "segment" ? "of the gap" : "of the rise";
  return (
    <div className="mx-6 mb-5 rounded-xl border border-slate-200 bg-white px-5 py-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
          <GitBranch className="h-3.5 w-3.5" />
          Why: the chain
        </div>
        <div className="text-[11px] text-slate-400">
          {world ? "From the Meterless World Model" : "From this run's verdicts (the World Model holding this run isn't connected)"}
        </div>
      </div>
      <div className="mt-3 flex flex-col gap-2 lg:flex-row lg:items-start">
        <div className="lg:w-56 lg:shrink-0">
          <Node tone="rose" title={chain.symptom} sub="what happened" />
        </div>
        {chain.causes.length > 0 && <Arrow />}
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          {chain.causes.map((c) => (
            <div key={c.name} className="flex flex-col gap-2 sm:flex-row sm:items-start">
              <div className="sm:w-72 sm:shrink-0">
                <Node
                  tone="brand"
                  title={c.name}
                  sub={c.share !== null ? `${Math.round(c.share * 1000) / 10}% ${of}` : "a confirmed cause"}
                />
              </div>
              {(chain.explains[c.name] ?? []).map((x) => (
                <div key={x} className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start">
                  <Arrow />
                  <Node tone="amber" title={x} sub="explains it" />
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
      {chain.where.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5 text-[12px] text-slate-600">
          <span className="font-medium text-slate-500">Where it sits:</span>
          {chain.where.map((w) => (
            <span key={w} className="rounded-md bg-slate-100 px-2 py-0.5 text-slate-700">
              {w}
            </span>
          ))}
        </div>
      )}
      {chain.ruled.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[12px]">
          <span className="font-medium text-slate-500">Ruled out:</span>
          {chain.ruled.map((r) => (
            <span key={r} className="rounded-md bg-slate-50 px-2 py-0.5 text-slate-400 line-through decoration-rose-400/70">
              {short(r)}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

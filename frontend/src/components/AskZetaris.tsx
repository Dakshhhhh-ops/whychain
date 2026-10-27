import { Code2, Database, Search, Sparkles } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api, type AskResult } from "../lib/api";
import { Badge, Button, Card, CardHeader, Spinner } from "./ui";

const EXAMPLES: Record<string, string[]> = {
  olist: [
    "Which 5 seller states had the most late deliveries in March 2018?",
    "Average carrier transit days by customer state for orders placed in March 2018",
    "How many orders got a 1-star review each month in 2018?",
  ],
  kaveri: [
    "Which suppliers have the most overdue purchase orders?",
    "Backordered units by warehouse and product category",
    "Open orders due next week at the Pune DC, by customer",
  ],
};

/** Ask Zetaris: a plain-English question, turned into one read-only Spark SQL query by a coding model (SpaceXAI's
 *  Grok when configured), run live through Zetaris; failed SQL goes back to the model with Zetaris's error. */
export function AskZetaris({ dataset }: { dataset: string }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<AskResult | null>(null);
  const [err, setErr] = useState("");
  const examples = EXAMPLES[dataset] ?? EXAMPLES.olist;

  const run = async (question: string) => {
    if (question.trim().length < 5) return;
    setQ(question);
    setBusy(true);
    setErr("");
    setRes(null);
    try {
      setRes(await api.ask(question.trim(), dataset));
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    run(q);
  };
  const byGrok = res?.provider?.startsWith("xai");

  return (
    <Card className="mt-6">
      <CardHeader
        icon={<Sparkles className="h-4 w-4" />}
        title="Ask Zetaris"
        subtitle="Ask the data in plain English. A coding agent writes one read-only Spark SQL query; Zetaris runs it live across every system. If Zetaris rejects the SQL, the agent reads the error and fixes it."
      />
      <div className="px-5 pb-5">
        <form onSubmit={submit} className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white p-1.5 pl-3 focus-within:border-brand-300">
          <Search className="h-4 w-4 shrink-0 text-slate-400" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={examples[0]}
            className="h-9 min-w-0 flex-1 bg-transparent text-[13.5px] text-slate-900 outline-none placeholder:text-slate-400"
          />
          <Button type="submit" disabled={busy || q.trim().length < 5}>
            {busy ? <Spinner /> : <Database className="h-4 w-4" />}
            Ask
          </Button>
        </form>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {examples.map((x) => (
            <button
              key={x}
              type="button"
              onClick={() => run(x)}
              className="rounded-full border border-slate-200 bg-white px-2.5 py-0.5 text-[11.5px] text-slate-600 hover:border-brand-300 hover:text-brand-700"
            >
              {x}
            </button>
          ))}
        </div>

        {err && <div className="mt-3 text-[12.5px] text-red-700">{err}</div>}

        {res && (
          <div className="mt-4 space-y-3">
            <div className="flex flex-wrap items-center gap-1.5 text-[12px]">
              <Badge tone={byGrok ? "violet" : "green"}>
                <Code2 className="h-3 w-3" />
                SQL by {byGrok ? "Grok (SpaceXAI)" : "Nemotron (NVIDIA)"}
                {res.model ? ` · ${res.model}` : ""}
              </Badge>
              <Badge tone={res.harness === "zetaris" ? "brand" : "amber"}>
                <Database className="h-3 w-3" />
                {res.harness === "zetaris" ? "Run live on Zetaris" : `Run on ${res.harness}`}
              </Badge>
              <span className="text-slate-500">
                {res.attempts.length} attempt{res.attempts.length === 1 ? "" : "s"} · {res.seconds} s
              </span>
            </div>
            {res.ok ? (
              <>
                {res.explanation && <div className="text-[13px] text-slate-700">{res.explanation}</div>}
                <div className="overflow-x-auto rounded-lg border border-slate-200">
                  <table className="w-full text-left text-[12.5px]">
                    <thead className="bg-slate-50 text-slate-500">
                      <tr>
                        {res.columns!.map((c) => (
                          <th key={c} className="px-3 py-1.5 font-medium">{c}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {res.rows!.slice(0, 50).map((r, i) => (
                        <tr key={i} className="border-t border-slate-100">
                          {r.map((v, j) => (
                            <td key={j} className="px-3 py-1.5 tabular-nums text-slate-800">{v === null ? "—" : String(v)}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : (
              <div className="text-[12.5px] text-red-700">No answer after {res.attempts.length} attempts: {res.error}</div>
            )}
            {res.attempts.map((a) => (
              <details key={a.n} open={res.attempts.length > 1 || !res.ok} className="rounded-lg border border-slate-200 bg-slate-50/60">
                <summary className="cursor-pointer px-3 py-1.5 text-[12px] text-slate-600">
                  Attempt {a.n}: {a.error ? <span className="text-red-700">rejected by {a.harness ?? "the data layer"}</span> : "ran"}
                  {a.model ? ` · written by ${a.model}` : ""}
                </summary>
                {a.sql && <pre className="overflow-x-auto px-3 pb-2 font-mono text-[11.5px] text-slate-700">{a.sql}</pre>}
                {a.error && <div className="px-3 pb-2 font-mono text-[11px] text-red-700">{a.error}</div>}
              </details>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}

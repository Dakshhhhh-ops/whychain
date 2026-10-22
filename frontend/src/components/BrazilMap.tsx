import clsx from "clsx";
import { useState } from "react";
import type { RegionRow } from "../lib/api";

/** Brazil's 27 federative units as a tile grid, placed roughly where they sit on the map. [column, row] */
const TILES: Record<string, [number, number]> = {
  RR: [2, 0], AP: [4, 0],
  AM: [1, 1], PA: [3, 1], MA: [5, 1], CE: [6, 1], RN: [7, 1],
  AC: [0, 2], RO: [1, 2], MT: [2, 2], TO: [4, 2], PI: [5, 2], PE: [6, 2], PB: [7, 2],
  MS: [2, 3], GO: [3, 3], DF: [4, 3], BA: [5, 3], SE: [6, 3], AL: [7, 3],
  SP: [3, 4], MG: [4, 4], ES: [5, 4],
  PR: [3, 5], RJ: [4, 5],
  SC: [3, 6],
  RS: [3, 7],
};

const REGION_OF: Record<string, string> = {
  AC: "North", AM: "North", AP: "North", PA: "North", RO: "North", RR: "North", TO: "North",
  AL: "Northeast", BA: "Northeast", CE: "Northeast", MA: "Northeast", PB: "Northeast", PE: "Northeast", PI: "Northeast", RN: "Northeast", SE: "Northeast",
  DF: "Centre-West", GO: "Centre-West", MS: "Centre-West", MT: "Centre-West",
  ES: "Southeast", MG: "Southeast", RJ: "Southeast", SP: "Southeast",
  PR: "South", RS: "South", SC: "South",
};

// sequential steps, light to dark; the last two take white text
const STEPS = ["bg-rose-50", "bg-rose-100", "bg-rose-200", "bg-rose-300", "bg-rose-400", "bg-rose-500", "bg-rose-600"];
const step = (v: number, cuts: number[]) => cuts.filter((c) => v >= c).length;

export type MapMode = "change" | "level";

export function BrazilMap({ rows, mode, highlight }: { rows: RegionRow[]; mode: MapMode; highlight?: Set<string> }) {
  const [hover, setHover] = useState<string | null>(null);
  const by = new Map(rows.map((r) => [r.state, r]));
  const value = (r?: RegionRow) => {
    if (!r || r.late_pct === null) return null;
    if (mode === "level") return r.late_pct;
    return r.base_late_pct === null ? null : r.late_pct - r.base_late_pct;
  };
  const cuts = mode === "change" ? [0.5, 3, 6, 10, 15, 25] : [4, 7, 10, 14, 18, 25];
  const h = hover ? by.get(hover) : null;
  return (
    <div className="flex flex-col gap-5 lg:flex-row lg:items-start">
      <div className="grid shrink-0 gap-1.5" style={{ gridTemplateColumns: "repeat(8, 52px)", gridTemplateRows: "repeat(8, 46px)" }}>
        {Object.entries(TILES).map(([uf, [c, r]]) => {
          const row = by.get(uf);
          const v = value(row);
          const s = v === null ? -1 : step(v, cuts);
          const on = highlight?.has(uf);
          const thin = row && mode === "change" && row.orders < 30;
          return (
            <button
              key={uf}
              type="button"
              onMouseEnter={() => setHover(uf)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(uf)}
              style={{ gridColumnStart: c + 1, gridRowStart: r + 1 }}
              className={clsx(
                "flex flex-col items-center justify-center rounded-md text-center leading-tight transition",
                s < 0 ? "bg-slate-100 text-slate-400" : STEPS[s],
                s >= 5 ? "text-white" : "text-slate-800",
                on && "ring-2 ring-brand-600 ring-offset-1",
                hover === uf && "scale-105 shadow-md",
                thin && "opacity-60",
              )}
            >
              <span className="text-[11px] font-semibold">{uf}</span>
              <span className="text-[10px] tabular-nums">
                {v === null ? "—" : mode === "change" ? `${v > 0 ? "+" : ""}${v.toFixed(0)}` : `${v.toFixed(0)}%`}
              </span>
            </button>
          );
        })}
      </div>
      <div className="min-w-0 flex-1 space-y-3">
        <div className="min-h-[96px] rounded-lg border border-slate-200 bg-slate-50/60 px-3.5 py-2.5 text-[12.5px]">
          {h ? (
            <>
              <div className="font-semibold text-slate-900">
                {h.name} <span className="font-normal text-slate-500">· {REGION_OF[h.state] ?? ""}</span>
              </div>
              <div className="mt-1 grid grid-cols-2 gap-x-4 gap-y-0.5 text-slate-700">
                <span>Late deliveries</span>
                <span className="tabular-nums">
                  {h.late_pct ?? "—"}%{h.base_late_pct !== null && <span className="text-slate-500"> (usually {h.base_late_pct}%)</span>}
                </span>
                {h.transit_days !== null && (
                  <>
                    <span>Carrier transit</span>
                    <span className="tabular-nums">
                      {h.transit_days} days{h.base_transit_days !== null && <span className="text-slate-500"> (usually {h.base_transit_days})</span>}
                    </span>
                  </>
                )}
                <span>Orders</span>
                <span className="tabular-nums">{h.orders.toLocaleString()}</span>
              </div>
            </>
          ) : (
            <span className="text-slate-500">Hover a state for its numbers. Outlined states are named in the confirmed causes.</span>
          )}
        </div>
        <MapScale mode={mode} cuts={cuts} />
      </div>
    </div>
  );
}

function MapScale({ mode, cuts }: { mode: MapMode; cuts: number[] }) {
  return (
    <div className="text-[11px] text-slate-500">
      <div className="mb-1">{mode === "change" ? "Change in late deliveries (percentage points)" : "Late deliveries (% of orders)"}</div>
      <div className="flex items-center gap-0.5">
        {STEPS.map((c, i) => (
          <div key={c} className="flex flex-col items-center">
            <span className={clsx("h-2.5 w-8 first:rounded-l last:rounded-r", c)} />
            <span className="mt-0.5 tabular-nums">{i === 0 ? (mode === "change" ? "≤0" : "0") : cuts[i - 1]}</span>
          </div>
        ))}
      </div>
      {mode === "change" && <div className="mt-1.5">Faded tiles: fewer than 30 orders that month.</div>}
    </div>
  );
}

const STATE_NAMES: Record<string, string> = {
  AC: "Acre", AL: "Alagoas", AP: "Amapá", AM: "Amazonas", BA: "Bahia", CE: "Ceará", DF: "Distrito Federal",
  ES: "Espírito Santo", GO: "Goiás", MA: "Maranhão", MT: "Mato Grosso", MS: "Mato Grosso do Sul", MG: "Minas Gerais",
  PA: "Pará", PB: "Paraíba", PR: "Paraná", PE: "Pernambuco", PI: "Piauí", RJ: "Rio de Janeiro", RN: "Rio Grande do Norte",
  RS: "Rio Grande do Sul", RO: "Rondônia", RR: "Roraima", SC: "Santa Catarina", SP: "São Paulo", SE: "Sergipe", TO: "Tocantins",
};

/** States named in a piece of text, by two-letter code or full name ("Northeast" names its nine states). */
export function statesIn(text: string): Set<string> {
  const out = new Set<string>();
  for (const uf of Object.keys(TILES)) if (new RegExp(`\\b${uf}\\b`).test(text)) out.add(uf);
  const lower = text.toLowerCase();
  for (const [uf, name] of Object.entries(STATE_NAMES)) if (lower.includes(name.toLowerCase())) out.add(uf);
  if (/north-?east|nordeste/i.test(text)) Object.entries(REGION_OF).forEach(([uf, r]) => r === "Northeast" && out.add(uf));
  return out;
}

const MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"];

/** The purchase month a question is about, e.g. "orders placed in March 2018" -> "2018-03". */
export function monthIn(question: string): string | null {
  const q = question.toLowerCase();
  const m = q.match(new RegExp(`\\b(${MONTH_NAMES.join("|")}|${MONTH_NAMES.map((n) => n.slice(0, 3)).join("|")})[a-z]*\\.?\\s+(20\\d\\d)\\b`));
  if (m) {
    const i = MONTH_NAMES.findIndex((n) => n.startsWith(m[1].slice(0, 3)));
    return `${m[2]}-${String(i + 1).padStart(2, "0")}`;
  }
  const bf = q.match(/black friday\s+(20\d\d)/);
  if (bf) return `${bf[1]}-11`;
  const ym = q.match(/\b(20\d\d)-(0[1-9]|1[0-2])\b/);
  return ym ? `${ym[1]}-${ym[2]}` : null;
}

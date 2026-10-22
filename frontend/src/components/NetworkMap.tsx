import clsx from "clsx";
import { useMemo } from "react";
import type { Network, NetworkNode } from "../lib/api";

const COLS: { type: NetworkNode["type"]; title: string; x: number; w: number }[] = [
  { type: "supplier", title: "Suppliers", x: 4, w: 150 },
  { type: "port", title: "Entry point", x: 236, w: 136 },
  { type: "dc", title: "Distribution centres", x: 452, w: 176 },
  { type: "city", title: "Customer cities", x: 712, w: 124 },
];
const WIDTH = 840;
const ROW = 40;
const TOP = 40;

/** Highlight ids for the entities named in some text (supplier codes/brands, DCs, lanes, carriers, ports). */
export function highlightFromText(text: string, net: Network): Set<string> {
  const t = text.toLowerCase();
  const ids = new Set<string>();
  for (const m of text.matchAll(/\b(S-\d{2})\b/g)) ids.add(m[1]);
  for (const m of text.matchAll(/\b(MUM|PUN|CHN)\b/g)) ids.add(m[1]);
  for (const m of text.matchAll(/\b((?:MUM|PUN|CHN)-[A-Z]{3})\b/g)) ids.add(m[1]);
  const carriers = new Set([...text.matchAll(/\b(C0\d)\b/g)].map((m) => m[1]));
  for (const n of net.nodes) {
    if (n.type === "supplier" && t.includes(n.label.toLowerCase())) ids.add(n.id);
    // "Pune orders" / "Pune DC" point at the DC; "Chennai port" doesn't
    if (n.type === "dc" && new RegExp(`\\b${n.label}\\b(?!\\s+port)`, "i").test(text)) ids.add(n.id);
    if (n.type === "port" && n.sub === "sea port" && t.includes(n.label.toLowerCase()) && t.includes("port")) ids.add(n.id);
  }
  for (const e of net.edges) {
    if (e.kind === "lane" && ((e.id && ids.has(e.id)) || (e.carrier && carriers.has(e.carrier)))) {
      if (e.id) ids.add(e.id);
      ids.add(e.from);
      ids.add(e.to);
    }
  }
  // complete inbound paths: a highlighted supplier lights up its entry port
  for (const e of net.edges) if (e.kind === "inbound" && ids.has(e.from) && e.to.startsWith("port:") && net.nodes.find((n) => n.id === e.from)?.type === "supplier") ids.add(e.to);
  return ids;
}

export function NetworkMap({ net, highlight }: { net: Network; highlight?: Set<string> }) {
  const layout = useMemo(() => {
    const portOrder = net.nodes.filter((n) => n.type === "port").map((n) => n.id);
    const dcOrder = net.nodes.filter((n) => n.type === "dc").map((n) => n.id);
    const portOf = (id: string) => net.edges.find((e) => e.from === id && e.kind === "inbound")?.to ?? "";
    const dcOf = (id: string) => net.edges.find((e) => e.to === id && e.kind === "lane")?.from ?? "";
    const cols = COLS.map((c) => {
      let nodes = net.nodes.filter((n) => n.type === c.type);
      if (c.type === "supplier") nodes = [...nodes].sort((a, b) => portOrder.indexOf(portOf(a.id)) - portOrder.indexOf(portOf(b.id)));
      if (c.type === "city") nodes = [...nodes].sort((a, b) => dcOrder.indexOf(dcOf(a.id)) - dcOrder.indexOf(dcOf(b.id)));
      return { ...c, nodes };
    });
    const rows = Math.max(...cols.map((c) => c.nodes.length), 1);
    const height = TOP + rows * ROW + 16;
    const pos: Record<string, { x: number; y: number; w: number; h: number }> = {};
    for (const c of cols) {
      const span = rows * ROW;
      const step = span / Math.max(c.nodes.length, 1);
      c.nodes.forEach((n, i) => {
        const h = c.type === "dc" ? 52 : c.type === "port" ? 40 : 30;
        pos[n.id] = { x: c.x, y: TOP + step * (i + 0.5) - h / 2, w: c.w, h };
      });
    }
    return { cols, pos, height };
  }, [net]);

  const hl = highlight && highlight.size ? highlight : null;
  const on = (id: string) => !hl || hl.has(id);
  const edgeOn = (from: string, to: string, id?: string) => !!hl && ((id && hl.has(id)) || (hl.has(from) && hl.has(to)));

  return (
    <svg viewBox={`0 0 ${WIDTH} ${layout.height}`} className="h-auto w-full select-none" role="img" aria-label="Supply network map">
      {layout.cols.map((c) => (
        <text key={c.type} x={c.x} y={18} className="fill-slate-400 text-[11px] font-semibold uppercase tracking-wider">
          {c.title}
        </text>
      ))}
      {net.edges.map((e, i) => {
        const a = layout.pos[e.from];
        const b = layout.pos[e.to];
        if (!a || !b) return null;
        const x1 = a.x + a.w;
        const y1 = a.y + a.h / 2;
        const x2 = b.x;
        const y2 = b.y + b.h / 2;
        const mx = (x1 + x2) / 2;
        const lit = edgeOn(e.from, e.to, e.id);
        const bad = e.status === "critical" || e.status === "warning";
        return (
          <g key={i}>
            <path
              d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`}
              fill="none"
              className={clsx(
                "transition-all",
                lit ? "stroke-brand-500" : bad ? (e.status === "critical" ? "stroke-rose-300" : "stroke-amber-300") : "stroke-slate-200",
                hl && !lit && "opacity-30",
              )}
              strokeWidth={lit ? 2.6 : bad ? 2 : 1.2}
              strokeDasharray={e.kind === "lane" && bad && !lit ? "5 4" : undefined}
            />
            {e.kind === "lane" && typeof e.ontime === "number" && bad && (
              <text x={x2 - 6} y={y2 - 5} textAnchor="end" className={clsx("text-[9.5px] font-semibold", e.status === "critical" ? "fill-rose-600" : "fill-amber-600")}>
                {Math.round(e.ontime * 100)}% on time
              </text>
            )}
          </g>
        );
      })}
      {layout.cols.flatMap((c) =>
        c.nodes.map((n) => {
          const p = layout.pos[n.id];
          const lit = !!hl && hl.has(n.id);
          const crit = n.status === "critical";
          const warn = n.status === "warning";
          return (
            <g key={n.id} className={clsx("transition-opacity", !on(n.id) && "opacity-35")}>
              <title>{`${n.label} · ${n.sub}${n.status ? ` · ${n.status}` : ""}`}</title>
              <rect
                x={p.x}
                y={p.y}
                width={p.w}
                height={p.h}
                rx={c.type === "dc" ? 10 : 8}
                className={clsx(
                  lit ? "fill-brand-50 stroke-brand-500" : crit ? "fill-rose-50 stroke-rose-300" : warn ? "fill-amber-50 stroke-amber-300" : "fill-white stroke-slate-200",
                )}
                strokeWidth={lit ? 2 : 1}
              />
              {(crit || warn) && <circle cx={p.x + p.w - 10} cy={p.y + 10} r={3.5} className={crit ? "fill-rose-500" : "fill-amber-500"} />}
              <text x={p.x + 10} y={p.y + (c.type === "dc" ? 21 : c.type === "port" ? 17 : p.h / 2 + 4)} className={clsx("text-[11.5px] font-semibold", lit ? "fill-brand-700" : "fill-slate-800")}>
                {n.label}
                {c.type === "supplier" && <tspan className="fill-slate-400 text-[10px] font-normal"> {n.sub}</tspan>}
              </text>
              {(c.type === "dc" || c.type === "port") && (
                <text x={p.x + 10} y={p.y + (c.type === "dc" ? 38 : 31)} className={clsx("text-[10px]", crit ? "fill-rose-600" : "fill-slate-500")}>
                  {n.sub}
                </text>
              )}
            </g>
          );
        }),
      )}
    </svg>
  );
}

export function MapLegend({ showPath }: { showPath?: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-4 text-[11px] text-slate-500">
      <span className="flex items-center gap-1.5">
        <span className="h-2.5 w-2.5 rounded-full bg-rose-500" /> Disrupted / critical
      </span>
      <span className="flex items-center gap-1.5">
        <span className="h-2.5 w-2.5 rounded-full bg-amber-500" /> Warning
      </span>
      {showPath && (
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-5 rounded bg-brand-500" /> Cause path
        </span>
      )}
    </div>
  );
}

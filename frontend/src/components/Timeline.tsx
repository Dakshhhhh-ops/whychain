import clsx from "clsx";
import { ExternalLink } from "lucide-react";
import type { ExternalEvent, TimelinePoint } from "../lib/api";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const label = (m: string) => {
  const [y, mm] = m.split("-");
  return `${MONTHS[Number(mm) - 1]} ${y.slice(2)}`;
};

export const EVENT_TONE: Record<string, { fill: string; dot: string; name: string }> = {
  strike: { fill: "fill-amber-500", dot: "bg-amber-500", name: "Strike" },
  peak_season: { fill: "fill-violet-500", dot: "bg-violet-500", name: "Peak season" },
  holiday: { fill: "fill-slate-400", dot: "bg-slate-400", name: "Holiday" },
  security: { fill: "fill-sky-600", dot: "bg-sky-600", name: "Security" },
  policy: { fill: "fill-teal-500", dot: "bg-teal-500", name: "Carrier policy" },
  weather: { fill: "fill-blue-400", dot: "bg-blue-400", name: "Weather" },
};
const tone = (t: string) => EVENT_TONE[t] ?? EVENT_TONE.holiday;

/** Monthly orders (bars), late-delivery share (line), highlighted months, and verified external events (markers). */
export function Timeline({
  points,
  highlight,
  events = [],
  activeEvent,
  onEvent,
}: {
  points: TimelinePoint[];
  highlight?: Set<string>;
  events?: ExternalEvent[];
  activeEvent?: string | null;
  onEvent?: (e: ExternalEvent | null) => void;
}) {
  if (!points.length) return null;
  const W = 840;
  const H = events.length ? 284 : 260;
  const pad = { l: 40, r: 44, t: events.length ? 42 : 18, b: 34 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;
  const maxOrders = Math.max(...points.map((p) => p.orders));
  const maxLate = Math.max(25, Math.ceil(Math.max(...points.map((p) => p.late_pct)) / 5) * 5);
  const bw = iw / points.length;
  const idx = new Map(points.map((p, i) => [p.month, i]));
  const x = (i: number) => pad.l + i * bw + bw / 2;
  const yLate = (v: number) => pad.t + ih - (v / maxLate) * ih;
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i)},${yLate(p.late_pct)}`).join(" ");
  const ticks = [0, maxLate / 2, maxLate];
  const byMonth = events.reduce<Record<string, ExternalEvent[]>>((acc, e) => {
    (acc[e.month] ??= []).push(e);
    return acc;
  }, {});
  const active = events.find((e) => e.id === activeEvent);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label="Monthly orders, late-delivery share and external events">
      {ticks.map((t) => (
        <g key={t}>
          <line x1={pad.l} x2={W - pad.r} y1={yLate(t)} y2={yLate(t)} className="stroke-slate-100" />
          <text x={W - pad.r + 6} y={yLate(t) + 3} className="fill-rose-500 text-[10px]">
            {t}%
          </text>
        </g>
      ))}
      <text x={pad.l - 6} y={pad.t - 6} textAnchor="end" className="fill-slate-400 text-[10px]">
        orders
      </text>
      {active && idx.has(active.month) && (
        <rect x={x(idx.get(active.month)!) - bw / 2} y={pad.t} width={bw} height={ih} className="fill-amber-50" />
      )}
      {points.map((p, i) => {
        const h = (p.orders / maxOrders) * ih;
        const on = highlight?.has(p.month) || active?.month === p.month;
        return (
          <g key={p.month}>
            <title>{`${label(p.month)}: ${p.orders.toLocaleString()} orders · ${p.late_pct}% late · seller late ${p.seller_late_pct}% · carrier ${p.carrier_days} days · review ${p.review_score}${p.strike_mentions ? ` · ${p.strike_mentions} strike mentions` : ""}`}</title>
            <rect
              x={x(i) - bw * 0.32}
              y={pad.t + ih - h}
              width={bw * 0.64}
              height={h}
              rx={2}
              className={clsx(on ? "fill-brand-300" : "fill-slate-200")}
            />
            {(i % 2 === 0 || points.length < 14) && (
              <text x={x(i)} y={H - 14} textAnchor="middle" className={clsx("text-[10px]", on ? "fill-brand-700 font-semibold" : "fill-slate-400")}>
                {label(p.month)}
              </text>
            )}
          </g>
        );
      })}
      {Object.entries(byMonth).map(([m, evs]) => {
        const i = idx.get(m);
        if (i === undefined) return null;
        return (
          <g key={m}>
            <line x1={x(i)} x2={x(i)} y1={pad.t - 4} y2={pad.t + ih} className="stroke-slate-300" strokeDasharray="2 3" />
            {evs.map((e, k) => {
              const cx = x(i) + (k - (evs.length - 1) / 2) * 11;
              const on = e.id === activeEvent;
              return (
                <g
                  key={e.id}
                  className="cursor-pointer"
                  onMouseEnter={() => onEvent?.(e)}
                  onClick={() => onEvent?.(e)}
                >
                  <title>{`${e.name} (${e.start}${e.end && e.end !== e.start ? ` to ${e.end}` : ""}, ${e.region})`}</title>
                  <rect
                    x={cx - (on ? 5.5 : 4.5)}
                    y={pad.t - 22 - (on ? 5.5 : 4.5)}
                    width={on ? 11 : 9}
                    height={on ? 11 : 9}
                    transform={`rotate(45 ${cx} ${pad.t - 22})`}
                    className={clsx(tone(e.type).fill, on && "stroke-slate-900")}
                    strokeWidth={1.2}
                  />
                </g>
              );
            })}
          </g>
        );
      })}
      <path d={path} fill="none" className="stroke-rose-500 pointer-events-none" strokeWidth={2.2} strokeLinejoin="round" />
      {points.map((p, i) => (
        <circle
          key={p.month}
          cx={x(i)}
          cy={yLate(p.late_pct)}
          r={highlight?.has(p.month) ? 5 : p.late_pct >= 10 ? 3.5 : 2.2}
          className={clsx("pointer-events-none", highlight?.has(p.month) ? "fill-brand-600 stroke-white" : "fill-rose-500")}
          strokeWidth={1.5}
        />
      ))}
      {points.map((p, i) =>
        p.late_pct >= 14 ? (
          <text key={`l${p.month}`} x={x(i)} y={yLate(p.late_pct) - 9} textAnchor="middle" className="pointer-events-none fill-rose-600 text-[10.5px] font-semibold">
            {p.late_pct}%
          </text>
        ) : null,
      )}
    </svg>
  );
}

export function TimelineLegend({ events = false }: { events?: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-4 text-[11px] text-slate-500">
      <span className="flex items-center gap-1.5">
        <span className="h-2.5 w-3 rounded-sm bg-slate-200" /> Orders delivered
      </span>
      <span className="flex items-center gap-1.5">
        <span className="h-0.5 w-5 rounded bg-rose-500" /> Late deliveries (% of orders)
      </span>
      {events && (
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 rotate-45 bg-amber-500" /> Verified external event
        </span>
      )}
    </div>
  );
}

const host = (u: string) => {
  try {
    return new URL(u).hostname.replace(/^www\./, "");
  } catch {
    return u;
  }
};

/** Details of the selected external event, with its public sources. */
export function EventDetail({ events, active }: { events: ExternalEvent[]; active: ExternalEvent | null }) {
  if (!events.length) return null;
  if (!active)
    return (
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11.5px] text-slate-500">
        <span>
          {events.length} verified external events, each with public sources. Hover a marker to read it.
        </span>
        {Object.entries(EVENT_TONE)
          .filter(([k]) => events.some((e) => e.type === k))
          .map(([k, t]) => (
            <span key={k} className="flex items-center gap-1.5">
              <span className={clsx("h-2 w-2 rotate-45", t.dot)} />
              {t.name}
            </span>
          ))}
      </div>
    );
  const t = tone(active.type);
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50/70 px-3.5 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className={clsx("h-2 w-2 rotate-45", t.dot)} />
        <span className="text-[13px] font-semibold text-slate-900">{active.name}</span>
        <span className="text-[11.5px] text-slate-500">
          {active.start}
          {active.end && active.end !== active.start ? ` to ${active.end}` : ""} · {active.region} · {t.name}
        </span>
      </div>
      <p className="mt-1 text-[12.5px] leading-relaxed text-slate-700">{active.summary}</p>
      {active.sources.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[11.5px]">
          {active.sources.map((u) => (
            <a key={u} href={u} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-brand-700 hover:underline">
              <ExternalLink className="h-3 w-3" />
              {host(u)}
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

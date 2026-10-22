"""The analysis engine: one federated query for order-level facts, then deterministic tests on them.

Every test returns a Result: a table (columns + rows, so every number can be cited by cell), a summary, a verdict
where the test has one, and a plain-language description of the method. Nothing here calls a language model.

Two kinds of comparison ("scopes"):
  time     orders placed in a period vs a NORMAL baseline: the months (or weeks) before, minus any that were
           themselves abnormal (rate above 1.5x their median)
  segment  orders in a segment (e.g. some states) vs the other orders (or a named comparison group), same period
"""
from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from whychain.analysis.model import STAGES, FactModel, fact_model
from whychain.analysis.stats import diff_ci, quantile_map, shapley, shift_share, wilson

MIN_SEGMENT = 30          # fewest reference orders to compare a segment like with like
CAUSE_SHARE = 0.10        # a mechanism must explain at least this share of the rise to count as a cause


def pct(x: float, nd: int = 1) -> float:
    return round(100 * float(x), nd) if x == x else float("nan")


def r1(x: float) -> float:
    return round(float(x), 1) if x == x else float("nan")


@dataclass
class Result:
    tool: str
    title: str
    columns: list[str]
    rows: list[list]
    summary: dict
    method: str
    verdict: str = "measured"           # supports | refutes | partial | measured
    sql: str = ""
    notes: list[str] = field(default_factory=list)

    def evidence(self, eid: str, task_id: str, agent: str) -> dict:
        return {"id": eid, "type": "analysis", "tool": self.tool, "task_id": task_id, "agent": agent,
                "title": self.title, "columns": self.columns, "rows": self.rows, "row_count": len(self.rows),
                "summary": self.summary, "method": self.method, "verdict": self.verdict, "sql": self.sql,
                "notes": self.notes, "truncated": False, "reproduced": True}


@dataclass
class Scope:
    mode: str                   # "time" | "segment"
    label: str                  # what the focus orders are
    ref_label: str              # what they are compared with
    focus: pd.DataFrame
    ref: pd.DataFrame
    period: tuple[date, date] | None = None
    filters: dict = field(default_factory=dict)
    baseline_units: list[str] = field(default_factory=list)
    excluded_units: list[str] = field(default_factory=list)
    unit: str = "month"

    @property
    def rise(self) -> float:
        return self.focus["late"].mean() - self.ref["late"].mean() if len(self.focus) and len(self.ref) else float("nan")


_CACHE: dict[tuple, "Engine"] = {}
_LOCK = threading.Lock()


def get_engine(harness) -> "Engine | None":
    ds = getattr(harness, "dataset", None)
    model = fact_model(getattr(ds, "key", ""))
    if model is None:
        return None
    key = (harness.name, model.dataset, id(harness))
    with _LOCK:
        if key not in _CACHE:
            _CACHE[key] = Engine(harness, model, getattr(harness, "as_of", None))
        return _CACHE[key]


class Engine:
    def __init__(self, harness, model: FactModel, as_of: str | None = None):
        self.h, self.m = harness, model
        self.as_of = pd.Timestamp(as_of) if as_of else None
        self._facts: pd.DataFrame | None = None
        self._raw_rows = 0
        self._events: pd.DataFrame | None = None
        self._reviews: pd.DataFrame | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ data
    def facts(self) -> pd.DataFrame:
        with self._lock:
            if self._facts is None:
                self._facts = self._load()
            return self._facts

    def _query_df(self, sql: str, limit: int = 500_000) -> pd.DataFrame:
        r = self.h.query(sql, limit=limit)
        if not r.ok:
            raise RuntimeError(f"fact query failed: {r.error}")
        return pd.DataFrame(r.rows, columns=r.columns)

    def _load(self) -> pd.DataFrame:
        df = self._query_df(self.m.sql)
        self._raw_rows = len(df)
        df = df.drop_duplicates("order_id", keep="last").reset_index(drop=True)
        for c in ("purchase_ts", "handover_ts", "delivered_ts", "promised_ts"):
            df[c] = pd.to_datetime(df[c], errors="coerce")
        df["purchase"] = df["purchase_ts"].dt.normalize()
        for c, src in (("handover", "handover_ts"), ("delivered", "delivered_ts"), ("promised", "promised_ts")):
            df[c] = df[src].dt.normalize()
        df["h"] = (df["handover"] - df["purchase"]).dt.days
        df["t"] = (df["delivered"] - df["handover"]).dt.days
        df["p"] = (df["promised"] - df["purchase"]).dt.days
        df["d"] = df["h"] + df["t"]
        status_ok = df["status"].astype(str).isin(self.m.delivered_status)
        dates_ok = df[["purchase", "handover", "delivered", "promised"]].notna().all(axis=1)
        df["delivered_ok"] = status_ok & dates_ok & (df["h"] >= 0) & (df["t"] >= 0)
        df["late"] = (df["delivered"] > df["promised"]).astype(float)
        df["late_ts"] = (df["delivered_ts"] > df["promised_ts"]).astype(float)   # timestamp definition (for checks)
        df["month"] = df["purchase"].dt.strftime("%Y-%m")
        df["week"] = (df["purchase"] - pd.to_timedelta(df["purchase"].dt.weekday, unit="D")).dt.strftime("%Y-%m-%d")
        df["handover_week"] = (df["handover"] - pd.to_timedelta(df["handover"].dt.weekday, unit="D"))
        if self.m.region_map:
            df["region"] = df[self.m.region_source].map(self.m.region_map).fillna("Other")
            if "seller_state" in df:
                df["seller_region"] = df["seller_state"].map(self.m.region_map).fillna("Unknown")
                df["interstate"] = np.where(df["seller_state"].isna(), "unknown",
                                            np.where(df["seller_state"] == df[self.m.region_source], "same state",
                                                     "other state"))
        return df

    def delivered(self) -> pd.DataFrame:
        f = self.facts()
        return f[f["delivered_ok"]]

    def events(self) -> pd.DataFrame:
        if self._events is None:
            try:
                self._events = self._query_df("SELECT event_id, name, type, start_date, end_date, region "
                                              "FROM signals.events", 1000)
            except RuntimeError:
                self._events = pd.DataFrame(columns=["event_id", "name", "type", "start_date", "end_date", "region"])
        return self._events

    def reviews(self) -> pd.DataFrame:
        if self._reviews is None:
            self._reviews = self._query_df(self.m.reviews_sql) if self.m.reviews_sql else pd.DataFrame()
        return self._reviews

    def provenance(self) -> str:
        return self.m.sql

    # ------------------------------------------------------------------ scopes
    def _apply(self, df: pd.DataFrame, filters: dict | None) -> pd.DataFrame:
        for col, vals in (filters or {}).items():
            if col not in df.columns:
                raise ValueError(f"unknown dimension {col!r}; known: {sorted(self.m.dims)}")
            vals = vals if isinstance(vals, (list, tuple, set)) else [vals]
            df = df[df[col].astype(str).isin([str(v) for v in vals])]
        return df

    def time_scope(self, start: date, end: date, filters: dict | None = None, label: str = "",
                   baseline: tuple[date, date] | None = None) -> Scope:
        """Orders placed in [start, end) vs a normal baseline before it (same filters), or vs an explicit period."""
        d = self._apply(self.delivered(), filters)
        if baseline is not None:
            a0, b0 = pd.Timestamp(baseline[0]), pd.Timestamp(baseline[1])
            focus = d[(d["purchase"] >= pd.Timestamp(start)) & (d["purchase"] < pd.Timestamp(end))]
            ref = d[(d["purchase"] >= a0) & (d["purchase"] < b0)]
            lab = f"the period you named: {a0.date()} to {(b0 - timedelta(days=1)).date()}"
            return Scope("time", label or f"orders placed {start} to {end}", lab, focus, ref, (start, end),
                         dict(filters or {}), sorted(ref["month"].unique()), [], "month")
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        focus = d[(d["purchase"] >= start) & (d["purchase"] < end)]
        weekly = (end - start).days <= 14
        unit, key = ("week", "week") if weekly else ("month", "month")
        if weekly:
            cands = [(start - timedelta(weeks=k)).strftime("%Y-%m-%d") for k in range(8, 0, -1)]
            cands = [(pd.Timestamp(c) - pd.Timedelta(days=pd.Timestamp(c).weekday())).strftime("%Y-%m-%d") for c in cands]
        else:
            first = start.replace(day=1)
            cands = [(first - pd.DateOffset(months=k)).strftime("%Y-%m") for k in range(6, 0, -1)]
        pool = d[d[key].isin(cands) & (d["purchase"] < start)]
        rates = pool.groupby(key)["late"].agg(["mean", "count"])
        rates = rates[rates["count"] >= 50]
        med = float(rates["mean"].median()) if len(rates) else 0.0
        keep = [u for u in cands if u in rates.index and rates.loc[u, "mean"] <= 1.5 * med + 1e-9]
        if not keep:
            keep = [u for u in cands if u in rates.index]
        excluded = [u for u in cands if u in rates.index and u not in keep]
        ref = pool[pool[key].isin(keep)]
        label = label or f"orders placed {start.date()} to {(end - timedelta(days=1)).date()}"
        ref_label = (f"a normal baseline: {unit}s {', '.join(keep)}"
                     + (f" (left out as abnormal: {', '.join(excluded)})" if excluded else ""))
        return Scope("time", label, ref_label, focus, ref, (start.date(), end.date()), dict(filters or {}),
                     keep, excluded, unit)

    def segment_scope(self, filters: dict, compare_with: dict | None = None, start: date | None = None,
                      end: date | None = None, label: str = "") -> Scope:
        """Orders matching `filters` vs the others (or those matching `compare_with`), in the same period."""
        d = self.delivered()
        if start is not None:
            d = d[(d["purchase"] >= pd.Timestamp(start)) & (d["purchase"] < pd.Timestamp(end))]
        focus = self._apply(d, filters)
        if compare_with:
            ref = self._apply(d, compare_with)
            ref_label = "orders with " + "; ".join(f"{k} in {v}" for k, v in compare_with.items())
        else:
            ref = d.drop(focus.index)
            ref_label = "all other orders in the same period"
        label = label or "orders with " + "; ".join(f"{k} in {v}" for k, v in filters.items())
        period = (pd.Timestamp(start).date(), pd.Timestamp(end).date()) if start is not None else None
        return Scope("segment", label, ref_label, focus, ref, period, dict(filters))

    # ------------------------------------------------------------------ tests
    def measure(self, s: Scope) -> Result:
        """The symptom: late rate of the focus orders vs the reference, with 95% intervals, plus stage medians."""
        rows = []

        def row(name, df):
            k, n = int(df["late"].sum()), len(df)
            lo, hi = wilson(k, n)
            rows.append([name, n, k, pct(k / n) if n else None, pct(lo), pct(hi), r1(df["h"].median()),
                         r1(df["t"].median()), r1(df["p"].median()), r1(df["t"].mean())])
            return k, n

        k1, n1 = row("focus", s.focus)
        k0, n0 = row("reference", s.ref)
        d = self.delivered()
        for u in s.baseline_units + s.excluded_units:
            sub = self._apply(d, s.filters)
            sub = sub[sub[s.unit if s.unit == "month" else "week"] == u]
            row(f"{s.unit} {u} ({'in baseline' if u in s.baseline_units else 'left out: abnormal'})", sub)
        diff, lo, hi = diff_ci(k1, n1, k0, n0)
        rows.insert(2, ["difference (focus - reference)", None, None, pct(diff), pct(lo), pct(hi), None, None, None, None])
        summary = {"focus_rate": pct(k1 / n1) if n1 else None, "ref_rate": pct(k0 / n0) if n0 else None,
                   "rise_pp": pct(diff), "rise_low_pp": pct(lo), "rise_high_pp": pct(hi), "focus_orders": n1,
                   "focus_late": k1, "ref_orders": n0, "focus": s.label, "reference": s.ref_label,
                   "baseline_units": s.baseline_units, "excluded_units": s.excluded_units}
        cols = ["group", "orders", "late_orders", "late_pct", "ci_low_pct", "ci_high_pct", "median_handover_days",
                "median_transit_days", "median_promised_days", "mean_transit_days"]
        method = (f"Late = delivered on a later calendar date than promised. Focus: {s.label}. Compared with "
                  f"{s.ref_label}. 95% Wilson intervals; the difference uses a normal approximation.")
        return Result("measure", "Late deliveries: focus vs reference", cols, rows, summary, method,
                      "measured", self.provenance())

    def stages(self, s: Scope) -> Result:
        """How much of the rise each stage explains: counterfactual late rates where one or more of hand-over,
        transit and promised days take the reference distribution (matched within the same customer area in time
        comparisons), combined with exact Shapley values so the shares add up."""
        F, R = s.focus, s.ref
        actual = F["late"].mean()
        mapped: dict[str, np.ndarray] = {}
        within = self.m.map_within if s.mode == "time" else None
        for st, col in (("handover", "h"), ("transit", "t"), ("promise", "p")):
            out = F[col].to_numpy(dtype=float).copy()
            if within and within in F.columns:
                done = np.zeros(len(F), dtype=bool)
                for seg, idx in F.groupby(within).indices.items():
                    ref_vals = R.loc[R[within] == seg, col].to_numpy(dtype=float)
                    if len(ref_vals) >= MIN_SEGMENT:
                        out[idx] = quantile_map(out[idx], ref_vals)
                        done[idx] = True
                if (~done).any():
                    out[~done] = quantile_map(out[~done], R[col].to_numpy(dtype=float))
            else:
                out = quantile_map(out, R[col].to_numpy(dtype=float))
            mapped[st] = np.rint(out)
        base = {"handover": F["h"].to_numpy(dtype=float), "transit": F["t"].to_numpy(dtype=float),
                "promise": F["p"].to_numpy(dtype=float)}

        def late_rate(reverted: frozenset) -> float:
            v = {k: (mapped[k] if k in reverted else base[k]) for k in base}
            return float(np.mean(v["handover"] + v["transit"] > v["promise"])) if len(F) else float("nan")

        gain = shapley(list(base), lambda S: actual - late_rate(S))   # how much reverting each stage removes
        rise = s.rise
        explained = sum(gain.values())
        rows = []
        for st, col in (("handover", "h"), ("transit", "t"), ("promise", "p")):
            share = gain[st] / rise if rise and rise > 0 else float("nan")
            rows.append([st, STAGES[st], r1(F[col].median()), r1(R[col].median()), r1(F[col].quantile(0.8)),
                         r1(R[col].quantile(0.8)), r1(F[col].mean()), r1(R[col].mean()), pct(gain[st], 2),
                         pct(share, 1)])
        other = rise - explained
        rows.append(["other", "everything else (mix of orders, interactions)", None, None, None, None, None, None,
                     pct(other, 2), pct(other / rise, 1) if rise and rise > 0 else None])
        shares = {st: (gain[st] / rise if rise and rise > 0 else None) for st in base}
        top = max(shares, key=lambda k: shares[k] or -1)
        summary = {"rise_pp": pct(rise), "explained_pp": pct(explained), "shares": {k: pct(v, 1) if v is not None
                   else None for k, v in shares.items()}, "top_stage": top, "focus": s.label, "reference": s.ref_label}
        cols = ["stage", "meaning", "focus_median_days", "ref_median_days", "focus_p80_days", "ref_p80_days",
                "focus_mean_days", "ref_mean_days", "contribution_pp", "share_of_rise_pct"]
        method = ("For each stage, every focus order's days are replaced by the reference value at the same rank "
                  + (f"(within the same {within}) " if within else "")
                  + "and the late rate is recomputed. Exact Shapley values over the three stages split the rise so the "
                  "contributions add up; 'other' is what no stage explains.")
        return Result("stages", "Which delivery stage explains the rise", cols, rows, summary, method, "measured",
                      self.provenance())

    def segments(self, s: Scope, dim: str, top: int = 8) -> Result:
        """Where the rise sits: each segment's contribution, split into rate effect (it got worse) and mix effect
        (more orders came from it). Contributions add up to the whole rise."""
        if dim not in s.focus.columns:
            raise ValueError(f"unknown dimension {dim!r}")
        ss = shift_share(s.focus, s.ref, dim)
        rise = s.rise
        ss["share"] = ss["total"] / rise if rise else np.nan
        ss = ss.sort_values("total", ascending=False)
        shown = ss[(ss["focus_orders"] >= 20) | (ss["ref_orders"] >= 20)].head(top)
        rows = [[str(seg), int(r.focus_orders), int(r.ref_orders), pct(r.focus_rate), pct(r.ref_rate),
                 pct(r.rate_effect, 2), pct(r.mix_effect, 2), pct(r.share, 1), pct(r.focus_weight, 1)]
                for seg, r in shown.iterrows()]
        mix_share = ss["mix_effect"].sum() / rise if rise else float("nan")
        rows.append(["(all segments: mix effect)", None, None, None, None, None, pct(ss["mix_effect"].sum(), 2),
                     pct(mix_share, 1), None])
        conc = [str(seg) for seg, r in shown.iterrows() if r.share >= 0.15 and r.share >= 1.5 * r.focus_weight]
        summary = {"dimension": dim, "rise_pp": pct(rise), "mix_share_pct": pct(mix_share, 1), "concentrated_in": conc,
                   "top": [str(i) for i in shown.index[:3]]}
        cols = [dim, "focus_orders", "ref_orders", "focus_late_pct", "ref_late_pct", "rate_effect_pp", "mix_effect_pp",
                "share_of_rise_pct", "share_of_focus_orders_pct"]
        method = ("Shift-share decomposition: rate effect = average weight x change in the segment's late rate; mix "
                  "effect = average rate x change in its share of orders. They add up exactly to the overall change.")
        return Result("segments", f"Where the rise sits, by {self.m.dims.get(dim, dim)}", cols, rows, summary,
                      method, "measured", self.provenance())

    def event(self, event_id: str, s: Scope | None = None) -> Result:
        """Did a dated event cause the effect? Weekly carrier transit and late rate by the week parcels were handed
        to the carrier (the week they were exposed), around the event. The effect must start with the event: a
        level already raised before it, or a change in line with the existing trend, does not count."""
        ev = self.events()
        row = ev[ev["event_id"] == event_id]
        if row.empty:
            raise ValueError(f"unknown event {event_id!r}")
        e = row.iloc[0]
        start = pd.Timestamp(e["start_date"])
        end = pd.Timestamp(e["end_date"]) if pd.notna(e["end_date"]) and str(e["end_date"]) not in ("", "None") else start
        wk0 = start - pd.Timedelta(days=start.weekday())
        d = self.delivered()
        region = str(e["region"] or "")
        regional = bool(self.m.region_map) and region in set(self.m.region_map)
        weeks = pd.date_range(wk0 - pd.Timedelta(weeks=26), end + pd.Timedelta(weeks=4), freq="W-MON")
        g = d[d["handover_week"].isin(weeks)].groupby("handover_week").agg(n=("t", "size"), transit=("t", "mean"),
                                                                           late=("late", "mean"))
        g = g.reindex(weeks)
        if regional:
            gr = d[(d["handover_week"].isin(weeks)) & (d[self.m.region_source] == region)].groupby("handover_week")
            g["region_transit"] = gr["t"].mean().reindex(weeks)
            g["region_late"] = gr["late"].mean().reindex(weeks)
            go = d[(d["handover_week"].isin(weeks)) & (d[self.m.region_source] != region)].groupby("handover_week")
            g["rest_transit"] = go["t"].mean().reindex(weeks)
        rel = ((g.index - wk0).days // 7).astype(int)
        normal = g[(rel >= -26) & (rel <= -7)]
        pre = g[(rel >= -6) & (rel <= -2)]
        post = g[(rel >= -1) & (g.index <= end)]
        normal_t, normal_l = float(normal["transit"].median()), float(normal["late"].median())
        level_t = float(pre["transit"].iloc[-2:].mean())
        level_l = float(pre["late"].iloc[-2:].mean())

        def trend_gap(col: str) -> float:
            x = ((pre.index - wk0).days // 7).to_numpy(dtype=float)
            y = pre[col].to_numpy(dtype=float)
            ok = ~np.isnan(y)
            if ok.sum() < 3 or post.empty:
                return float("nan")
            a, b = np.polyfit(x[ok], y[ok], 1)
            xp = ((post.index - wk0).days // 7).to_numpy(dtype=float)
            return float(np.nanmean(post[col].to_numpy(dtype=float) - (a * xp + b)))

        jump_t, jump_l = trend_gap("transit"), trend_gap("late")
        elevated = (level_t - normal_t >= max(1.0, 0.15 * normal_t)) or (level_l - normal_l >= 0.05)
        effect = (jump_t >= max(1.0, 0.10 * level_t)) or (jump_l >= 0.05)
        aligned = None
        if regional and not post.empty:
            rgap = float(np.nanmean(post["region_transit"] - pre["region_transit"].iloc[-2:].mean()))
            ogap = float(np.nanmean(post["rest_transit"] - pre["rest_transit"].iloc[-2:].mean()))
            aligned = (rgap - ogap) >= 1.0
        if effect and (aligned is None or aligned):
            verdict = "partial" if elevated else "supports"
        else:
            verdict = "refutes"
        show = g[(rel >= -8) & (g.index <= end + pd.Timedelta(weeks=3))]
        pad = [None, None] if regional else []
        rows = [["normal level (weeks 26 to 7 before)", None, "summary", None, r1(normal_t), pct(normal_l)] + pad,
                ["just before (2 weeks)", None, "summary", None, r1(level_t), pct(level_l)] + pad,
                ["change with the event, vs prior trend", None, "summary", None, r1(jump_t), pct(jump_l)] + pad]
        for w, r in show.iterrows():
            k = int(((w - wk0).days) // 7)
            phase = "event" if w >= wk0 and w <= end else ("in flight" if k == -1 else ("before" if k < -1 else "after"))
            row_ = [w.strftime("%Y-%m-%d"), k, phase, int(r.n) if r.n == r.n else 0, r1(r.transit), pct(r.late)]
            if regional:
                row_ += [r1(r.region_transit), r1(r.rest_transit)]
            rows.append(row_)
        cols = ["handover_week", "weeks_from_event", "phase", "parcels_handed_over", "mean_transit_days", "late_pct"]
        if regional:
            cols += [f"{region}_mean_transit_days", "rest_mean_transit_days"]
        summary = {"event": event_id, "name": str(e["name"]), "start": str(start.date()), "end": str(end.date()),
                   "region": region, "normal_transit_days": r1(normal_t), "pre_event_transit_days": r1(level_t),
                   "normal_late_pct": pct(normal_l), "pre_event_late_pct": pct(level_l),
                   "elevated_before": bool(elevated), "transit_jump_vs_trend_days": r1(jump_t),
                   "late_jump_vs_trend_pp": pct(jump_l), "effect_with_event": bool(effect),
                   "region_aligned": aligned}
        method = ("Weeks are the week a parcel was handed to the carrier. 'Normal' = median of weeks 26 to 7 before the "
                  "event; 'before' = the two weeks before the in-flight week. The jump is measured against the trend of "
                  "weeks 6 to 2 before, extrapolated, so a change that was already under way doesn't count. Supports: "
                  "a jump of at least 1 day (or 10%) of transit, or 5 points of late rate, starting with the event"
                  + (", larger in the event's region than elsewhere" if regional else "")
                  + ". Partial: the jump happened, but the level was already raised before the event.")
        return Result("event", f"Event timing: {e['name']}", cols, rows, summary, method, verdict, self.provenance())

    def volume(self, s: Scope) -> Result:
        """Did more orders overload the chain? Orders per day vs the reference, and whether hand-over or transit
        slowed in the busiest weeks."""
        def per_day(df):
            return len(df) / max(df["purchase"].nunique(), 1) if not df.empty else 0.0

        ref = s.ref
        if s.mode == "time" and s.period:  # the 4 weeks just before the period, same filters
            d = self._apply(self.delivered(), s.filters)
            a = pd.Timestamp(s.period[0])
            ref = d[(d["purchase"] >= a - pd.Timedelta(days=28)) & (d["purchase"] < a)]
        f_day, r_day = per_day(s.focus), per_day(ref)
        ratio = f_day / r_day if r_day else float("nan")
        dh = s.focus["h"].mean() - ref["h"].mean()
        dt = s.focus["t"].mean() - ref["t"].mean()
        wk = pd.concat([s.focus, ref]).groupby("week").agg(orders=("late", "size"), late=("late", "mean"))
        corr = float(wk["orders"].rank().corr(wk["late"].rank())) if len(wk) >= 5 else float("nan")  # Spearman
        surge = ratio >= 1.25 and (dh >= 0.5 or dt >= 1.0)
        verdict = "supports" if surge else ("refutes" if ratio < 1.1 or (dh < 0.5 and dt < 1.0) else "measured")
        rows = [["focus", len(s.focus), r1(f_day), r1(s.focus["h"].mean()), r1(s.focus["t"].mean())],
                ["4 weeks before" if ref is not s.ref else "reference", len(ref), r1(r_day), r1(ref["h"].mean()),
                 r1(ref["t"].mean())],
                ["focus / before", None, round(ratio, 2) if ratio == ratio else None, None, None]]
        summary = {"orders_per_day_ratio": round(ratio, 2) if ratio == ratio else None,
                   "focus_orders_per_day": r1(f_day), "ref_orders_per_day": r1(r_day),
                   "handover_change_days": r1(dh), "transit_change_days": r1(dt),
                   "weekly_orders_vs_late_spearman": round(corr, 2) if corr == corr else None}
        method = ("Orders per day (per day with orders, by purchase date) in the focus vs the 4 weeks just before it "
                  "(growth of the business is not a surge). A surge counts if volume is at least 25% higher AND hand-over "
                  "(+0.5 day) or transit (+1 day) slowed with it.")
        return Result("volume", "Order volume", ["group", "orders", "orders_per_day", "mean_handover_days",
                      "mean_transit_days"], rows, summary, method, verdict, self.provenance())

    def data_quality(self, s: Scope) -> Result:
        """Could the rise be an artefact? Missing or impossible dates, orders left out, duplicates, and how much the
        definition of 'late' matters."""
        f = self.facts()
        if s.period:
            a, b = pd.Timestamp(s.period[0]), pd.Timestamp(s.period[1])
            pool = self._apply(f[(f["purchase"] >= a) & (f["purchase"] < b)], s.filters)
        else:
            pool = self._apply(f, s.filters)
        n = len(pool)
        delivered_status = pool["status"].astype(str).isin(self.m.delivered_status)
        missing = int((delivered_status & pool[["handover", "delivered", "promised"]].isna().any(axis=1)).sum())
        impossible = int((delivered_status & ((pool["h"] < 0) | (pool["t"] < 0))).sum())
        not_delivered = int((~delivered_status).sum())
        dupes = max(self._raw_rows - len(f), 0)
        foc = s.focus
        ts_rate, date_rate = foc["late_ts"].mean(), foc["late"].mean()
        bad = (missing + impossible) / max(n, 1)
        verdict = "supports" if bad > 0.02 else "refutes"
        rows = [["orders in scope", n], ["not delivered (left out)", not_delivered],
                ["delivered but a date is missing", missing], ["impossible date order", impossible],
                ["duplicate order rows in the source", dupes],
                ["late % comparing calendar dates", pct(date_rate)], ["late % comparing timestamps", pct(ts_rate)]]
        summary = {"anomaly_share_pct": pct(bad, 2), "definition_gap_pp": pct(ts_rate - date_rate),
                   "not_delivered": not_delivered}
        method = ("Counts delivered orders with missing or impossible dates (an artefact counts if over 2%), and "
                  "compares the calendar-date definition of late with a raw timestamp comparison.")
        return Result("data_quality", "Data quality and definitions", ["check", "result"], rows, summary, method,
                      verdict, self.provenance())

    TERMS = {"delay": r"atras", "strike": r"\bgreve", "not received": r"n[aã]o (?:recebi|chegou)", "post office": r"correio"}

    def text(self, s: Scope) -> Result:
        """What customers wrote: review mentions per 1,000 orders, focus vs reference, with verbatim examples."""
        rv = self.reviews()
        if rv.empty:
            raise ValueError("this dataset has no customer review text")
        txt = (rv["title"].astype(str) + " " + rv["message"].astype(str)).str.lower()
        rv = rv.assign(txt=txt)
        rows, samples = [], []
        fids, rids = set(s.focus["order_id"]), set(s.ref["order_id"])
        rf, rr = rv[rv["order_id"].isin(fids)], rv[rv["order_id"].isin(rids)]
        for name, rx in self.TERMS.items():
            mf = rf["txt"].str.contains(rx, regex=True)
            mr = rr["txt"].str.contains(rx, regex=True)
            a = 1000 * mf.sum() / max(len(s.focus), 1)
            b = 1000 * mr.sum() / max(len(s.ref), 1)
            rows.append([name, int(mf.sum()), r1(a), int(mr.sum()), r1(b), round(float(a / b), 2) if b else None])
            for _, r in rf[mf].head(2).iterrows():
                msg = str(r["message"]).strip() or str(r["title"]).strip()
                if msg:
                    samples.append({"review_id": r["review_id"], "term": name, "text": msg[:240]})
        summary = {"samples": samples[:6]}
        method = "Reviews of the focus and reference orders that contain each term (Portuguese), per 1,000 orders."
        return Result("text", "What customers wrote", ["term", "focus_mentions", "focus_per_1000", "ref_mentions",
                      "ref_per_1000", "ratio"], rows, summary, method, "measured", self.m.reviews_sql)

    def promise_buffer(self, s: Scope, dim: str | None = None, quantile: float = 0.8) -> Result:
        """Prescriptive: how many days to add to each segment's promise so that (in the focus orders) the given
        share would have arrived on time, and how many late orders that would have avoided."""
        dim = dim or self.m.map_within
        rows = []
        F = s.focus
        total_avoided = 0
        for seg, g in F.groupby(dim):
            if len(g) < 20:
                continue
            pq = float(np.quantile(g["d"], quantile))
            med_p = float(g["p"].median())
            buf = max(0, math.ceil(pq - med_p))
            late_now = int(g["late"].sum())
            late_buf = int(((g["d"]) > (g["p"] + buf)).sum())
            total_avoided += late_now - late_buf
            rows.append([str(seg), len(g), r1(med_p), r1(pq), buf, late_now, late_buf, pct(late_now / len(g)),
                         pct(late_buf / len(g))])
        rows.sort(key=lambda r: -(r[5] - r[6]))
        summary = {"segments_needing_buffer": sum(1 for r in rows if r[4] > 0), "late_avoided": total_avoided,
                   "quantile": quantile, "dimension": dim}
        method = (f"For each {dim}: the {int(quantile * 100)}th percentile of actual delivery days (hand-over + transit) "
                  "against the median promised days; the buffer is the gap in whole days. 'Late with buffer' recounts "
                  "the same orders against promise + buffer.")
        return Result("promise_buffer", f"Promise buffer by {self.m.dims.get(dim, dim)}",
                      [dim, "orders", "median_promised_days", f"p{int(quantile * 100)}_delivery_days", "buffer_days",
                       "late_orders", "late_with_buffer", "late_pct", "late_pct_with_buffer"],
                      rows[:15], summary, method, "measured", self.provenance())


# ---------------------------------------------------------------------- question parsing helpers
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]


def period_from_question(q: str, as_of: date | None = None) -> tuple[date, date, str] | None:
    """A month, explicit date range or 'since <month>' named in the question, e.g. 'March 2018' ->
    (2018-03-01, 2018-04-01); 'since September' -> (September 1 before as_of, the day after as_of)."""
    t = q.lower()
    m = re.search(r"\bsince\s+(?:early\s+|mid[- ]|late\s+)?(" + "|".join(_MONTHS) + "|" + "|".join(n[:3] for n in _MONTHS)
                  + r")[a-z]*\.?(?:\s+(20\d\d))?", t)
    if m and as_of is not None:
        mo = next(i for i, n in enumerate(_MONTHS, 1) if n.startswith(m.group(1)[:3]))
        y = int(m.group(2)) if m.group(2) else (as_of.year if mo <= as_of.month else as_of.year - 1)
        a, b = date(y, mo, 1), as_of + timedelta(days=1)
        return a, b, f"since {_MONTHS[mo - 1].title()} {y}"
    m = re.search(r"\b(\d{1,2})\s*[-–]\s*(\d{1,2})\s+(" + "|".join(_MONTHS) + r")[a-z]*\.?\s+(20\d\d)", t)
    if m:
        mo = next(i for i, n in enumerate(_MONTHS, 1) if n.startswith(m.group(3)[:3]))
        y = int(m.group(4))
        a, b = date(y, mo, int(m.group(1))), date(y, mo, int(m.group(2))) + timedelta(days=1)
        return a, b, f"{a.isoformat()} to {(b - timedelta(days=1)).isoformat()}"
    names = "|".join(_MONTHS) + "|" + "|".join(n[:3] for n in _MONTHS)
    m2 = re.search(rf"\b({names})[a-z]*\s+(?:and|to|through|-|–)\s+({names})[a-z]*\.?(?:\s+(20\d\d))?\b", t)
    if m2 and (m2.group(3) or as_of is not None):
        mo1 = next(i for i, n in enumerate(_MONTHS, 1) if n.startswith(m2.group(1)[:3]))
        mo2 = next(i for i, n in enumerate(_MONTHS, 1) if n.startswith(m2.group(2)[:3]))
        y = int(m2.group(3)) if m2.group(3) else (as_of.year if mo2 <= as_of.month else as_of.year - 1)
        y1 = y if mo1 <= mo2 else y - 1
        a = date(y1, mo1, 1)
        b = date(y + (mo2 == 12), mo2 % 12 + 1, 1)
        if as_of is not None and b > as_of + timedelta(days=1):
            b = as_of + timedelta(days=1)
        return a, b, f"{_MONTHS[mo1 - 1].title()} to {_MONTHS[mo2 - 1].title()} {y}"
    m = re.search(rf"\b({names})[a-z]*\.?\s+(20\d\d)\b", t)
    if not m and as_of is not None:
        m0 = re.search(rf"\b(?:in|during|for)\s+({names})[a-z]*\b", t)
        if m0:
            mo = next(i for i, n in enumerate(_MONTHS, 1) if n.startswith(m0.group(1)[:3]))
            y = as_of.year if mo <= as_of.month else as_of.year - 1
            a, b = date(y, mo, 1), date(y + (mo == 12), mo % 12 + 1, 1)
            return a, min(b, as_of + timedelta(days=1)), f"{_MONTHS[mo - 1].title()} {y}"
    if m:
        mo = next(i for i, n in enumerate(_MONTHS, 1) if n.startswith(m.group(1)[:3]))
        y = int(m.group(2))
        a = date(y, mo, 1)
        b = date(y + (mo == 12), mo % 12 + 1, 1)
        return a, b, f"{_MONTHS[mo - 1].title()} {y}"
    return None


_EVENT_STOP = {"national", "workers", "worker", "brazil", "against", "during", "service", "services", "public",
               "state", "states", "city", "rio", "janeiro", "paulo", "record", "parcels", "parcel", "delivery",
               "deliveries", "areas", "area", "through", "price", "metro", "emergency"}


def baseline_from_question(q: str, as_of: date | None) -> tuple[date, date] | None:
    """An explicit comparison period, e.g. '... compared with August' -> that month."""
    t = q.lower()
    names = "|".join(_MONTHS) + "|" + "|".join(n[:3] for n in _MONTHS)
    m = re.search(rf"\b(?:compared (?:with|to)|vs\.?|versus|than (?:in )?)\s+({names})[a-z]*(?:\s+(20\d\d))?", t)
    if not m or as_of is None and not m.group(2):
        return None
    mo = next(i for i, n in enumerate(_MONTHS, 1) if n.startswith(m.group(1)[:3]))
    y = int(m.group(2)) if m.group(2) else (as_of.year if mo <= as_of.month else as_of.year - 1)
    return date(y, mo, 1), date(y + (mo == 12), mo % 12 + 1, 1)


def events_in_text(q: str, events: pd.DataFrame) -> list[str]:
    """Events named in the question, matched on distinctive whole words of their name (not place names), e.g.
    'Carnival 2018', 'truckers' strike', 'cargo theft'. A year in the question must fall within the event."""
    t = q.lower()
    years = set(re.findall(r"\b(20\d\d)\b", t))
    out = []
    for _, e in events.iterrows():
        words = [w for w in re.findall(r"[a-z]+", str(e["name"]).lower().replace("'", ""))
                 if len(w) > 4 and w not in _EVENT_STOP]
        hits = [w for w in words if re.search(rf"\b{w}", t)]
        y0 = str(e["start_date"])[:4]
        y1 = str(e["end_date"])[:4] if str(e["end_date"]) not in ("", "None", "nan", "NaT") else y0
        in_year = not years or any(y0 <= y <= y1 for y in years)
        if hits and in_year:
            out.append((len(hits), str(e["event_id"])))
    return [eid for _, eid in sorted(out, key=lambda x: -x[0])]

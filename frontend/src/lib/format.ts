export const inr = (v?: number | null) =>
  v === undefined || v === null || Number.isNaN(v)
    ? "—"
    : new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(v);

export const num = (v?: number | null) => (v === undefined || v === null ? "—" : new Intl.NumberFormat("en-IN").format(v));

export const pct = (v?: number | null) => (v === undefined || v === null ? "—" : `${Math.round(v * 100)}%`);

export function duration(s?: number | null) {
  if (s === undefined || s === null) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${Math.round(s - m * 60)}s`;
}

export function when(iso?: string) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

/** Run ids look like 20261021-232646-e917. */
export function runDate(runId: string) {
  const m = runId.match(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/);
  if (!m) return "";
  return when(`${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}`);
}

export const CITATION = /\[([A-Z][A-Za-z0-9]*\.[QDP]\d+)\](?!\()/g;

/** ₹2.67 Cr / ₹78.3 L style for big rupee amounts. */
export function inrShort(v?: number | null) {
  if (v === undefined || v === null) return "—";
  if (v >= 1e7) return `₹${(v / 1e7).toFixed(2)} Cr`;
  if (v >= 1e5) return `₹${(v / 1e5).toFixed(1)} L`;
  return inr(v);
}

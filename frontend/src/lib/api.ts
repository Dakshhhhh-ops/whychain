// Types and calls for the WhyChain API.

export type TaskT = {
  id: string;
  round: number;
  type: string; // "measure" | "hypothesis"
  statement: string;
  agent: "data" | "docs" | "tool";
  checks?: string[];
  priority?: number;
  addresses_gap?: string | null;
};

export type Finding = {
  task_id: string;
  statement: string;
  type: string;
  agent_kind: "data" | "docs" | "tool";
  agent: string;
  verdict: string;
  claim: string;
  key_numbers?: unknown;
  who_knew?: string | null;
  confidence?: number | null;
  evidence_ids: string[];
  steps: number;
  sql_errors_recovered: number;
  quotes_rejected: number;
  round: number;
};

export type SqlEvidence = {
  id: string;
  type: "sql";
  task_id: string;
  agent: string;
  purpose: string;
  sql: string;
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  harness: string;
  ms: number;
  reproduced?: boolean;
};

export type DocEvidence = {
  id: string;
  type: "doc";
  task_id: string;
  agent: string;
  doc_id: string;
  title?: string | null;
  doc_type?: string | null;
  sent_at?: string | null;
  sender?: string | null;
  recipients?: string | null;
  quote: string;
};

export type OptimizationEvidence = {
  id: string;
  type: "optimization";
  task_id: string;
  agent: string;
  purpose: string;
  outcome: string;
  verified: boolean;
  certificate: boolean;
  certified_with: string | null;
  objective_inr: number;
  checks: { name: string; passed: boolean; detail?: string }[];
  stats: { variables: number; constraints: number; nonzeros: number };
  summary: {
    skus: number;
    need_units: number;
    truck_units: number;
    trucks: { lane: string; units: number; trucks: number; cost_inr: number; transit_days: number }[];
    truck_cost_inr: number;
    air_units: number;
    air_cost_inr: number;
    unfilled_units: number;
    unfilled_by_dc: Record<string, number>;
    covered_share: number;
  };
  columns: string[];
  rows: unknown[][];
  row_count: number;
  insights: string[];
  notes?: string[];
  queries: Record<string, string>;
  scope: Record<string, unknown>;
  to_warehouses: string[];
  deadline_days: number;
  cover_days: number;
  harness: string;
};

export type AnalysisEvidence = {
  id: string;
  type: "analysis";
  task_id: string;
  agent: string;
  tool: string;
  title: string;
  columns: string[];
  rows: unknown[][];
  row_count: number;
  summary: Record<string, unknown>;
  method: string;
  verdict: string;
  sql: string;
  harness?: string;
  notes?: string[];
};

export type Evidence = SqlEvidence | DocEvidence | OptimizationEvidence | AnalysisEvidence;

export type Verdict = {
  status: string; // confirmed | rejected | needs_more
  reason: string;
  is_cause?: boolean;
  share_of_symptom?: number | null;
  round: number;
};

export type Evaluation = {
  decision: string;
  round: number;
  summary?: string;
  symptom_explained?: number;
  red_flags?: string[];
  gaps?: { what: string; why?: string }[];
  forced_accept?: boolean;
};

export type Action = {
  id: string;
  title: string;
  steps?: string;
  addresses?: string[];
  orders_protected?: number | null;
  value_protected_inr?: number | null;
  est_cost_inr: number | null;
  unsupported_figures?: string[];
  time_to_effect?: string;
  confidence: number;
  policy_notes?: string;
  evidence: string[];
  needs_approval: boolean;
  approval_reasons: string[];
  decision: string; // pending | approved | rejected
  decision_note?: string | null;
  currency?: string;
};

export type MemoryItem = {
  id?: string;
  content: string;
  kind?: string;
  tier?: string;
  confidence?: number;
  score?: number | null;
  run_id?: string | null;
  agent?: string | null;
  tags?: string[];
  created_at?: string;
  provenance?: { source?: string; query?: string | null; doc?: string | null; origin?: string | null };
};

export type DataMap = {
  sources?: { source: string; what_it_holds?: string; grain?: string }[];
  joins?: { left: string; right: string; meaning?: string; match_rate?: number }[];
  caveats?: string[];
};

export type RunResult = {
  run_id: string;
  status: string;
  question: string;
  dataset?: string;
  headline?: string;
  brief?: string;
  brief_check?: { checked: number; unverified: string[] };
  symptom?: string;
  round?: number;
  harness?: string;
  harness_notes?: string[];
  memory?: { online: boolean; backend: string; url?: string };
  llm?: { calls: number; by_provider: Record<string, number>; fallbacks: number; tokens: number };
  tasks?: TaskT[];
  findings?: Finding[];
  evidence?: Record<string, Evidence>;
  verdicts?: Record<string, Verdict>;
  evaluations?: Evaluation[];
  actions?: Action[];
  risks?: { risk: string; exposure?: number; exposure_inr?: number; evidence?: string[] }[];
  lessons?: string[];
  memories?: MemoryItem[];
  data_map?: DataMap;
};

export type RunSummary = {
  run_id: string;
  dataset?: string;
  question: string;
  headline: string;
  status: string;
  harness?: string;
  rounds?: number;
  causes?: number;
  evidence?: number;
  actions?: number;
  needs_approval?: number;
  model_calls?: number;
  started_at?: string;
  duration_s?: number;
  queries?: number;
};

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type TraceEvent = { seq: number; t: number; ts: string; run_id: string; kind: string; agent: string; [k: string]: any };

export type Status = {
  harness: { name: string; ok: boolean; notes: string[]; erp?: string; url?: string; error?: string };
  memory: { online: boolean; backend: string; url: string; engines?: Record<string, boolean>; embedder?: string | null };
  models: Record<string, { configured: boolean; model: string }>;
  as_of: string;
  company?: string;
  row_counts: Record<string, number>;
  models_ready?: boolean;
  preview?: boolean;
  dataset?: { key: string; name: string; real: boolean; currency: string };
};

export type Sample = { title: string; tag: string; question: string };

export type SourcesResponse = {
  harness: string;
  notes: string[];
  tables: { source: string; table: string; fqn: string; row_count: number | null; columns: { name: string; type: string }[] }[];
  source_notes: Record<string, string>;
  data_map: DataMap | null;
};

export type WorldState = {
  relations?: { from_name?: string; type?: string; to_name?: string; open?: boolean; confidence?: number }[];
  entities?: { id: string; type: string; name: string }[];
};

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  if (!r.ok) {
    const body = await r.text();
    throw new Error(`${r.status} ${r.statusText}: ${body.slice(0, 200)}`);
  }
  return (await r.json()) as T;
}

const dsq = (ds?: string, first = true) => (ds ? `${first ? "?" : "&"}dataset=${encodeURIComponent(ds)}` : "");

export const api = {
  status: (ds?: string) => request<Status>(`/api/status${dsq(ds)}`),
  samples: (ds?: string) => request<Sample[]>(`/api/samples${dsq(ds)}`),
  runs: (ds?: string) => request<RunSummary[]>(`/api/runs${dsq(ds)}`),
  run: (id: string) => request<RunResult>(`/api/runs/${id}`),
  trace: (id: string) => request<TraceEvent[]>(`/api/runs/${id}/trace`),
  world: (id: string) => request<WorldState>(`/api/runs/${id}/world`),
  memory: (ds?: string) =>
    request<{ status: Status["memory"]; count: number; items: MemoryItem[]; tiers: Record<string, number> }>(`/api/memory?limit=200${dsq(ds, false)}`),
  sources: (ds?: string) => request<SourcesResponse>(`/api/sources${dsq(ds)}`),
  start: (question: string, dataset?: string) =>
    request<{ run_id: string }>("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, dataset }),
    }),
  decide: (runId: string, actionId: string, decision: "approved" | "rejected", note?: string) =>
    request<{ ok: boolean }>(`/api/runs/${runId}/actions/${actionId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, note }),
    }),
};

/** Subscribe to a run's events. Returns an unsubscribe function. */
export function subscribe(runId: string, onEvent: (e: TraceEvent) => void, onEnd: () => void, replay = 0): () => void {
  const es = new EventSource(`/api/runs/${runId}/events${replay ? `?replay=${replay}` : ""}`);
  es.onmessage = (m) => {
    try {
      onEvent(JSON.parse(m.data) as TraceEvent);
    } catch {
      /* ignore malformed */
    }
  };
  es.addEventListener("end", () => {
    es.close();
    onEnd();
  });
  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) onEnd();
  };
  return () => es.close();
}

export type NetworkNode = { id: string; type: "supplier" | "port" | "dc" | "city"; label: string; sub: string; status?: string | null };
export type NetworkEdge = { from: string; to: string; kind: "inbound" | "lane"; id?: string; carrier?: string; status?: string | null; ontime?: number | null; units?: number };
export type Network = { nodes: NetworkNode[]; edges: NetworkEdge[] };
export type Problem = {
  id: string;
  when?: string | null;
  kind: string;
  severity: "critical" | "high" | "medium";
  title: string;
  metric: string;
  baseline: string;
  detail: string;
  question: string;
  entities: string[];
};
export type TimelinePoint = {
  month: string;
  orders: number;
  late_pct: number;
  seller_late_pct: number;
  carrier_days: number;
  review_score: number;
  strike_mentions: number;
};
export type ExternalEvent = {
  id: string;
  name: string;
  type: string;
  start: string;
  end: string | null;
  region: string;
  summary: string;
  sources: string[];
  month: string;
};
export type KpiCard = { label: string; value: string; sub?: string; tone?: "bad" | null };

export type Overview = {
  as_of: string;
  harness: string;
  kpi_cards: KpiCard[];
  timeline: TimelinePoint[] | null;
  events?: ExternalEvent[];
  states?: { state: string; name: string; orders: number; late_pct: number }[];
  dataset?: { key: string; name: string; real: boolean; currency: string; company?: string };
  kpis?: {
    ontime_recent: number | null;
    ontime_baseline: number | null;
    orders_due_next_week: number;
    backordered_next_week: number;
    value_at_risk_inr: number;
    late_supplier_units: number;
    problems: number;
  };
  problems: Problem[];
  network: Network | null;
};

export const getOverview = (ds?: string) => request<Overview>(`/api/overview${ds ? `?dataset=${encodeURIComponent(ds)}` : ""}`);

export type Deliverable =
  | { kind: "table"; title: string; note?: string | null; source: string; sql: string; columns: string[]; rows: unknown[][]; row_count: number; harness: string }
  | { kind: "email"; title: string; to: string; subject: string; body: string }
  | { kind: "document"; title: string; body: string };

export type Deliverables = {
  status: "none" | "running" | "done" | "error";
  summary?: string;
  deliverables: Deliverable[];
  rejected?: { title: string; reason: string }[];
  steps?: number;
  last?: string;
  error?: string;
};

export const getDeliverables = (runId: string, actionId: string) =>
  request<Deliverables>(`/api/runs/${runId}/actions/${actionId}/deliverables`);

export type RegionRow = {
  state: string;
  name: string;
  orders: number;
  late_pct: number | null;
  base_late_pct: number | null;
  transit_days: number | null;
  base_transit_days: number | null;
};
export type Regions = { month: string | null; baseline: string | null; states: RegionRow[] };
export const getRegions = (ds: string, month?: string | null) =>
  request<Regions>(`/api/regions?dataset=${encodeURIComponent(ds)}${month ? `&month=${month}` : ""}`);

// Folds the stream of trace events into the live picture of an investigation.
import type { TaskT, TraceEvent } from "./api";

export type Stage = "recall" | "scout" | "plan" | "investigate" | "evaluate" | "act" | "report" | "remember" | "done";

export const STAGES: { key: Stage; label: string }[] = [
  { key: "recall", label: "Recall" },
  { key: "scout", label: "Map data" },
  { key: "plan", label: "Plan" },
  { key: "investigate", label: "Investigate" },
  { key: "evaluate", label: "Challenge" },
  { key: "act", label: "Act" },
  { key: "report", label: "Report" },
];

export type LiveTask = TaskT & {
  state: "queued" | "running" | "done";
  queries: number;
  docs: number;
  sqlErrors: number;
  verdict?: string;
  claim?: string;
  evidence: string[];
  evalStatus?: string;
  confidence?: number | null;
  lastThought?: string;
};

export type LiveRound = {
  round: number;
  reasoning?: string;
  symptom?: string;
  taskIds: string[];
  evaluation?: {
    decision: string;
    explained?: number;
    summary?: string;
    gaps?: { what: string; why?: string }[];
    red_flags?: string[];
    forced?: boolean;
  };
};

export type LiveState = {
  question?: string;
  status: "running" | "done" | "error";
  stage: Stage;
  tasks: Record<string, LiveTask>;
  rounds: LiveRound[];
  queries: number;
  sqlErrors: number;
  llmCalls: number;
  fallbacks: number;
  escalations: { action_id: string; title?: string; reasons: string[] }[];
  scout: { skipped: boolean; joins: { left: string; right: string; match_rate: number | null; kept: boolean }[] };
  recalled: number;
  reverified: Record<string, boolean>;
  elapsed: number;
  harness?: string;
  memoryBackend?: string;
  memoryEmbedder?: string;
  dataset?: string;
  error?: string;
  // the answer built by code from the computed tests, published before the written record and the written answer
  preliminary?: { headline: string; summary: string; seconds?: number; t?: number };
  events: TraceEvent[];
};

export const emptyState = (): LiveState => ({
  status: "running",
  stage: "recall",
  tasks: {},
  rounds: [],
  queries: 0,
  sqlErrors: 0,
  llmCalls: 0,
  fallbacks: 0,
  escalations: [],
  scout: { skipped: false, joins: [] },
  recalled: 0,
  reverified: {},
  elapsed: 0,
  events: [],
});

function stageOf(agent: string): Stage | null {
  if (agent === "Data Scout") return "scout";
  if (agent === "Planner") return "plan";
  if (agent.startsWith("Investigator") || agent.startsWith("Doc Analyst") || agent.startsWith("Analysis engine")) return "investigate";
  if (agent === "Evaluator") return "evaluate";
  if (agent === "Risk & Action") return "act";
  if (agent === "Reporter") return "report";
  return null;
}

export function applyEvent(s: LiveState, e: TraceEvent): LiveState {
  const n: LiveState = { ...s, events: [...s.events, e], elapsed: Math.max(s.elapsed, e.t ?? 0) };
  const task = (id?: string) => (id && n.tasks[id] ? (n.tasks[id] = { ...n.tasks[id] }) : undefined);
  switch (e.kind) {
    case "run_start":
      n.question = e.question;
      n.harness = e.harness;
      n.dataset = e.dataset;
      n.memoryBackend = e.memory?.backend;
      n.memoryEmbedder = e.memory?.embedder ?? undefined;
      break;
    case "agent_start": {
      const st = stageOf(e.agent);
      if (st) n.stage = st;
      const t = task(e.task_id);
      if (t) t.state = "running";
      break;
    }
    case "agent_end":
      if (e.agent === "Memory" && typeof e.memories === "number") n.recalled = e.memories;
      break;
    case "agent_skip":
      if (e.agent === "Data Scout") n.scout = { ...n.scout, skipped: true };
      break;
    case "join_test":
      n.scout = { ...n.scout, joins: [...n.scout.joins, { left: e.left, right: e.right, match_rate: e.match_rate, kept: e.kept }] };
      break;
    case "plan": {
      n.stage = "plan";
      const tasks = { ...n.tasks };
      for (const t of e.tasks as TaskT[]) {
        tasks[t.id] = { ...t, round: e.round, state: "queued", queries: 0, docs: 0, sqlErrors: 0, evidence: [] };
      }
      n.tasks = tasks;
      n.rounds = [...n.rounds, { round: e.round, reasoning: e.reasoning, symptom: e.symptom, taskIds: (e.tasks as TaskT[]).map((t) => t.id) }];
      break;
    }
    case "query": {
      if (e.ok) n.queries += 1;
      else n.sqlErrors += 1;
      const t = task(e.task_id);
      if (t) {
        if (e.ok) t.queries += 1;
        else t.sqlErrors += 1;
        if (e.thought) t.lastThought = e.thought;
      }
      break;
    }
    case "read_doc": {
      const t = task(e.task_id);
      if (t) {
        t.docs += 1;
        t.lastThought = e.thought || `Reading ${e.doc_id}`;
      }
      break;
    }
    case "finding": {
      const t = task(e.task_id);
      if (t) {
        t.state = "done";
        t.verdict = e.verdict;
        t.claim = e.claim;
        t.evidence = e.evidence ?? [];
        t.confidence = e.confidence;
      }
      break;
    }
    case "reverify":
      n.reverified = { ...n.reverified, [e.evidence_id]: e.reproduced };
      break;
    case "evaluation": {
      n.stage = "evaluate";
      const verdicts = (e.verdicts ?? {}) as Record<string, string>;
      for (const [id, status] of Object.entries(verdicts)) {
        const t = task(id);
        if (t) t.evalStatus = status;
      }
      n.rounds = n.rounds.map((r) =>
        r.round === e.round
          ? { ...r, evaluation: { decision: e.decision, explained: e.explained, summary: e.summary, gaps: e.gaps, red_flags: e.red_flags, forced: e.forced } }
          : r,
      );
      break;
    }
    case "escalation":
      n.escalations = [...n.escalations, { action_id: e.action_id, title: e.title, reasons: e.reasons }];
      break;
    case "memory_write":
      n.stage = "remember";
      break;
    case "llm_call":
      n.llmCalls += 1;
      break;
    case "llm_fallback":
      n.fallbacks += 1;
      break;
    case "preliminary":
      n.preliminary = { headline: String(e.headline ?? ""), summary: String(e.summary ?? ""), seconds: e.seconds as number | undefined, t: e.t };
      break;
    case "run_error":
    case "agent_error":
      if (e.kind === "run_error") {
        n.status = "error";
        n.error = e.error;
      }
      break;
    case "run_end":
      n.stage = "done";
      n.status = e.status === "ok" ? "done" : "error";
      break;
  }
  return n;
}

export const foldEvents = (events: TraceEvent[]) => events.reduce(applyEvent, emptyState());

// Scout Intent on WhyChain's own questions: does it route each one where WhyChain's word rules do?
//   npx tsx scout/test-routing.ts              (Stage 1 only; add SCOUT_SEMANTIC=nemotron for Stage 2)
// routing-cases.json holds every question WhyChain knows (its eval questions, sample chips, control-tower
// questions, a few off-topic ones) with the route the rules give. The eval and sample questions must agree; the
// others are reported. Exit code 1 on a must-agree disagreement.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Scout } from "./scout.ts";

const here = dirname(fileURLToPath(import.meta.url));
export const ROUTE: Record<string, string> = {
  "delivery.explain": "battery",
  "supply.explain": "supply",
  "open.investigate": "agent",
  GENERAL: "off_topic",
};

/** WhyChain follows Scout when it is confident (High or Medium band, no clarification needed), else its rules. */
export function chosen(d: Awaited<ReturnType<Scout["decide"]>>, rules: string): { route: string; by: "scout" | "rules" } {
  if (d.risk.level === "block") return { route: "refuse", by: "scout" };
  if ((d.intent.band === "high" || d.intent.band === "medium") && !d.clarification)
    return { route: ROUTE[d.intent.primary] ?? rules, by: "scout" };
  return { route: rules, by: "rules" };
}

const cases = JSON.parse(readFileSync(join(here, "routing-cases.json"), "utf-8")) as
  { question: string; rules: string; must_agree?: boolean }[];
const scout = new Scout({ intentRegistry: join(here, "whychain-intents.json"), policyPack: ["default", "adversarial-tight"] });
let bad = 0;
let agree = 0;
for (const c of cases) {
  const d = await scout.decide({ prompt: c.question, user: { id: "test", role: "analyst" }, surface: "app" });
  const pick = chosen(d, c.rules);
  const ok = pick.route === c.rules || (pick.route === "refuse" && c.rules === "off_topic");
  if (ok) agree++;
  else if (c.must_agree) bad++;
  console.log(`${ok ? "  " : c.must_agree ? "XX" : "!="} ${pick.route.padEnd(9)} by ${pick.by.padEnd(5)} ${d.intent.primary} ${d.intent.confidence} ${d.intent.band}${d.stages.semantic ? " S2" : ""}  rules: ${c.rules.padEnd(9)} ${c.question.slice(0, 80)}`);
}
console.log(`\n${agree}/${cases.length} agree with the rules; ${bad} disagreement(s) where they must agree`);
process.exit(bad ? 1 : 0);

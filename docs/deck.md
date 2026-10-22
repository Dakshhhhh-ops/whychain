# WhyChain: slide-by-slide content

For the slide deck the Official Rules require (section 6): the problem and target users, the solution, the agent
workflow or architecture, the technical stack, how Zetaris and Meterless were used, what was unique about the sponsor
technologies, product visuals and future enhancements. 12 slides. Screenshots are in `docs/img/`.

---

## 1. Title

- **WhyChain**: the agent that explains why your deliveries went wrong.
- One line: Ask why something happened in your supply chain; get the causes, how much each one explains, the
  red herrings ruled out, and actions sized from your data, with every number traceable to the query behind it.
- Track 2, The Agent That Can Explain Why. Open Agent Hackathon 2026.
- Screenshot: `docs/img/control-tower.png` (cropped to the chart with event markers).

## 2. The problem and who has it

- Operations and logistics managers see the symptom on a dashboard (late deliveries up, orders at risk, a backlog) but
  not the cause.
- The cause is spread across systems: the order database, seller and carrier exports, payments, reviews, news and
  contracts. Answering "why" takes an analyst days of joins.
- The obvious suspect is often wrong. In March 2018, Brazil's postal workers went on strike and deliveries got late,
  but the strike wasn't the cause.
- Who: delivery and fulfilment managers at marketplaces and distributors; the analysts who support them.

## 3. What WhyChain does

- Ask in plain English: "Why did late deliveries jump to 19% for orders placed in March 2018?"
- In about 15 seconds, a preliminary answer computed from the data; two to three minutes later, the full brief.
- The brief opens with three lines for a manager (what happened, why, what to do, in money), then the why chain, drawn
  from the Meterless World Model: the symptom, each cause's share, where it sits, what was ruled out (crossed out).
- Then: what the written record says, quoted word for word; actions sized from the data, each with the value at stake,
  the risky ones held for approval; and, folded, the medians, percentiles and counterfactuals behind every line.
- Every number in the brief links to the cell and the SQL it came from, and is checked by code.
- Screenshots: `docs/img/brief.png`; a citation opened: `docs/img/evidence.png` (computed table, "Data via Zetaris",
  and the federated SQL).

## 4. A real answer (Olist, real data)

- 1,328 of 7,003 orders placed in March 2018 were late: 19.0%, against a normal 5.6%.
- Carrier transit explains 72.7% of the rise; shorter promised windows 26.7%.
- The Correios strike (12–13 March) is ruled out: lateness was already high in the two weeks before it.
- What it cost: the 1,328 late orders were worth R$ 187,138 (plus R$ 35,034 of freight); their customers gave them
  2.1 stars on average against 4.2 on time, and 68% of their reviews were 1 or 2 stars (11% on time).
- Actions, each with the value at stake: transit back to normal in SP, RJ, MG (593 orders, R$ 83,603); promise
  buffers per state, e.g. RJ +9 days (255 orders, R$ 37,740, needs approval). Value at stake, not cost: the data has
  no cost of the fix.
- Same computed findings on every run: 62 of 62 identical across three passes, local and through Zetaris.

## 5. How it works (architecture)

- Diagram (from the README's mermaid chart): question → **Meterless Scout Intent** (routes it, guards against prompt
  injection, signs an execution contract) → memory recall (Meterless H-MEM) → the path Scout chose:
  - late deliveries → a fixed battery of tests in the analysis engine (baseline, stages with counterfactuals and
    Shapley shares, where it sits, events against the prior trend, volume, data quality, waiting for stock);
  - stock-outs, backlogs, orders at risk → the supply engine (inbound orders and ports, demand, reorder policy,
    duplicates, volume);
  - anything else → an agent loop (Planner, SQL Investigators, Evaluator).
- Coded verdicts → a preliminary answer in seconds → actions sized by code (and the Optiagent optimizer) → a brief
  whose every number is checked → memory and the why chain written back to Meterless.
- Line at the bottom: "Code computes; agents read, plan, quote and write."

## 6. The agents and where Nemotron works

- Document Analyst: finds and quotes contracts, news and email; quotes verified word for word. Nemotron 3.5 Super.
- Reporter: writes the two-sentence answer from checked facts only. Nemotron 3.5 Super.
- Agent loop (other questions): Planner and Evaluator with reasoning on; Data Scout and Investigators writing Spark
  SQL. Nemotron 3.5 Super, with Nemotron 3 Ultra as the fallback.
- Memory: Nemotron 3 Embed embeds every finding and recall query for H-MEM.
- Risk & Action: templates sized from computed tables; the Optiagent optimizer for stock transfers.
- Screenshot: `docs/img/investigation-live.png` (the Board streaming agents).

## 7. Zetaris: one query across six systems

- Local Zetaris platform (Docker bundle). Order systems via a Postgres JDBC datasource; CSV and JSON exports as
  Lightning file tables read in place. Nothing is copied.
- 26 tables registered; every row count checked against the source.
- One federated fact query joins the order system and the seller export; agents write Spark SQL against all six
  sources; every cited result can be re-run through Zetaris.
- Same answers as a local DuckDB copy: 11 eval questions, identical computed findings.
- Screenshot: `docs/img/sources.png` (Data sources, "Federated by Zetaris").

## 8. Meterless: three engines doing real work

- **Scout Intent** routes every question: WhyChain's registry (late deliveries, a shortage, an open investigation, not
  about the data), Nemotron embeddings as its semantic stage, an injection guard, a signed execution contract. Its
  spec ships no code; WhyChain's implementation passes the spec's own eval harness (32-example smoke set: every
  metric 1.000, clarification rate 0.125) and routes all 48 questions WhyChain knows the way its word rules do.

- H-MEM stores every confirmed and ruled-out finding with provenance (run, agent, query, document); new
  investigations recall related findings as hints, never as evidence.
- Recall runs on NVIDIA Nemotron embeddings (one vendored file gained a provider hook; conformance 13/13 with it off
  and on). Reworded questions find the right past finding 5 times in 6, against 2 in 6 with the mock.
- The World Model keeps each run's why chain (causes, shares, where, what explains them, what was ruled out); the
  brief draws it. Screenshot: `docs/img/brief.png` (the chain under the summary).
- Screenshot: `docs/img/memory.png` (the Memory page, or a run's Memory tab with the cause graph).

## 9. What's unique

- Numbers never come from a model. The analysis engine computes; coded rules decide what counts as a cause; a numbers
  check marks anything unsupported (on a probe of 1,000 invented numbers, 3% got through).
- Red herrings are tested, not argued: dated events against the prior trend, a stage must move to count as a cause.
- Actions are sized from data and proven where possible: the stock transfer is Optiagent's proven-optimal plan, with
  a duality certificate.
- Honest evaluation: held-out questions scored on their first run, including a failure (KH2), and the fix disclosed.

## 10. Results

- Final pass, 22 Oct 21:35 to 22:19 IST, one commit (`bdf4e16`), every query through Zetaris: root cause found in
  18/18 questions (8 on real Olist data, 10 on the simulated company), red herring avoided in 15/15, right action in 8/8.
- Held-out questions (ground truth from SQL before any run): 10 asked so far, 9 passed on their first run; 1 failed
  (KH2, a red herring) and led to a rule change. 5/5 still-held-out questions pass in the final pass.
- Reproducible: the computed findings match the previous pass exactly (Olist 62/62, Kaveri 80/80).
- Cost: 6 to 9 model calls and 38 s to 4 minutes per question (the earlier pure-agent design used 50 to 70 calls).

## 11. The prescriptive loop (Kaveri)

- Pune orders at risk: Voltix purchase orders stuck behind the Chennai dock strike.
- Fix: a stock transfer from Mumbai plus air freight, proven optimal, protecting 62 orders for INR 133,590.
- A manager approves; the Action Executor prepares the deliverables, for example a per-SKU transfer order and a
  draft email to the supplier (it is an agent, so what it prepares varies; it takes two to three minutes).
- Screenshots: `docs/img/kaveri-transfer.png` (the actions), `docs/img/deliverables.png` (after approval).

## 12. What's next

- Zetaris Cloud instead of the local bundle; more source types (carrier APIs, ticketing).
- Meterless Nightshift to run the Document Analyst on a local GPU, so contracts and email never leave the building.
- More computed engines (returns, quality, cost-to-serve); fewer questions on the slower agent loop.
- Validate the thresholds against outside data; more held-out questions from people other than the author.

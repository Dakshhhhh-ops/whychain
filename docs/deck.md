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
- It answers with: the symptom against a normal baseline; each cause's share of the problem; where it sits; what was
  ruled out and why; what the written record says, quoted word for word; and actions sized from the data, with the
  risky ones held for approval.
- Every number in the brief links to the cell and the SQL it came from, and is checked by code.
- Screenshot: `docs/img/brief.png`.

## 4. A real answer (Olist, real data)

- 1,328 of 7,003 orders placed in March 2018 were late: 19.0%, against a normal 5.6%.
- Carrier transit explains 72.7% of the rise; shorter promised windows 26.7%.
- The Correios strike (12–13 March) is ruled out: lateness was already high in the two weeks before it.
- Action: promise buffers per state (e.g. RJ +9 days), which would have put 255 of these late orders on time.
- Same computed findings on every run: 62 of 62 identical across three passes, local and through Zetaris.

## 5. How it works (architecture)

- Diagram (from the README's mermaid chart): question → memory recall (Meterless H-MEM) → the kind of question picks
  the path:
  - late deliveries → a fixed battery of tests in the analysis engine (baseline, stages with counterfactuals and
    Shapley shares, where it sits, events against the prior trend, volume, data quality, waiting for stock);
  - stock-outs, backlogs, orders at risk → the supply engine (inbound orders and ports, demand, reorder policy,
    duplicates, volume);
  - anything else → an agent loop (Planner, SQL Investigators, Evaluator).
- Coded verdicts → actions sized by code (and the Optiagent optimizer) → a brief whose every number is checked →
  memory and cause graph written back.
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

## 8. Meterless: memory with provenance, and a cause graph

- H-MEM stores every confirmed and ruled-out finding with provenance (run, agent, query, document); new
  investigations recall related findings as hints, never as evidence.
- Recall runs on NVIDIA Nemotron embeddings (one vendored file gained a provider hook; conformance 13/13 with it off
  and on). Reworded questions find the right past finding 5 times in 6, against 2 in 6 with the mock.
- The World Model keeps the cause-effect relations across runs.
- Screenshot: `docs/img/memory.png` (the Memory page, or a run's Memory tab with the cause graph).

## 9. What's unique

- Numbers never come from a model. The analysis engine computes; coded rules decide what counts as a cause; a numbers
  check marks anything unsupported (on a probe of 1,000 invented numbers, 3% got through).
- Red herrings are tested, not argued: dated events against the prior trend, a stage must move to count as a cause.
- Actions are sized from data and proven where possible: the stock transfer is Optiagent's proven-optimal plan, with
  a duality certificate.
- Honest evaluation: held-out questions scored on their first run, including a failure (KH2), and the fix disclosed.

## 10. Results

- Real data (Olist): 6/6 questions found, three passes, 62/62 computed findings identical.
- Simulated company (Kaveri): 5/5 planted causes found with the right action.
- Held-out questions: 5 asked, 4 passed on their first run, 1 failed (a red herring) and led to a rule change.
- Cost: 5 to 9 model calls per real-data question (the earlier pure-agent design used 50 to 70).
- [Update these lines from the README's final results table before building the slide.]

## 11. The prescriptive loop (Kaveri)

- Pune orders at risk: Voltix purchase orders stuck behind the Chennai dock strike.
- Fix: a stock transfer from Mumbai plus air freight, proven optimal, protecting 62 orders for INR 133,590.
- A manager approves; the Action Executor prepares the transfer order, a coverage check and the supplier email.
- Screenshot: `docs/img/kaveri-transfer.png` (the brief's actions table or the deliverables).

## 12. What's next

- Zetaris Cloud instead of the local bundle; more source types (carrier APIs, ticketing).
- Meterless Nightshift to run the Document Analyst on a local GPU, so contracts and email never leave the building.
- More computed engines (returns, quality, cost-to-serve); fewer questions on the slower agent loop.
- Validate the thresholds against outside data; more held-out questions from people other than the author.

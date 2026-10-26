# Submission form text (paste-ready)

## Track

Track 2: The Agent That Can Explain Why

## Project name

WhyChain

## One-paragraph description

WhyChain is an agent that explains why something went wrong in a supply chain. Ask in plain English, for example
"Why did late deliveries jump to 19% for orders placed in March 2018?", and it investigates across the order system,
seller, payment and review exports, an external events feed and news briefs, federated by Zetaris. It returns each
cause with its share of the problem, where the problem sits, the red herrings it ruled out and why, quotes from the
written record, and actions sized from the data, with the risky ones held for a manager's approval. Numbers and
verdicts come from code, not from the model: an analysis engine runs a fixed battery of tests (counterfactuals with
Shapley shares, event tests against the prior trend), coded rules decide what counts as a cause, and every number in
the brief is checked against the cell it came from. A preliminary answer computed from the data appears in seconds;
the brief opens with three lines for a manager (what happened, why, what to do, in money) and a why chain drawn from
the Meterless World Model. Meterless Scout Intent routes every question (and refuses injected instructions), NVIDIA
Nemotron reads and quotes the documents and writes the short answer, and Meterless H-MEM, with Nemotron embeddings,
remembers findings with provenance. On real Brazilian e-commerce data it finds that March 2018's 19.0% late rate
(against a normal 5.6%) came from slower carrier transit (72.7% of the rise) and shorter promises (26.7%), rules out
the postal strike because lateness was already high before it began, and puts a value on it: the 1,328 late orders
were worth R$ 187,138 and were rated 2.1 stars against 4.2 for on-time ones.

## Sponsor technologies

- Zetaris: the only path to company data; Postgres and file exports federated in one SQL endpoint (local platform).
- Meterless: three engines. Scout Intent routes every question, guards against prompt injection and signs an execution
  contract (WhyChain's implementation of the spec, passing the spec's own eval harness); H-MEM memory with provenance
  (on Nemotron embeddings); the World Model holds each run's why chain, which the brief draws. Plus Nightshift:
  tick "Private documents" and the written record is read on the laptop's own GPU (NVIDIA Nemotron 3 Nano 4B via `meterless/local`),
  with Nightshift's signed receipt checked on every call (local-only, egress none).
- NVIDIA: Nemotron 3.5 Super and Nemotron 3 Ultra for the agents; Nemotron 3 Embed for memory.
- Development tools: VS Code (Cursor is optional under the updated onboarding guide).

## Pre-existing work (declaration)

Built before the build window (21 Oct 23:04 to 22 Oct about 03:05 IST) and imported in the first commits at
22 Oct 13:44 IST: the simulated distributor and its documents, the Olist data loader and the 16 researched events,
the Zetaris REST client (rewritten in the window for the local platform) and the local DuckDB harness, the first
agent graph (memory, data scout, planner, investigators, document analyst, evaluator, risk and action, reporter,
action executor), the NVIDIA NIM client, the first stock-transfer optimizer (its data layer and `optimize`
action; the solver it called has since been replaced by `whychain/lp.py`, written in the window), the Meterless
bridge service, the FastAPI server
and the React app, and the first evals and tests.

Third-party components: the Meterless H-MEM and World Model reference engines (Apache-2.0; one file changed in the window, to add
an embedding provider hook); open-source libraries; and the Olist dataset (CC BY-NC-SA 4.0).

Written in the window: the analysis engine and supply engine, the stock-transfer LP solver and its duality
certificate (`whychain/lp.py`), the numbers check, the connection to Zetaris, the
Nemotron embeddings for H-MEM, the Scout Intent implementation, the value at stake, the preliminary answer, the why
chain, the evals with held-out questions, and everything after the first commits. Full
details: `git log`.

## Links

- Repository: https://github.com/Dakshhhhh-ops/whychain
- Demo video: [FOR THE AUTHOR: video link]
- Slide deck: [FOR THE AUTHOR: deck link]

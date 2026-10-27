# WhyChain test results

How the results are scored: a case passes "root cause found" when the expected terms appear, as whole words, in a cause the Evaluator confirmed or in the headline. Keyword scoring is crude; read the briefs too. Regenerate with `python -m evals.run_evals` (or `--dataset olist|kaveri`).

### Final pass: all 18 questions at one commit, through Zetaris (23 Oct 23:04 to 23:27 IST, commit `96815d9`)

**Results: root cause found in 18/18; red herring avoided in 15/15 cases that define one; right action in 8/8 cases that have one; held-out questions 4/4 (H1, HO1, KH1, KH4). Preliminary answer: median 3.5 s after the start (1 to 42 s). Meterless Scout routed 17 of 18 questions itself and left 1 to the word rules; its route equalled the rules' every time.**

This pass is on the code as submitted: everything added on 23 Oct (the value at stake, the manager summary, the preliminary answer, the why chain, Meterless Scout Intent, Ask why next, the open audit fixes) is in `96815d9`. Two later commits change only how the app displays a result, not what is computed (`c9cf194`: the why chain reads causes as the World Model stores them; `1ef97d6`: reais grouped like the brief text). Every question ran once, one at a time, every query through Zetaris (no failed call in any trace, no uncommitted change in the code), each dataset on an empty memory.

| Case | Held out | Root cause found | Red herring avoided | Right action | Routed by | Preliminary answer | Full brief | Headline (first sentence) | Run |
|---|---|---|---|---|---|---|---|---|---|
| O1 March 2018 spike |  | ✅ 2/2 | ✅ | — | Scout (medium) | 42 s | 70 s | 1,328 of 7,003 delivered orders placed March 2018 were late (19.0%), against 5.6% for a normal baseline. | `20261023-231833-6735` |
| O2 Black Friday 2017 |  | ✅ 2/2 | ✅ | — | Scout (high) | 4 s | 45 s | Yes: Black Friday 2017 delayed deliveries. For parcels handed over during it, carrier transit rose 1.9 days and the late rate 5.1 points against the trend before it. | `20261023-231942-7fce` |
| O3 Truckers' strike 2018 |  | ✅ 2/2 | ✅ | — | Scout (medium) | 3 s | 65 s | Yes: Truckers' strike delayed deliveries. For parcels handed over during it, carrier transit rose 2.8 days and the late rate 5.9 points against the trend before it. | `20261023-232027-2a5f` |
| H1 Carnival 2018 | yes | ✅ 1/1 | ✅ | — | Scout (high) | 3 s | 22 s | No: Carnival holiday 2018 did not cause the delays. They were already there: carrier transit was 11.1 days just before it, against a normal 8.7. | `20261023-232132-5f36` |
| H2 Northeast delays |  | ✅ 2/2 | ✅ | — | Scout (medium) | 4 s | 30 s | 1,150 of 9,044 delivered orders to Northeast were late (12.7%), against 6.2% for all other orders in the same period. | `20261023-232155-c440` |
| H3 Rio security |  | ✅ 2/2 | — | — | Scout (medium) | 4 s | 88 s | The data can't test record cargo theft in Rio de Janeiro state (2017) directly. | `20261023-232224-3233` |
| HO1 February 2018 | yes | ✅ 2/2 | ✅ | — | Scout (high) | 4 s | 135 s | 926 of 6,555 delivered orders placed February 2018 were late (14.1%), against 5.1% for a normal baseline. | `20261023-232352-a33d` |
| HO2 December 2017 |  | ✅ 1/1 | ✅ | — | Scout (medium) | 4 s | 70 s | 411 of 5,513 delivered orders placed December 2017 were late (7.5%), against 3.5% for a normal baseline. | `20261023-232607-32c1` |
| A Pune orders at risk |  | ✅ 2/2 | ✅ | ✅ | Scout (medium) | 23 s | 106 s | 23 of the 29 open orders at the Pune DC due in the coming week have a line on backorder (79.3%). | `20261023-230420-4dd8` |
| B Nashik late |  | ✅ 2/2 | ✅ | ✅ | Scout (medium) | 4 s | 43 s | 64 of 70 delivered orders to Nashik placed since September 2026 were late (91.4%), against 15.1% for a normal baseline. | `20261023-230605-33ab` |
| C Mumbai earbuds |  | ✅ 2/2 | ✅ | ✅ | rules (Scout not confident) | 1 s | 91 s | 33 of the 101 orders for earbuds placed at the Mumbai DC in mid October 2026 have a line on backorder (32.7%). | `20261023-230648-df0b` |
| D Chennai backlog |  | ✅ 1/1 | ✅ | ✅ | Scout (high) | 1 s | 97 s | 47 of the 81 open orders created at the Chennai DC this week have a line on backorder (58.0%). | `20261023-230819-e78b` |
| E On-time drop |  | ✅ 3/3 | ✅ | ✅ | Scout (medium) | 2 s | 108 s | 232 of 1,206 delivered orders placed September to October 2026 were late (19.2%), against 7.7% for the period you named. | `20261023-230956-15d8` |
| KH1 Chennai power banks | yes | ✅ 2/2 | ✅ | ✅ | Scout (medium) | 7 s | 43 s | 31 of the 89 orders for power banks placed at the Chennai DC in the last 14 days have a line on backorder (34.8%). | `20261023-231144-f381` |
| KH2 Hyderabad September |  | ✅ 2/2 | ✅ | ✅ | Scout (medium) | 1 s | 80 s | 17 of 98 delivered orders to Hyderabad placed September 2026 were late (17.3%), against 3.3% for a normal baseline. | `20261023-231227-b0f1` |
| KH3 Pune October (false premise) |  | ✅ 1/1 | — | — | Scout (medium) | 1 s | 28 s | It did not rise: 2 of 45 delivered orders to Pune placed October 2026 were late (4.4%), against 12.6% for a normal baseline. There is no rise to explain. | `20261023-231347-8ba1` |
| KH4 Surat September | yes | ✅ 3/3 | ✅ | ✅ | Scout (medium) | 1 s | 105 s | 19 of 87 delivered orders to Surat placed September 2026 were late (21.8%), against 6.7% for a normal baseline. | `20261023-231415-0e14` |
| KH5 Mumbai chargers (false premise) |  | ✅ 1/1 | — | — | Scout (high) | 1 s | 139 s | There is no shortage to explain: 0 of the 59 orders for chargers placed at the Mumbai DC in the last 14 days have a line on backorder (0.0%). | `20261023-231600-7098` |

- **Preliminary answer.** The time from the start of the run to the code-written answer (summary, causes, sized actions). The two slow ones (23 and 42 s) are each dataset's first question, while the process pulled its tables through Zetaris; the app's warm-up (`/api/warm`) does that before a demo. The full brief, with the written record and the short model-written answer, came 22 to 139 s after the start.
- **Against the previous final pass** (22 Oct, `bdf4e16`), every confirmed cause and its share are the same in all 18 questions. What changed is what the 23 Oct fixes were meant to change: the event tests' context line uses a "normal" that leaves out abnormal weeks (truckers' strike 11.8 → 8.5 days, the March 2018 Correios strike 9.6 → 8.7, Black Friday 8.5 → 8.7; the event verdicts are the same); when the late rate did not rise (O3, KH3), "where it sits" is not applicable instead of listing zero or negative shares; HO2 no longer counts Black Friday (23 to 27 November) as a cause of December's lateness; comparison questions speak of a gap; and every late-delivery question has a new finding, the value at stake. Of the 163 findings both passes share, 80 of 80 on Kaveri and 76 of 83 on Olist have the same numbers; the 7 differences are the ones listed here.
- **Held-out record.** KH1, KH4, H1 and HO1 are still held out and pass. HO2 became a development case on 23 Oct (its brief led to the event-before-the-period rule); it passed on its first, held-out run (21:24 IST, 22 Oct). All first runs so far: 10 questions, 9 passed, 1 failed (KH2).
- **Time and cost.** 5 to 9 model calls per question.

Earlier passes, kept for the record (each states its time, commit or architecture, and harness):


### Previous final pass (before the 23 Oct features): all 18 questions through Zetaris (22 Oct 21:35 to 22:19 IST, commit `bdf4e16`)

**Results: root cause found in 18/18; red herring avoided in 15/15 cases that define one; right action in 8/8 cases that have one; held-out questions 5/5 (H1, HO1, HO2, KH1, KH4).**

Every question ran once, one at a time, with every query through Zetaris (`harness: zetaris` in each run's `result.json`; no failed Zetaris call in any trace). Each dataset's questions started on an empty memory (Meterless H-MEM with Nemotron embeddings). The analysis code is the same as at the code freeze (`6cdc35e`, 20:39 IST); `bdf4e16` differs from it only in the Zetaris harness and its tests (below).

| Case | Held out | Root cause found | Red herring avoided | Right action | Model calls | Time | Headline (first sentence) | Run |
|---|---|---|---|---|---|---|---|---|
| O1 March 2018 spike |  | ✅ 2/2 | ✅ | — | 7 | 79 s | 1,328 of 7,003 delivered orders placed March 2018 were late (19.0%), against 5.6% for a normal baseline. | `20261022-215935-c817` |
| O2 Black Friday 2017 |  | ✅ 2/2 | ✅ | — | 7 | 145 s | Yes: Black Friday 2017 delayed deliveries. For parcels handed over during it, carrier transit rose 1.9 days and the late rate 5.1 points against the trend before it. | `20261022-220054-0a6c` |
| O3 Truckers' strike 2018 |  | ✅ 2/2 | ✅ | — | 8 | 98 s | Yes: Truckers' strike delayed deliveries. For parcels handed over during it, carrier transit rose 2.8 days and the late rate 5.9 points against the trend before it. | `20261022-220319-bab5` |
| H1 Carnival 2018 | yes | ✅ 1/1 | ✅ | — | 6 | 38 s | No: Carnival holiday 2018 did not cause the delays. They were already there: carrier transit was 11.1 days just before it, against a normal 8.7. | `20261022-220457-dcbc` |
| H2 Northeast delays |  | ✅ 2/2 | ✅ | — | 7 | 182 s | 1,150 of 9,044 delivered orders to Northeast were late (12.7%), against 6.2% for all other orders in the same period. | `20261022-220535-9e0f` |
| H3 Rio security |  | ✅ 2/2 | — | — | 7 | 240 s | The data can't test record cargo theft in Rio de Janeiro state (2017) directly. | `20261022-220836-5fd2` |
| HO1 February 2018 | yes | ✅ 2/2 | ✅ | — | 8 | 214 s | 926 of 6,555 delivered orders placed February 2018 were late (14.1%), against 5.1% for a normal baseline. | `20261022-221236-a838` |
| HO2 December 2017 | yes | ✅ 1/1 | ✅ | — | 7 | 188 s | 411 of 5,513 delivered orders placed December 2017 were late (7.5%), against 3.5% for a normal baseline. | `20261022-221610-770c` |
| A Pune orders at risk |  | ✅ 2/2 | ✅ | ✅ | 7 | 198 s | 23 of the 29 open orders at the Pune DC due in the coming week have a line on backorder (79.3%). | `20261022-213507-bec5` |
| B Nashik late |  | ✅ 2/2 | ✅ | ✅ | 7 | 192 s | 64 of 70 delivered orders to Nashik placed since September 2026 were late (91.4%), against 15.1% for a normal baseline. | `20261022-213825-b030` |
| C Mumbai earbuds |  | ✅ 2/2 | ✅ | ✅ | 7 | 132 s | 33 of the 101 orders for earbuds placed at the Mumbai DC in mid October 2026 have a line on backorder (32.7%). | `20261022-214137-5094` |
| D Chennai backlog |  | ✅ 1/1 | ✅ | ✅ | 7 | 145 s | 47 of the 81 open orders created at the Chennai DC this week have a line on backorder (58.0%). | `20261022-214349-1802` |
| E On-time drop |  | ✅ 3/3 | ✅ | ✅ | 7 | 97 s | 232 of 1,206 delivered orders placed September to October 2026 were late (19.2%), against 7.7% for the period you named. | `20261022-214614-ac93` |
| KH1 Chennai power banks | yes | ✅ 2/2 | ✅ | ✅ | 6 | 179 s | 31 of the 89 orders for power banks placed at the Chennai DC in the last 14 days have a line on backorder (34.8%). | `20261022-214751-6fe1` |
| KH2 Hyderabad September |  | ✅ 2/2 | ✅ | ✅ | 7 | 141 s | 17 of 98 delivered orders to Hyderabad placed September 2026 were late (17.3%), against 3.3% for a normal baseline. | `20261022-215050-78e6` |
| KH3 Pune October (false premise) |  | ✅ 1/1 | — | — | 6 | 114 s | It did not rise: 2 of 45 delivered orders to Pune placed October 2026 were late (4.4%), against 12.6% for a normal baseline. There is no rise to explain. | `20261022-215312-f738` |
| KH4 Surat September | yes | ✅ 3/3 | ✅ | ✅ | 9 | 199 s | 19 of 87 delivered orders to Surat placed September 2026 were late (21.8%), against 6.7% for a normal baseline. | `20261022-215506-0449` |
| KH5 Mumbai chargers (false premise) |  | ✅ 1/1 | — | — | 6 | 62 s | There is no shortage to explain: 0 of the 59 orders for chargers placed at the Mumbai DC in the last 14 days have a line on backorder (0.0%). | `20261022-215825-ee38` |

- **Why `bdf4e16` and not the freeze commit.** The first final pass, at `6cdc35e` (20:39 to 21:26 IST), exposed a harness bug: the local Zetaris platform issues 15-minute login tokens, the harness kept them for 30 minutes, and the platform answers an expired token with HTTP 400 rather than 401, so nothing logged in again. From 15 minutes into each eval process every query failed: KH4, KH5, HO1 and HO2 fell back to the local DuckDB copy (they passed there), and the Document Analyst's queries failed part-way through KH3 and H3. `1750530` first put this down to load; `bdf4e16` found and fixed the cause (renew before expiry; log in again on an auth failure; tested in `tests/test_zetaris.py` and against the live platform). The whole pass was then run again. Its runs, and the first attempt's, are in `runs/`.
- **Held-out questions.** Ground truth was computed with SQL and written into `evals/cases.yaml` before anything ran them. HO1 and HO2 (added 20:28 IST) were first run at 21:21 and 21:24 IST in the first attempt, on the DuckDB copy because of the token bug: both passed. The table shows their second run. All first runs of held-out questions so far: 10 questions, 9 passed (H1 16:01, H2 16:15, H3 16:16, KH1 19:11, KH3 19:13, KH4 19:30, KH5 19:32, HO1 21:21, HO2 21:24 IST) and 1 failed (KH2 19:12: it blamed transit that had not moved). KH2, KH3, H2, H3 and KH5 later led to code changes and are development cases now; that is why the table marks only five questions as held out.
- **Reproducibility.** The computed findings (each check's verdict, cause share, and the numbers in its claim) are identical to earlier passes: Kaveri 80/80 against the 19:40 IST pass (`32ced57`) and against the first attempt at `6cdc35e`; Olist 62/62 against the 20:00 IST pass (`32ced57`) and against the first attempt; HO1 and HO2 21/21 against their first runs, which went to the DuckDB copy (so the two harnesses agree too). Against the four earlier Olist passes (16:10 and 16:17 local; 18:03 and 19:15 through Zetaris), all 62 verdicts, causes and shares are the same; some claims there repeat a number that the current wording drops ("2.0 against 2.0" became "the same as"). What changed by design since those passes: the event questions (O2, O3, H1) answer "Yes" or "No" with the size in the headline, H3 says the condition can't be tested directly, KH3 says "It did not rise", KH5 says there is no shortage, KH2 no longer blames transit, and shares over 100% say why. The short model-written answer and the Document Analyst's reading vary from run to run.
- **What reading the briefs still finds (not fixed: the code was frozen).** HO2 lists Black Friday (23 to 27 November) among the causes of December's lateness, without showing that its backlog carried into December. When the late rate did not rise (O3), "Where it happened" still lists segments with zero or negative shares. The event test's "normal" transit for the truckers' strike (11.8 days) includes the February to March 2018 spike; the verdict compares with the trend just before the event, so it is unaffected. Comparison questions (H2, H3) say "of the rise" for what is a gap between two groups.
- **Time and cost.** Olist 38 to 240 s, Kaveri 62 to 199 s per question; 6 to 9 model calls per question.

### Real data (Olist), analysis-engine architecture: two independent passes, 22 Oct 16:10 to 16:25 IST

Each pass ran all six questions on an empty memory, one after the other (`evals/run_evals.py --dataset olist`). O1 to O3 are development cases; H1 to H3 are held out (added after the last prompt change, ground truth computed with SQL before any agent ran them).

| Case | | Pass 1 | Pass 2 | Model calls | Time | What the brief says |
|---|---|---|---|---|---|---|
| O1 March 2018 spike | dev | ✅ 2/2, no red herring | ✅ 2/2, no red herring | 7 / 6 | 52 s / 55 s | 19.0% late vs a normal 5.6%. Carrier transit 72.7% and shorter promises 26.7% of the rise; concentrated in RJ. Strike, Carnival, Rio storm, volume and data errors ruled out. |
| O2 Black Friday 2017 | dev | ✅ 2/2, no red herring | ✅ 2/2, no red herring | 7 / 6 | 77 s / 25 s | Yes: the effect starts with Black Friday. Volume about 2.9x the weeks before, and transit slowed with it. |
| O3 Truckers' strike 2018 | dev | ✅ 2/2, no red herring | ✅ 2/2, no red herring | 7 / 6 | 48 s / 111 s | Yes: transit rose against the prior trend for parcels handed over during the strike (the old architecture said "no measurable delay"). |
| H1 Carnival 2018 | held out | ✅ 1/1, no red herring | ✅ 1/1, no red herring | 5 / 6 | 68 s / 29 s | No: transit was already raised before Carnival and didn't jump with it. |
| H2 Northeast delays | held out | ✅ 2/2, no red herring | ✅ 2/2, no red herring | 9 / 9 | 90 s / 123 s | Carrier transit is twice as long (median 14 vs 7 days); almost all orders ship from another state; longer promises offset most of it. |
| H3 Rio security | held out | ✅ 2/2 (no red herring defined) | ✅ 2/2 | 6 / 7 | 44 s / 140 s | RJ is late more often than states at a similar distance, and transit explains it; the data can't prove theft is the cause. |

- **Reproducibility:** across the two passes, **all 62 computed findings were identical** (verdict, cause share and claim), and so were all six headlines. Only the Document Analyst's reading and the short model-written answer vary.
- **Models used:** Nemotron 3.5 Super, with Nemotron 3 Ultra taking over when NVIDIA rate-limited it (column "Models used" in `evals/results.md`). The computed findings don't depend on which model answered.
- **Scorer change, disclosed:** pass 1 was first scored with the earlier scorer, which counted only confirmed *causes*. O1 and H2 then scored 1/2, because "where the rise sits" (RJ / interstate shipping) is reported as a measurement. The scorer now also counts confirmed measurements; red herrings are still judged on causes only. The table uses the current scorer for both passes.
- **Earlier architecture, for comparison:** 8 to 10 minutes and 50 to 70 model calls per question. The strike verdict on O1 flipped between runs, and O3 was answered wrongly.
- Both passes above used the local DuckDB harness. The third pass, below, ran through Zetaris.

### Real data (Olist) through Zetaris: third pass, 22 Oct 18:03 to 18:19 IST

The same six questions and the same code, with `WHYCHAIN_HARNESS=zetaris`: every query, including the engine's fact query, went to the local Zetaris platform (`harness: zetaris` in each run's `result.json`), on an empty memory.

| Case | | Result | Model calls | Time |
|---|---|---|---|---|
| O1 March 2018 spike | dev | ✅ 2/2, no red herring | 7 | 241 s |
| O2 Black Friday 2017 | dev | ✅ 2/2, no red herring | 8 | 154 s |
| O3 Truckers' strike 2018 | dev | ✅ 2/2, no red herring | 7 | 99 s |
| H1 Carnival 2018 | held out | ✅ 1/1, no red herring | 6 | 150 s |
| H2 Northeast delays | held out | ✅ 2/2, no red herring | 8 | 85 s |
| H3 Rio security | held out | ✅ 2/2 (no red herring defined) | 7 | 139 s |

- **Same answers as the local passes:** all 62 computed findings (verdict, cause share and claim) and all six headlines are identical to both earlier passes.
- **Slower:** 85 to 241 s per question, against 25 to 140 s locally. Zetaris queries take 0.2 to 1.3 s each. O1 also rebuilt the data map once (about 80 s: Zetaris lists JSON columns in a different order, so the cached map didn't match), some NVIDIA calls timed out and were retried, and the test suite and comparison scripts were running on the same laptop during O1 to O3.
- **Models used:** Nemotron 3.5 Super for 71 to 100% of calls per question, Nemotron 3 Ultra for the rest.

### Simulated company (Kaveri) through Zetaris, with the supply engine: 22 Oct 18:32 to 18:39 IST

The five Kaveri questions on commit `f10d80d`, every query through Zetaris, on an empty memory. A, C and D run on the supply engine; B and E are late-delivery questions and run on the analysis engine.

| Case | Path | Root cause found | Red herring avoided | Right action | Model calls | Time | What the brief says |
|---|---|---|---|---|---|---|---|
| A Pune orders at risk | supply | ✅ 2/2 | ✅ | ✅ | 7 | 100 s | 23 of 29 orders due next week have a backordered line. Late Voltix (S-14) purchase orders, held at Chennai by the dock strike, are involved in 58.4% of the short units; demand spikes and stale reorder points also count. Actions: a proven-optimal transfer from Mumbai (4,414 units by truck, 621 by air, INR 125,921), chase the Voltix orders, raise reorder points. |
| B Nashik late | engine | ✅ 2/2 | ✅ | ✅ | 6 | 66 s | 91.4% late against a normal 15.1%; carrier transit explains it, concentrated in carrier C02. Actions include switching from C02 to C01 or C03. |
| C Mumbai earbuds | supply | ✅ 2/2 | ✅ | ✅ | 7 | 40 s | A demand spike on promotion DIWALI26 (up to 7.5x the baseline) and reorder points last reviewed in August. Late supply, the port strike and duplicates (4.4%) ruled out. Action: raise the reorder points. |
| D Chennai backlog | supply | ✅ 1/1 | ✅ | ✅ | 6 | 103 s | 23 of 81 orders are extra EDI copies of earlier orders, ordering units equal to 72.7% of the shortage; genuine order volume did not rise. Action: cancel the copies and fix the EDI connector (needs approval). |
| E On-time drop | engine | ✅ 2/3 | ✅ | ✅ | 8 | 86 s | Carrier transit (46.6% of the rise) and warehouse dispatch (21.9%), concentrated in Nashik. The documents name the Chennai port strike; this run's Document Analyst didn't mention the Diwali promotion, so the third group was missed (the engine finds that dispatch slowed, not why). |

- **Against the earlier pass (below):** the three supply questions went from 0 of 3 found, 11 to 16 minutes and 49 to 86 model calls each, to 3 of 3 found in under two minutes with 6 or 7 calls.
- **Disclosed:** this pass came after fixes made from reading an earlier supply-engine pass the same evening (18:18 to 18:27, also 5/5 found). That pass named 3 duplicate orders as a cause of the earbud stock-out, because duplicates had their own threshold; they are now judged like every other check (`f10d80d`). D's headline was reworded afterwards to say exactly what the 72.7% measures.
- All Kaveri ground truth was planted by the author, so these are development cases, not held-out ones.

### Held-out questions for the simulated company, scored on their first run

Three questions added at 19:03 IST (commit `6346120`), with ground truth computed by SQL and written into `evals/cases.yaml` before any engine or agent ran them. Each was run once, from 19:11 to 19:15 IST, through Zetaris on commit `44b257b`, with the memory empty at the start of the pass. A case passes when it finds the cause, avoids the red herring and proposes the right action, where the case defines them.

| Case | Result | Cause found | Red herring avoided | Right action | Run |
|---|---|---|---|---|---|
| KH1 Power banks out of stock at the Chennai DC | ✅ pass | 2/2: Voltix (S-14) purchase orders, held at the Chennai port | ✅ | ✅ | `20261022-191118-0024` |
| KH2 Hyderabad deliveries later in September | ❌ **fail** | 2/2: dispatch slowed, orders waiting for stock | ❌ it also confirmed "carrier transit got slower" (14.6% of the rise), though transit was flat (typically 2.0 against 2.0 days) | ✅ | `20261022-191234-0dc3` |
| KH3 Pune "rise" in October (a false premise) | ✅ pass | 1/1: no cause confirmed (the late rate fell) | — (nothing was blamed) | — | `20261022-191325-32b1` |

Both KH2 and KH3 then led to code changes (commit `4ae49f7`): a stage must itself have moved to count as a cause (KH2), and a headline that says "It did not rise" when the late rate fell (KH3). **KH2 and KH3 are therefore development cases from that commit on.**

Two replacement held-out questions were added at 19:28 IST (commit `23fadb2`, ground truth from SQL first) and run once, at 19:30 to 19:34 IST, through Zetaris on that commit, on an empty memory (Nemotron embeddings):

| Case | Result | Cause found | Red herring avoided | Right action | Run |
|---|---|---|---|---|---|
| KH4 Surat deliveries late in September | ✅ pass | 3/3: dispatch slowed, orders waiting for stock, mostly Voltix (S-14) | ✅ (transit flat, promises longer: neither blamed) | ✅ | `20261022-193027-a920` |
| KH5 "Charger shortage" at the Mumbai DC (a false premise: no charger backorders) | ✅ pass | 1/1: no cause confirmed | — (nothing was blamed) | — | `20261022-193243-c38c` |

Held-out record at that point (19:36 IST): 5 questions, 4 passed on their first run (KH1, KH3, KH4, KH5), 1 failed (KH2). (The record since, and KH5's later move to the development cases, are in the final pass above.) These are still the author's planted mechanisms in new scopes and wordings, so they test generality, not independence.

### Before the supply engine: Kaveri, one pass, 22 Oct 16:25 to 17:08 IST (local harness)

| Case | Path | Root cause found | Right action | Model calls | Time | Notes |
|---|---|---|---|---|---|---|
| A Pune orders at risk | agent loop | ❌ 1/2 | ❌ | 49 | 14 min | Found the delayed inbound supply, but not the supplier (Voltix, S-14). It never proposed the stock transfer from Mumbai. |
| B Nashik late | engine | ✅ 2/2 | ❌ | 8 | 69 s | Transit explains 106% of the rise, concentrated in carrier C02. The contract change comes from the verified documents. The switch-carrier action was added after this run. |
| C Mumbai earbuds | agent loop | ❌ crashed | ❌ | 60 | 11 min | A bug in the numbers check (a key number pointing past a truncated result), since fixed and tested. |
| D Chennai backlog | agent loop | ❌ 0/1 | ❌ | 86 | 16 min | Missed the duplicate EDI orders. Earlier runs found them, but the prompts then contained the hint "duplicates"; with the hints removed, it doesn't. |
| E On-time drop | engine | ✅ 3/3 | ✅ | 9 | 68 s | Transit (46.6%) and warehouse dispatch; the documents name the port strike and the promotion. |

The agent loop, then used for stock, backlog and risk questions, was slow (11 to 16 minutes, mostly NVIDIA rate limits) and found the planted cause in none of its three cases. That is why those questions moved onto computed checks (the supply engine, above).

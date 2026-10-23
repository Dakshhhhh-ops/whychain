# WhyChain demo video: script and shot list

Target length 3:15 (rules: 1 to 4 minutes). Screen recording at 1600×1000 or larger, browser zoom 100%, app at
`http://localhost:8000`. **[Nemotron]** marks where NVIDIA Nemotron is doing the work on screen.

A preliminary answer, computed from the data, appears within seconds of starting a run (median 3.5 s in the final pass, after the warm-up);
the full brief takes 1.5 to 4 minutes through Zetaris. Show the preliminary answer live, then cut ahead to the brief (say so
on screen: "about two minutes later"). Never present a replay as live: a replay is labelled "Replaying" in the app.

## Before recording (by hand)

1. Docker Desktop running; `ZETARIS_DIR` set in `.env` (the Zetaris bundle folder); run `start.cmd`. It starts
   Docker if needed, Zetaris, Postgres, the Meterless bridge and the web app, then runs `scripts/warmup.py`, which
   waits for Zetaris and runs the slow first queries. Wait for "Warm-up done."
2. In the app, check the left panel: **Zetaris: federated**, **Meterless memory: Nemotron embeddings**, Nemotron
   models green.
3. Run the two demo questions once before recording, so there are finished runs to fall back on and so memory holds
   an earlier March 2018 investigation (needed for shot 7):
   - Olist: "Why did late deliveries jump to 19% for orders placed in March 2018?"
   - Kaveri: "Why are so many Pune orders at risk of missing their promised date next week?"
   Note both run ids. Fallback if NVIDIA is slow during the take: open `http://localhost:8000/runs/<run id>?replay=1`
   (plays the saved run back at real speed, badged "Replaying"; say "here's the run from earlier").
4. Close other browser tabs and notifications. Have the Zetaris UI (`http://localhost:3000`) logged in, on its SQL
   editor, in a second tab.

## Shots

| # | Time | Screen | Action | Say |
|---|---|---|---|---|
| 1 | 0:00–0:15 | Control tower, Olist dataset | Hold on the chart with event markers | "When deliveries go late, an operations manager needs to know *why*: which stage broke, where, and whether the strike in the news is really to blame. The answer is spread across the order system, seller exports, reviews and news. WhyChain investigates it." |
| 2 | 0:15–0:35 | Control tower | Point at the 19% March 2018 spike, then click **Investigate why** on that problem | "Real Brazilian e-commerce data, 99,000 orders. In March 2018, 19% of deliveries were late. And there was a Correios strike that month. Let's ask why." |
| 3 | 0:35–1:00 | Investigation, **Board** tab, live | Show the activity line "Meterless Scout: delivery.explain → the analysis engine · signed contract" and the **Zetaris** badge; within about 15 s the **Preliminary answer** card appears at the top (what happened in money, why, what to do) | "**Meterless Scout** reads the question first: it routes it, checks it for injected instructions and signs a contract. Every query goes through **Zetaris**, which federates the order database, seller exports, reviews and news without copying them. The numbers come from code, so the answer is here in seconds: 1,328 late orders worth 187,000 reais, rated 2.1 stars against 4.2. **[Nemotron]** is still reading the news and will write the short answer." Cut: "two minutes later" (optional). |
| 4 | 1:00–1:35 | **Brief** tab | Read **In short**; point at the **why chain** under it (from the Meterless World Model; the strike crossed out); click a citation like `[T4.A1]` to open its evidence (table + SQL + "Zetaris"); unfold **How we know** for one line | "19% late against a normal 5.6%. Carrier transit explains 72.7% of the rise, shorter promises 26.7%. The strike? Ruled out: lateness was already high before it began. Every number is checked against the cell it came from, and the medians and counterfactuals behind each line are one click away. **[Nemotron]** writes only the short answer, from those checked facts." |
| 4b | (optional, +20 s) | Brief, **Ask why next** | Click "Why were deliveries to Rio de Janeiro late for orders placed March 2018?"; the preliminary answer appears in seconds | "The rise sits in Rio, so the next why is one click: 34.5% late there against a normal 16.3%, worth 35,000 reais, rated 1.6 stars. Each answer opens the next why." |
| 5 | 1:35–1:55 | Brief, **Recommended actions** | Point at **Value at stake** on the transit action; open the promise-buffer action, click **Approve** | "Each action is sized from the data and says what's at stake: getting transit back to normal in three states covers 593 of these orders, 83,600 reais of them; promise buffers per state, RJ plus 9 days, cover 255. Changing customer promises needs approval, so it waits for me." |
| 6 | 1:55–2:35 | Switch dataset to **Kaveri**; run **Pune orders at risk** live | The preliminary answer appears in seconds with the transfer; then the brief: Voltix purchase orders held at the Chennai port strike; the transfer action with **Protects**, **Value at stake** and cost; **Approve**; cut ahead about two to three minutes; show the deliverables (a per-SKU transfer order and the supplier email draft; the Action Executor is an agent, so check them in the rehearsal run) | "A simulated distributor. Pune orders at risk: Voltix purchase orders are stuck behind a dock strike at Chennai. The fix is a stock transfer from Mumbai, proven optimal by Optiagent with a duality certificate: 133,590 rupees of transport to protect 62 orders worth 2.28 crore. Approve it, and the Action Executor prepares the transfer order and the supplier email." |
| 7 | 2:35–3:00 | Olist again: run the March 2018 question a second time (or a reworded one); open its **Memory** tab | Show "Recalled N memories" in the activity, the recalled findings with provenance, then the **Cause graph** card | "**Meterless** gives it memory. H-MEM recalled what the earlier investigation found, with provenance: which run, which query. **[Nemotron]** embeddings match a reworded question to the right past finding. The World Model keeps the cause graph across runs." |
| 8 | 3:00–3:15 | **Data sources** page ("Federated by Zetaris"), then the Zetaris tab's SQL editor with a query over `oms.orders` joined to `sellers.order_items` | Show the registered sources, run the query | "Six systems, one federated query layer in Zetaris; agents write Spark SQL against it. WhyChain: an agent that explains why, with the evidence to prove it." |

## Query for shot 8 (Zetaris SQL editor)

Tested through the same endpoint on 22 Oct (about 2 s; SP 68,641 orders, MG 7,735, PR 7,512, RJ 4,227, SC 3,603). It
joins the Postgres order system with the seller platform's CSV export in one query:

```sql
SELECT s.seller_state, COUNT(DISTINCT o.order_id) AS orders
FROM oms.orders o
JOIN sellers.order_items i ON i.order_id = o.order_id
JOIN sellers.sellers s ON s.seller_id = i.seller_id
WHERE o.order_status = 'delivered'
GROUP BY s.seller_state ORDER BY orders DESC LIMIT 5
```

## Rehearsal notes (23 Oct, 23:30 to 23:40 IST, the code as submitted, app warmed, fresh memory, through Zetaris)

- Before recording: run `start.cmd` and wait for "Warm-up done" (it loads the analysis tables into the app: about
  30 s for Olist, 10 s for Kaveri once Zetaris is up). Without it, the first question waits a minute for its tables.
- Shot 2: the first "Investigate why" card on the Olist control tower is "Late deliveries spiked in March 2018"; it
  starts the run at once with the demo question.
- Shot 3: the activity shows "Meterless Scout: delivery.explain → the analysis engine · signed contract" at 4 s; the
  **Preliminary answer** card appeared at 8 s; the full brief was ready at 38 s (NVIDIA was fast; on a slow evening
  it took up to 3 minutes, so keep the cut).
- Shot 4: **In short**, then the **why chain** (two causes with their shares, RJ, six ruled-out tests struck
  through); citation chips open the computed table, "Data via Zetaris" and the SQL (unfold it).
- Shot 4b: "Ask why next" → the Rio de Janeiro run: its brief was ready at 29 s (34.5% against 16.3%).
- Shot 6: Pune's preliminary answer came at 24 s (it includes the proven-optimal transfer); after **Approve** the
  Action Executor's deliverables were ready within seconds this time (it took 2.5 minutes on 22 Oct).
- Shot 7: the second March 2018 run showed "Recalled 2 memories" and the cause graph on its Memory tab.
- The Memory page now shows the written record as verified quotes only, with a link to the run it came from.

## Where Nemotron appears

- Document Analyst (shots 3–4): Nemotron 3.5 Super finds and quotes the news briefs; quotes are verified word for word.
- Brief answer (shot 4): Nemotron 3.5 Super phrases the short answer from computed, checked facts.
- Memory (shot 7): Nemotron 3 Embed (`nvidia/nemotron-3-embed-1b`) embeds memories and recall queries.
- Fallback (not on screen): Nemotron 3 Ultra takes over when 3.5 Super is overloaded.

## Rules check (Official Rules, section 6)

- Main workflow and how the agent solves the challenge: shots 2–6.
- Key features: computed causes, ruled-out red herrings, numbers check, clickable evidence, sized actions, approval,
  proven-optimal transfer, deliverables.
- Zetaris integration: shots 3, 4 (evidence shows the harness) and 8. Meterless integration: shot 7.

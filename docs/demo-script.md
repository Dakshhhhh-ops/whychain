# WhyChain demo video: script and shot list

Target length 3:15 (rules: 1 to 4 minutes). Screen recording at 1600×1000 or larger, browser zoom 100%, app at
`http://localhost:8000`. **[Nemotron]** marks where NVIDIA Nemotron is doing the work on screen.

Live runs through Zetaris take 1.5 to 4 minutes, so the video shows each run starting live, then cuts ahead (say so
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
| 3 | 0:35–1:00 | Investigation, **Board** tab, live | Let the agents stream for ~20 s: Memory recall, the analysis battery's tests appearing, the Document Analyst reading briefs. Show the **Zetaris** badge in the header | "Every query goes through **Zetaris**, which federates Postgres, CSV and JSON exports into one SQL endpoint; nothing is copied. The numbers come from code: a fixed battery of tests, counterfactuals and coded verdicts. **[Nemotron]** reads the documents and quotes them word for word." Cut: "about two minutes later". |
| 4 | 1:00–1:35 | **Brief** tab | Read the headline; scroll the root causes; click a citation like `[T3.A1]` to open its evidence (table + SQL + "Zetaris"); scroll to **Ruled out** and the strike line | "19% late against a normal 5.6%. Carrier transit explains 72.7% of the rise, shorter promises 26.7%. The strike? Ruled out: lateness was already high before it began. Every number is checked against the cell it came from. **[Nemotron]** writes only the two-sentence answer at the top, from those checked facts." |
| 5 | 1:35–1:55 | Brief, **Recommended actions** | Open the promise-buffer action, click **Approve** | "Each action is sized from the data: promise buffers per state, RJ plus 9 days, 255 late orders that would have been on time. Changing customer promises needs approval, so it waits for me." |
| 6 | 1:55–2:35 | Switch dataset to **Kaveri**; open the Pune run (live start, cut ahead, or the earlier run) | Brief: headline, Voltix purchase orders held at the Chennai port strike; the transfer action with **Protects** and cost; **Approve**; cut ahead about two to three minutes; show the deliverables (a per-SKU transfer order and the supplier email draft; the Action Executor is an agent, so check them in the rehearsal run) | "A simulated distributor. Pune orders at risk: Voltix purchase orders are stuck behind a dock strike at Chennai. The fix is a stock transfer from Mumbai, proven optimal by Optiagent with a duality certificate, at 133,590 rupees, protecting 62 orders. Approve it, and the Action Executor prepares the transfer order and the supplier email." |
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

## Rehearsal notes (22 Oct, 22:20 to 22:45 IST, app on Zetaris)

- Shot 2: the first "Investigate why" card on the Olist control tower is "Late deliveries spiked in March 2018"; it
  starts the run at once with the demo question.
- Shot 3: the board fills with the 13 computed tests within about 5 s; the run took 2 to 3.5 minutes through Zetaris.
- Shot 4: citation chips open a drawer with the computed table, "Data via Zetaris" and, under "The federated query
  these numbers come from", the SQL (click to unfold it).
- Shot 6: Pune run took about 3.5 minutes; after **Approve**, the Action Executor took about 2.5 minutes.
- Shot 7: the second March 2018 run showed "Recalled 8 memories, plus a cached data map" and the cause graph.
  The Memory tab also shows the Document Analyst's one-line summary, which over-reaches on the strike (see the
  README's Known limitations); don't linger on it.
- The left panel shortens "Nemotron embeddings" to "Nemotr..." at this width; the run header shows it in full.

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

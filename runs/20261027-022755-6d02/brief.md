# 23 of the 29 open orders at the Pune DC due in the coming week have a line on backorder (79.3%). Main causes: inbound purchase orders for the short SKUs are late, held up at Chennai by a dock workers' strike (58.4% of the short units), and demand for the short SKUs spiked above the planning baseline (40.2% of the short units).

## Summary
- **What happened:** 23 of the 29 open orders at the Pune DC due in the coming week have a line on backorder (79.3%): 1,730 units across 22 SKUs, worth INR 5,650,370 on backorder. [T1.A1]
- **Why:** Inbound purchase orders for the short SKUs are late, held up at Chennai by a dock workers' strike (58.4% of the short units), demand for the short SKUs spiked above the planning baseline (40.2% of the short units) and reorder points are too low for recent demand (33.5% of the short units).
- **What to do:** A1. Transfer stock to cover the S-14 shortage (62 orders; INR 22,822,365 of orders at stake; costs INR 133,590; within limits). A2. Chase the late purchase orders from Voltix Power Components and plan around Chennai (within limits).

## Answer
23 of the 29 open Pune orders due next week have a line on backorder (79.3%), totaling 1,730 units across 22 SKUs worth INR 5,650,370 [T1.A1]. Most short units are delayed because late inbound purchase orders are stuck at Chennai due to a dock workers' strike (58.4%) [T4.A1][T5.A1], while demand spiked above the planning baseline for 40.2% of the short units [T6.A1].

## Root causes
1. **Inbound purchase orders for the short SKUs are late** (58.4% of the short units), held up at Chennai by a dock workers' strike. [T4.A1]
2. **Demand for the short SKUs spiked above the planning baseline** (40.2% of the short units). [T6.A1]
3. **Reorder points are too low for recent demand** (33.5% of the short units). [T7.A1]

## Ruled out
- **Duplicate orders (the same external reference) inflate the numbers**: The copies order units equal to only 4.0% of the shortage (cause threshold: 25%). [T8.A1]

## What customers and the written record say
- URGENT: Chennai port strike, vessel MV Coral Breeze held at anchorage (2026-10-09): "Dock workers at Chennai port began an indefinite strike this morning. Our vessel MV Coral Breeze, carrying your festive pre-build containers, is held at anchorage and cannot berth. Affected POs include, for Pune DC: PO-40494, PO-40495, PO-40496, PO-40497, PO-40498, PO-40499" [T9.D1]
- RE: Chennai port strike: revised ETA (2026-10-14): "Our forwarder expects berthing to resume around 28 October once negotiations close, with container release by 30 October. Revised DC arrival: Chennai about 31 October, Pune and Mumbai about 3 November." [T9.D2]
- IMD orange alert: heavy rain in Pune, 19–22 October (2026-10-19): "The DC remains fully operational and dock schedules are unchanged." [T9.D3]

## Recommended actions
| Action | Protects | Value at stake | Est. cost | Approval |
|---|---|---|---|---|
| A1. Transfer stock to cover the S-14 shortage: MUM→PUN 4,414 units, plus 621 units by air [A.P1] | 62 orders | INR 22,822,365 | INR 133,590 | within limits |
| A2. Chase the late purchase orders from Voltix Power Components and plan around Chennai [T4.A1] | — | — | not computed (no cost data) | within limits |
| A3. Raise reorder points to recent demand: CBL-1027 at PUN 10 -> 22; CHG-1004 at PUN 292 -> 540; EBD-1032 at PUN 167 -> 277; KBM-1066 at PUN 10 -> 45; KBM-1069 at PUN 15 -> 36 [T7.A1][T6.A1] | — | — | not computed (no cost data) | within limits |

## How we know
- **Inbound purchase orders for the short SKUs are late** (58.4% of the short units): 58.4% of the short units are SKUs with a late inbound purchase order; most late units are from S-14 (Voltix Power Components), coming through Chennai. Latest: PO-40494 CHG-1001 475 units, ETA 2026-11-03 vs expected 2026-10-13 (21 days late); PO-40496 CHG-1003 150 units, ETA 2026-11-03 vs expected 2026-10-13 (21 days late); PO-40497 CHG-1004 275 units, ETA 2026-11-03 vs expected 2026-10-13 (21 days late). [T4.A1]
   - Why they are late: 58.4% of the short units are SKUs whose late inbound orders come through a port the signals feed reports as disrupted. Disrupted ports: Chennai (DISRUPTED: Dock workers' strike). [T5.A1]
- **Demand for the short SKUs spiked above the planning baseline** (40.2% of the short units): 40.2% of the short units are SKUs whose recent demand is at least 1.5x the planning baseline: SPK-1059 at PUN: 9.3 a day vs 0.5 (18.57x); SPL-1047 at PUN: 7.9 a day vs 0.5 (15.71x); KBM-1066 at PUN: 5.0 a day vs 0.5 (10.0x). [T6.A1]
- **Reorder points are too low for recent demand** (33.5% of the short units): 33.5% of the short units are SKUs whose reorder point is below what recent demand needs: CBL-1027 at PUN reorder point 10 vs 22 needed (last reviewed 2026-08-31); CHG-1004 at PUN reorder point 292 vs 540 needed (last reviewed 2026-08-31); EBD-1032 at PUN reorder point 167 vs 277 needed (last reviewed 2026-08-31). [T7.A1]
- Each check reports the share of the short units it is involved in (for duplicates: the units the extra copies order). Shares overlap: one SKU can be both late inbound and under a stale reorder point.
- Every number above was computed by the analysis engine from the cited results, and checked against their cells. Verdicts follow fixed rules (see the README), not a model's judgement.
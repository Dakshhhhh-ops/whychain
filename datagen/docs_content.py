"""Unstructured documents for the fictional company: contracts, policies and email.

Each email lands in a different team's inbox. That is the point: the facts that
explain a problem are spread across people who never compare notes.
"""
from __future__ import annotations


def email(sender: str, to: str, date: str, subject: str, body: str, cc: str = "") -> str:
    head = f"From: {sender}\nTo: {to}\n"
    if cc:
        head += f"Cc: {cc}\n"
    return f"{head}Date: {date}\nSubject: {subject}\n\n{body.strip()}\n"


def render_all(ctx: dict) -> dict[str, str]:
    sg = ctx["shree_ganesh"]
    stuck_pun = ", ".join(ctx["voltix_stuck_by_wh"]["PUN"][:6]) or "n/a"
    stuck_chn = ", ".join(ctx["voltix_stuck_by_wh"]["CHN"][:6]) or "n/a"
    edi_names = ", ".join(c["name"] for c in ctx["edi_customers"])
    docs: dict[str, str] = {}

    # ------------------------------------------------------------ contracts
    docs["contracts/supplier_S-14_voltix_supply_agreement.md"] = f"""# Supply Agreement: Voltix Power Components (Supplier S-14)

**Parties:** Kaveri Consumer Electronics Distribution Pvt Ltd ("Buyer") and Voltix Power Components Co. Ltd, Shenzhen ("Supplier")
**Effective:** 1 January 2026 · **Term:** 24 months · **Products:** chargers and power banks (SKU families CHG, PWB)

## 1. Lead time and delivery
1.1 Standard lead time is **21 calendar days** from PO acceptance to delivery at the Buyer's distribution centre.
1.2 Incoterm: **CIF Chennai**. All India-bound shipments are consolidated through the Supplier's forwarder at
Chennai port, then trucked to Buyer DCs (Chennai 1 day, Pune 4 days, Mumbai 4 days).
1.3 The Supplier shall notify the Buyer in writing within 48 hours of any event likely to delay a shipment.

## 2. Liquidated damages
2.1 For late delivery attributable to the Supplier, liquidated damages accrue at **0.5% of PO value per week**, capped at 5%.

## 3. Force majeure
3.1 Neither party is liable for delay caused by events beyond its reasonable control, including natural disasters,
war, government action, epidemics, and **strikes, lockouts or closures of ports, airports or customs**.
3.2 During a force majeure event, liquidated damages under clause 2 **do not accrue**.
3.3 If a force majeure event exceeds 30 days, the Buyer may cancel affected POs without penalty.

## 4. Expedite options
4.1 At the Buyer's request and cost, the Supplier can ship by **air freight to Mumbai (BOM)**. Lead time 6 days.
Surcharge: **INR 95 per unit** for chargers and **INR 140 per unit** for power banks. Minimum 500 units per shipment.
"""

    docs["contracts/supplier_S-07_soundwave_supply_agreement.md"] = """# Supply Agreement: SoundWave Acoustics Co. (Supplier S-07)

**Effective:** 1 March 2026 · **Products:** earbuds (EBD family)

1. Standard lead time **18 calendar days**, CIF Nhava Sheva (JNPT), trucked to Buyer DCs.
2. Minimum order quantity: 100 units per SKU per PO.
3. **Air-freight expedite** to Mumbai available within 5 days at **INR 85 per unit** surcharge.
4. Promotional volumes: the Buyer shall share promotional forecasts **at least 21 days** before a campaign starts
so the Supplier can reserve capacity. Without notice, the Supplier cannot guarantee quantities above 1.5x the
trailing 8-week average.
"""

    docs["contracts/carrier_C02_swiftcargo_rate_agreement_v2.md"] = """# Transport Rate Agreement v2: SwiftCargo Logistics (Carrier C02)

**Effective:** 1 September 2026 (replaces v1 of 1 March 2025)
**Lanes:** MUM → Nashik (MUM-NSK), CHN → Hyderabad (CHN-HYD)

## Summary of changes from v1
- Base rates reduced by an average of **22%**.
- **Annexure B applies to lane MUM-NSK.**

## Annexure B: Consolidated service, lane MUM-NSK
B.1 From 1 September 2026, lane MUM-NSK moves from dedicated full-truckload (FTL) to a **consolidated
multi-drop service**.
B.2 Consolidated departures run **twice a week, on Tuesday and Friday**, from the Bhiwandi hub.
B.3 Transit time from departure to delivery is **2 days**.
B.4 Freight handed over on other days is held at the hub until the next scheduled departure.
B.5 The Shipper remains responsible for aligning customer promise dates with the departure schedule.

## Rates (INR per kg, minimum charge per consignment INR 2,400)
| Lane | v1 (FTL) | v2 |
|---|---|---|
| MUM-NSK | 7.10 | 4.90 (consolidated) |
| CHN-HYD | 8.40 | 7.20 (FTL, unchanged service) |

## Service levels
On-time delivery target **95%**, measured against the transit time for the service type.
"""

    docs["contracts/carrier_C03_deccan_freight_rate_card.md"] = """# Rate Card: Deccan Freight (Carrier C03), valid 15 Nov 2025 to 14 Nov 2026

Daily dedicated FTL departures. Current lanes: MUM-THN, MUM-STV, CHN-CJB.
Lanes on request with 7 days' onboarding notice: **MUM-NSK** (Nashik) at **INR 5.80 per kg**, daily departures, 1-day transit.

| Lane | INR per kg | Transit | Trailing 6-month on-time |
|---|---|---|---|
| MUM-THN | 6.20 | 1 day | 96% |
| MUM-STV | 6.90 | 2 days | 95% |
| CHN-CJB | 7.40 | 2 days | 94% |
| MUM-NSK (on request) | 5.80 | 1 day | — |
"""

    docs[f"contracts/customer_{sg['customer_id']}_shree_ganesh_master_agreement.md"] = f"""# Master Supply Agreement: Shree Ganesh Electronics Retail ({sg['customer_id']})

**Customer:** Shree Ganesh Electronics Retail, Pune (key account, 14 stores)
**Supplying DC:** Pune (PUN) · **Effective:** 1 April 2026

## 4. Delivery performance
4.1 The Distributor shall deliver each order complete by the **promised date** confirmed on order acceptance.
4.2 **Late delivery penalty:** 1.5% of the order value for each day late, capped at 10% of the order value.
4.3 If more than **20% of orders** in any calendar month are late, the Customer may move up to 30% of its volume
to an alternative distributor for the next quarter.

## 5. Festive season
5.1 Between 1 October and 15 November the Distributor shall keep at least **14 days of cover** at the Pune DC for
the Customer's top-moving SKUs, including chargers and power banks.
"""

    # ------------------------------------------------------------ policies
    docs["policies/inter_dc_transfer_policy.md"] = """# Policy: Inter-DC Stock Transfers (OPS-POL-007)

Owner: Head of Supply Planning · Last updated: 12 May 2026

1. Stock may be transferred between DCs when the receiving DC has orders that cannot be filled and the sending DC
keeps at least **14 days of forward cover** for the transferred SKUs after the transfer.
2. Transfer lanes and costs (dedicated truck, up to 1,800 cartons or units):
   | From → To | Transit | Cost per truck |
   |---|---|---|
   | MUM → PUN | 1 day | INR 14,000 |
   | PUN → MUM | 1 day | INR 14,000 |
   | MUM → CHN | 3 days | INR 52,000 |
   | CHN → PUN | 3 days | INR 49,000 |
3. Transfers above **INR 3,00,000** total cost, or affecting more than 20 SKUs, need approval from the Head of Supply Planning.
4. Orders ship complete. A single short line holds the whole order.
"""

    docs["policies/replenishment_policy.md"] = """# Policy: Replenishment Parameters (OPS-POL-003)

1. Reorder points = average daily demand x (supplier lead time + 2 days) + 9 days of safety stock.
2. Reorder points are reviewed **quarterly**. Last review: **31 August 2026** (baseline Jul–Aug). Next review: 30 November 2026.
3. **Marketing must share any promotion with Supply Planning at least 21 days before launch** so reorder points and
supplier capacity can be adjusted. Promotions not shared are planned at baseline demand.
4. Seasonal pre-builds for Diwali are raised in early September for the top categories.
"""

    docs["policies/edi_operations_runbook.md"] = """# Runbook: EDI Order Intake (IT-RUN-021)

EDI customers send purchase orders through the EDI connector, which creates sales orders in the ERP.
- Every EDI order carries the customer's PO number in `external_ref`. **The same external_ref should never appear on two orders.**
- If the connector times out, it retries up to 2 times.
- Duplicate check: query sales orders grouped by customer_id and external_ref having count > 1.
"""

    # ------------------------------------------------------------ email
    docs["emails/2026-08-20_swiftcargo_rate_agreement_v2.md"] = email(
        "Rohit Menon <rohit.menon@swiftcargo.example>", "logistics@kaveri.example", "2026-08-20 16:12",
        "Rate agreement v2: signed copy attached",
        """Hi team,

Thanks for signing. v2 takes effect on 1 September. As discussed with your logistics manager, the 22% saving on
Nashik comes from moving MUM-NSK onto our consolidated milk-run (Tue/Fri departures, see Annexure B).
Hyderabad stays on FTL.

Regards,
Rohit""")

    docs["emails/2026-09-25_marketing_diwali26_campaign.md"] = email(
        "Ananya Rao <ananya.rao@kaveri.example>", "sales-west@kaveri.example", "2026-09-25 11:40",
        "DIWALI26 campaign is GO: 25% off SoundWave earbuds",
        """Hi Sales West,

Final approval is in. DIWALI26 runs **8–18 October** for all Mumbai-region retailers (Mumbai, Thane, Nashik,
Surat): 25% off the full SoundWave (S-07) earbuds range. Push it hard, it's our flagship festive deal. Retailers just
quote promo code DIWALI26 on the order.

We expect 3x normal volume on SoundWave.

Cheers,
Ananya (Marketing)""", cc="marketing@kaveri.example")

    docs["emails/2026-10-09_voltix_port_strike_notice.md"] = email(
        "Li Wei <li.wei@voltix-power.example>", "procurement@kaveri.example", "2026-10-09 19:05",
        "URGENT: Chennai port strike, vessel MV Coral Breeze held at anchorage",
        f"""Dear Kaveri Procurement,

Dock workers at Chennai port began an indefinite strike this morning. Our vessel MV Coral Breeze, carrying your
festive pre-build containers, is held at anchorage and cannot berth.

Affected POs include, for Pune DC: {stuck_pun}
and for Chennai DC: {stuck_chn}.

This is a force majeure event under clause 3 of our agreement. We will update ETAs as the situation develops.
Air freight to Mumbai is available under clause 4 if you need it urgently.

Best regards,
Li Wei, Logistics Coordinator, Voltix""")

    docs["emails/2026-10-14_voltix_eta_update.md"] = email(
        "Li Wei <li.wei@voltix-power.example>", "procurement@kaveri.example", "2026-10-14 10:22",
        "RE: Chennai port strike: revised ETA",
        """Dear team,

Our forwarder expects berthing to resume around 28 October once negotiations close, with container release by
30 October. Revised DC arrival: Chennai about 31 October, Pune and Mumbai about 3 November. The supplier_eta
field on your portal has been updated.

Regards,
Li Wei""")

    docs["emails/2026-10-17_it_edi_connector_incident.md"] = email(
        "IT Service Desk <it-servicedesk@kaveri.example>", "it-ops@kaveri.example", "2026-10-17 09:15",
        "INC-48812: EDI connector v3.2 timeouts, possible duplicate orders",
        f"""Incident INC-48812 (P3)

Since the EDI connector upgrade to v3.2 on 16 October, timeouts to the ERP API trigger automatic retries. Early
analysis suggests the retry does not check for an existing order, so some EDI purchase orders may have been
created more than once. Affected customers: {edi_names}.

Workaround: none yet. Fix scheduled for the release on 24 October. Orders created during this window should be
deduplicated by external_ref.

IT Service Desk""")

    docs["emails/2026-10-19_facilities_weather_alert.md"] = email(
        "Facilities Pune <facilities.pune@kaveri.example>", "all-pune@kaveri.example", "2026-10-19 07:30",
        "IMD orange alert: heavy rain in Pune, 19–22 October",
        """All,

IMD has issued an orange alert for heavy rain in Pune from 19 to 22 October. Please take care when commuting.
The DC remains fully operational and dock schedules are unchanged.

Facilities""")

    docs["emails/2026-10-20_shree_ganesh_escalation.md"] = email(
        "Vikram Kulkarni <vikram@shreeganesh-retail.example>", "kam-pune@kaveri.example", "2026-10-20 18:47",
        "Festive orders showing BACKORDERED again",
        f"""Hello,

Several of our orders due this week show as backordered on your portal, mostly chargers and power banks. This is
our peak week before Diwali. Please remember our agreement ({sg['customer_id']}): late deliveries attract penalties,
and we need 14 days of cover on power accessories in this season.

Please share a recovery plan by Wednesday.

Vikram Kulkarni, Category Head, Shree Ganesh Electronics Retail""")

    docs["emails/2026-10-03_finance_quarter_close.md"] = email(
        "Finance <finance@kaveri.example>", "all-managers@kaveri.example", "2026-10-03 12:00",
        "Q2 close timetable",
        """Reminder: Q2 close submissions are due by 10 October. Please accrue all open freight invoices.""")

    docs["emails/2026-10-12_hr_holiday_calendar.md"] = email(
        "HR <hr@kaveri.example>", "all@kaveri.example", "2026-10-12 09:00",
        "Diwali holiday calendar",
        """The DCs will close on 8 and 9 November for Diwali. Plan dispatches accordingly.""")

    return docs

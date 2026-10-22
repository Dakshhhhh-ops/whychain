"""Unit tests for the deterministic checks: claim numbers, action figures, memory reuse, eval matching."""
from evals.run_evals import matches
from whychain.agents.nodes import ground_action_numbers, question_similarity
from whychain.grounding import check_claim, numbers_in

ROWS = [{"id": "T1.Q1", "type": "sql", "columns": ["month", "orders", "late", "late_rate"],
         "rows": [["2018-03", 7003, 1328, 0.18963], ["2018-02", 6555, 926, 0.1413]]}]


def test_numbers_skip_dates_ids_and_years_but_not_small_counts():
    found = [n.raw for n in numbers_in("On Mar 12-13 2018 (T2.Q1, S-14) 3 DCs had 1,328 late of 7,003 (18.96%)")]
    assert found == ["3", "1,328", "7,003", "18.96"]


def test_claim_numbers_need_cited_cells_or_implied_arithmetic():
    # distinctive numbers (two decimals, or 100+) can be found in the cited rows; a one-decimal rate needs a cell ref
    c = check_claim("Late rate 18.96% (1,328 of 7,003) vs 14.1% in Feb", ROWS)
    assert c["support"] == {"18.96": "found", "1,328": "found", "7,003": "found", "14.1": "missing"}


def test_invented_numbers_are_reported():
    c = check_claim("35.31% of orders were late, and outreach costs BRL 5,000", ROWS)
    assert c["missing"] == ["35.31", "5,000"]


def test_action_figures_not_in_cited_data_are_removed():
    out, removed = ground_action_numbers({"est_cost": 5000, "value_protected": 1328, "orders_protected": 0}, ROWS)
    assert out == {"est_cost": None, "value_protected": 1328.0, "orders_protected": 0.0}
    assert removed == ["est_cost=5000"]
    out, removed = ground_action_numbers({"est_cost": 15000}, [])  # no evidence at all
    assert out["est_cost"] is None and removed == ["est_cost=15000"]


def test_findings_are_reused_only_for_similar_questions():
    march = "Why did late deliveries jump to 21% for orders placed in March 2018?"
    assert question_similarity(march, "Why did late deliveries jump to 19% for orders placed in March 2018?") >= 0.4
    assert question_similarity(march, "Why did deliveries run late around Black Friday 2017?") < 0.4


def test_eval_terms_match_whole_words_not_fragments():
    assert not matches("the period before", ["rio"]) and matches("rio de janeiro", ["rio"])
    assert not matches("final report", ["port"]) and matches("the port strike", ["port"])
    assert not matches("we expedite", ["edi"]) and not matches("repair", ["air"])
    assert matches("a consolidated milk run", ["consolidat"])


def test_key_number_on_a_row_beyond_the_stored_sample():
    """A result stored truncated (2 of 5 rows): a key number pointing at row 4 is re-fetched, not an IndexError."""
    full = [["2018-0%d" % m, 100 * m, 10 * m, 0.1 * m] for m in range(1, 6)]
    ev = [{"id": "T1.Q1", "type": "sql", "columns": ["month", "orders", "late", "late_rate"], "rows": full[:2],
           "truncated": True, "sql": "SELECT month, orders, late, late_rate FROM t"}]
    keys = [{"value": 500, "evidence": "T1.Q1", "column": "orders", "where": {"month": "2018-05"}}]
    c = check_claim("May had 500 orders", ev, keys, fetch=lambda table: full)
    assert c["missing"] == [] and c["key_numbers"]["verified"] == 1


def test_answer_may_repeat_only_the_rule_thresholds_it_was_shown():
    from whychain.agents.nodes import check_brief

    md = "## Answer\nOrders did not rise by 25% or more, and 40% of them were late.\n\n## Root causes\n- none"
    shown, _, rep = check_brief(md, "Headline", {"evidence": {}, "findings": []}, [], answer_rules=[25.0])
    assert rep["unverified"] == ["40%"] and "25% or more" in shown   # the threshold it was given passes
    _, _, rep = check_brief(md, "Headline", {"evidence": {}, "findings": []}, [])
    assert rep["unverified"] == ["40%", "25%"]                        # without it, the strict answer check flags it

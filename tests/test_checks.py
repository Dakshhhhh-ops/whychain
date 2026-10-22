"""Unit tests for the deterministic checks: claim numbers, action figures, memory reuse, eval matching."""
from evals.run_evals import matches
from whychain.agents.nodes import ground_action_numbers, question_similarity
from whychain.grounding import check_claim, numbers_in

ROWS = [{"type": "sql", "rows": [["2018-03", 7003, 1328, 0.18963], ["2018-02", 6555, 926, 0.1413]]}]


def test_numbers_skip_dates_ids_and_small_counts():
    found = [raw for raw, _, _ in numbers_in("On Mar 12-13 2018 (T2.Q1, S-14) 3 DCs had 1,328 late of 7,003 (18.96%)")]
    assert found == ["1,328", "7,003", "18.96"]


def test_claim_numbers_found_with_rounding_percent_and_one_step_arithmetic():
    c = check_claim("Late rate 18.96% (1,328 of 7,003) vs 14.1% in Feb, up 4.8 points", ROWS)
    assert c == {"checked": 5, "missing": []}


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

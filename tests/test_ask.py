"""Ask Zetaris: the coding model writes SQL, the data layer runs it, and a rejected query goes back with its error."""
from types import SimpleNamespace

from whychain.ask import ask
from whychain.llm import LLMResult


class FakeHarness:
    name = "zetaris"

    def __init__(self):
        self.seen = []

    def query(self, sql, limit=200):
        self.seen.append(sql)
        if "orders_typo" in sql:
            return SimpleNamespace(ok=False, error="Table or view not found: oms.orders_typo", harness=self.name,
                                   elapsed_ms=5, columns=[], rows=[], row_count=0, truncated=False)
        return SimpleNamespace(ok=True, error=None, harness=self.name, elapsed_ms=7, columns=["n"], rows=[[42]],
                               row_count=1, truncated=False)


def test_a_rejected_query_is_fixed_with_the_data_layers_error():
    prompts = []

    def chat(role, system, user, **kw):
        prompts.append(user)
        sql = "SELECT COUNT(*) AS n FROM oms.orders_typo" if len(prompts) == 1 else "SELECT COUNT(*) AS n FROM oms.orders"
        return LLMResult(f'{{"sql": "{sql}", "explanation": "counts orders"}}', "xai-code", "grok-build-0.1", 10)

    h = FakeHarness()
    out = ask("How many orders?", h, SimpleNamespace(chat=chat), "oms.orders(order_id)", "2018-09-01", "olist")
    assert out["ok"] and out["rows"] == [[42]] and out["provider"] == "xai-code"
    assert [a["error"] is None for a in out["attempts"]] == [False, True]
    assert "Table or view not found" in prompts[1]          # the model saw exactly what Zetaris said

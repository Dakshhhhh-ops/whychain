"""Private documents: the Document Analyst's calls go only to Meterless Nightshift, never to a cloud model."""
import pytest

from whychain.config import Provider
from whychain.llm import LLM, LLMError


def providers(nightshift: bool) -> dict[str, Provider]:
    return {
        "nim": Provider("nim", "https://cloud.example/v1", "key", "nemotron"),
        "nim-backup": Provider("nim-backup", "https://cloud.example/v1", "key", "nemotron-ultra"),
        "nightshift": Provider("nightshift", "http://127.0.0.1:8088/v1" if nightshift else "", "rt-local",
                               "meterless/fast"),
    }


def names(llm: LLM, role: str) -> list[str]:
    return [p.name for p in llm._chain(role)]


def test_documents_go_to_the_cloud_first_by_default():
    llm = LLM(providers=providers(nightshift=True))
    assert names(llm, "local") == ["nim", "nim-backup", "nightshift"]


def test_private_documents_use_only_nightshift():
    llm = LLM(providers=providers(nightshift=True))
    llm.private_docs = True
    sent = []
    llm._create = lambda p, kwargs, agent, attempts, deadline: sent.append(p.name) or (_ for _ in ()).throw(
        RuntimeError("offline"))
    with pytest.raises(LLMError):
        llm.chat("local", "system", "user", agent="Doc Analyst")
    assert sent == ["nightshift"]          # no cloud fallback, even when Nightshift fails


def test_private_documents_without_nightshift_say_so():
    llm = LLM(providers=providers(nightshift=False))
    llm.private_docs = True
    with pytest.raises(LLMError, match="Nightshift"):
        llm.chat("local", "system", "user", agent="Doc Analyst")


def test_private_mode_leaves_other_roles_alone():
    llm = LLM(providers=providers(nightshift=True))
    llm.private_docs = True
    assert names(llm, "work")[0] == "nim"


class _Resp:
    """A chat completion as Nightshift returns it, with its compute receipt."""
    def __init__(self, path: str):
        self.choices = [type("C", (), {"message": type("M", (), {"content": "ok"})()})()]
        self.usage = None
        self.model_extra = {"meterless": {"privacy": {"path": path}, "egress": {"destination": "none"},
                                          "receipt_id": "rcpt_1", "winning_route": {"label": "Llama 3.2 1B"}}}


def test_a_private_reply_needs_a_local_only_receipt():
    llm = LLM(providers=providers(nightshift=True))
    llm.private_docs = True
    headers = []
    llm._create = lambda p, kwargs, agent, attempts, deadline: headers.append(kwargs["extra_headers"]) or _Resp(
        "local-only")
    assert llm.chat("local", "system", "user", agent="Doc Analyst").text == "ok"
    assert headers == [{"meterless-app": "whychain-private"}]
    llm._create = lambda p, kwargs, agent, attempts, deadline: _Resp("cloud")
    with pytest.raises(LLMError):       # a reply that went to the cloud is discarded, not used
        llm.chat("local", "system", "user", agent="Doc Analyst")


def test_the_private_reader_quotes_whole_sentences_by_number():
    from types import SimpleNamespace

    from whychain.agents.nodes import investigate

    body = "Postal workers began a national strike on 12 March. Parcels piled up in Rio de Janeiro. It ended on 14 March."
    rows = [["d1", "news_brief", "Correios strike, March 2018", "2018-03-12", None, None, body],
            ["d2", "news_brief", "Black Friday 2017", "2017-11-24", None, None, "Sales doubled on 24 November."]]
    harness = SimpleNamespace(query=lambda sql, limit=50: SimpleNamespace(
        ok=True, rows=rows, columns=["doc_id", "doc_type", "title", "sent_at", "sender", "recipients", "body"]))
    asked = []

    def json(role, system, user, **kw):
        asked.append(role)
        # a small model's answer: relevant or not, and sentence numbers (one out of range, which is ignored)
        return {"relevant": True, "sentences": [1, 2, 9]} if "Correios" in user else {"relevant": False}

    ctx = SimpleNamespace(llm=SimpleNamespace(private_docs=True, json=json), harness=harness,
                          tracer=SimpleNamespace(event=lambda *a, **k: None))
    task = {"id": "T9", "agent": "docs", "type": "hypothesis", "round": 1, "context_only": True,
            "statement": "What the written record says"}
    out = investigate({"task": task, "question": "Why were deliveries late in March 2018?"}, ctx)
    quotes = [e["quote"] for e in out["evidence"].values()]
    assert quotes == ["Postal workers began a national strike on 12 March.", "Parcels piled up in Rio de Janeiro."]
    assert asked == ["local", "local"]                  # one short call per document, on the private route
    assert out["findings"][0]["verdict"] == "supports" and "Correios strike" in out["findings"][0]["claim"]


def test_the_private_reader_keeps_to_the_period():
    from types import SimpleNamespace

    from whychain.agents.nodes import investigate

    rows = [["old", "news_brief", "Strike, 2017", "2017-09-19", None, None, "Postal workers struck in September 2017."],
            ["near", "news_brief", "Storm, February 2018", "2018-02-15", None, None, "A storm closed roads in Rio."],
            ["in", "news_brief", "Strike, March 2018", "2018-03-12", None, None, "Postal workers struck on 12 March 2018."]]
    harness = SimpleNamespace(query=lambda sql, limit=50: SimpleNamespace(
        ok=True, rows=rows, columns=["doc_id", "doc_type", "title", "sent_at", "sender", "recipients", "body"]))
    seen = []

    def json(role, system, user, **kw):   # a small model that calls nothing relevant
        seen.append(user.split("DOCUMENT: ")[1].split(" (")[0])
        return {"relevant": "Storm" in user, "sentences": [1] if "Storm" in user else []}

    ctx = SimpleNamespace(llm=SimpleNamespace(private_docs=True, json=json), harness=harness,
                          tracer=SimpleNamespace(event=lambda *a, **k: None))
    task = {"id": "T9", "agent": "docs", "type": "hypothesis", "round": 1, "context_only": True, "statement": "Record"}
    out = investigate({"task": task, "question": "Why late in March 2018?",
                       "analysis": {"primary": {"start": "2018-03-01", "end": "2018-03-31"}}}, ctx)
    assert seen == ["Storm, February 2018", "Strike, March 2018"]     # the 2017 brief is too far off to read
    assert [e["doc_id"] for e in out["evidence"].values()] == ["in", "near"]   # in the period first, kept regardless

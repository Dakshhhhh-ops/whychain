"""Model access. Every provider speaks the OpenAI API, so one client covers all of them.

Each agent asks for a *role*, not a model:
  reason  -> planner, evaluator, risk, reporter   (xAI Grok, then NVIDIA NIM, then local)
  work    -> data investigators, scout           (NVIDIA NIM, then Grok, then local)
  local   -> document analyst                    (Meterless Nightshift on the local NVIDIA GPU, then Ollama, then NIM)

If a provider fails, the call falls through to the next one, and the trace records it.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass

from openai import APIConnectionError, APITimeoutError, InternalServerError, OpenAI, RateLimitError

from whychain.config import SETTINGS, Provider
from whychain.slots import slot

ROLE_CHAINS = {
    "reason": ["xai", "nim-reason", "nim-backup", "nim", "nightshift", "ollama"],
    "work": ["nim", "nim-backup", "xai", "nim-reason", "nightshift", "ollama"],
    "local": ["nightshift", "nim", "nim-backup", "ollama", "xai"],
}
JSON_MODE_OK = {"xai", "ollama", "nightshift", "nim", "nim-reason", "nim-backup"}
TRANSIENT = (InternalServerError, RateLimitError, APITimeoutError, APIConnectionError)
ATTEMPTS = 4  # per provider, for transient errors, before falling back to the next provider
RATE_LIMIT_ATTEMPTS = 5  # 429s: the backup model shares the same key and quota, so wait instead of switching
# at most this many calls in flight per endpoint ON THIS MACHINE, across every WhyChain process (see slots.py)
MAX_PARALLEL = int(os.environ.get("WHYCHAIN_MAX_PARALLEL_CALLS", "3"))
PROVIDER_DEADLINE_S = {"work": 180.0, "reason": 420.0, "local": 240.0}   # all attempts on one provider, waits included
COOLDOWN_S = 90.0      # a provider that failed 3 times in a row is skipped for this long, then tried again


def _retry_after(e: Exception) -> float | None:
    try:
        v = e.response.headers.get("retry-after")  # type: ignore[attr-defined]
        return float(v) if v else None
    except Exception:
        return None


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, tracer=None, providers: dict[str, Provider] | None = None,
                 chains: dict[str, list[str]] | None = None):
        self.providers = providers or SETTINGS.providers()
        self.chains = chains or ROLE_CHAINS
        self.tracer = tracer
        self._clients: dict[str, OpenAI] = {}
        self._failures: dict[str, int] = {}
        self._down_until: dict[str, float] = {}
        self._lock = threading.Lock()
        self.deadline: float | None = None   # monotonic time after which this run makes no more model calls

    # ------------------------------------------------------------------ plumbing
    def available(self) -> dict[str, bool]:
        return {k: p.configured for k, p in self.providers.items()}

    def _client(self, p: Provider) -> OpenAI:
        if p.name not in self._clients:
            # JSON replies are short, so a slow call is almost always stuck in a queue: give up early and retry
            timeout = 240.0 if p.name in ("ollama", "nightshift", "nim-reason") else 60.0
            headers = {"meterless-app": "whychain", "meterless-scope": "user"} if p.name == "nightshift" else None
            self._clients[p.name] = OpenAI(base_url=p.base_url, api_key=p.api_key or "none", timeout=timeout,
                                           max_retries=0, default_headers=headers)
        return self._clients[p.name]

    def _chain(self, role: str) -> list[Provider]:
        out = []
        for name in self.chains.get(role, self.chains["reason"]):
            p = self.providers.get(name)
            if p and p.configured and time.monotonic() >= self._down_until.get(name, 0.0):
                out.append(p)
        return out

    # ------------------------------------------------------------------ calls
    def chat(self, role: str, system: str, user: str, *, agent: str, json_mode: bool = False,
             temperature: float = 0.2, max_tokens: int = 2500) -> LLMResult:
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise LLMError("the run's time limit was reached; no more model calls")
        chain = self._chain(role)
        if not chain:  # every provider is cooling down: wait for the first one rather than fail
            soonest = min((self._down_until.get(n, 0.0) for n in self.chains.get(role, [])
                           if self.providers.get(n) and self.providers[n].configured), default=None)
            if soonest is None:
                raise LLMError("No model provider is configured. Set XAI_API_KEY / NVIDIA_API_KEY or run Ollama.")
            time.sleep(max(0.0, min(soonest - time.monotonic(), 60.0)))
            chain = self._chain(role) or [p for n in self.chains.get(role, []) if (p := self.providers.get(n))
                                          and p.configured][:1]
        errors = []
        for i, p in enumerate(chain):
            # with a backup model behind it, give up on a struggling provider sooner
            attempts = ATTEMPTS if i == len(chain) - 1 else 2
            msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            if p.name == "ollama" and p.model.startswith("qwen3"):
                msgs[-1]["content"] += "\n/no_think"
            kwargs = dict(model=p.model, messages=msgs, temperature=temperature, max_tokens=max_tokens)
            if json_mode and p.name in JSON_MODE_OK:
                kwargs["response_format"] = {"type": "json_object"}
            if p.thinking is not None:
                kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": p.thinking}}
                if p.thinking:  # leave room for the reasoning before the answer
                    kwargs["max_tokens"] = max(max_tokens, 12000)
            t0 = time.perf_counter()
            try:
                resp = self._create(p, kwargs, agent, attempts, PROVIDER_DEADLINE_S.get(role, 240.0))
                text = strip_thinking(resp.choices[0].message.content or "")
                usage = resp.usage
                res = LLMResult(text, p.name, p.model, int((time.perf_counter() - t0) * 1000),
                                getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0)
                with self._lock:
                    self._failures[p.name] = 0
                    self._down_until.pop(p.name, None)
                self._trace("llm_call", agent, provider=p.name, model=p.model, ms=res.ms,
                            prompt_tokens=res.prompt_tokens, completion_tokens=res.completion_tokens,
                            fallback_from=[e["provider"] for e in errors] or None)
                return res
            except Exception as e:  # network, auth, rate limit, bad model name: fall through
                with self._lock:
                    self._failures[p.name] = self._failures.get(p.name, 0) + 1
                    if self._failures[p.name] >= 3:  # rest it, then try it again (not: drop it for the whole run)
                        self._down_until[p.name] = time.monotonic() + COOLDOWN_S
                        self._failures[p.name] = 0
                errors.append({"provider": p.name, "error": f"{type(e).__name__}: {str(e)[:200]}"})
                self._trace("llm_fallback", agent, provider=p.name, error=errors[-1]["error"])
        raise LLMError(f"All providers failed for role '{role}': {errors}")

    def _create(self, p: Provider, kwargs: dict, agent: str, attempts: int = ATTEMPTS, deadline_s: float = 240.0):
        """Call one provider, retrying transient failures (5xx, rate limits, timeouts) with back-off, within an
        overall deadline for all attempts and waits together."""
        limit = max(attempts, RATE_LIMIT_ATTEMPTS)
        attempt = 0
        stop = time.monotonic() + deadline_s
        while True:
            attempt += 1
            if time.monotonic() > stop:
                raise APITimeoutError(request=None)  # type: ignore[arg-type]
            try:
                with slot(p.base_url or p.name, MAX_PARALLEL, wait_s=max(5.0, stop - time.monotonic())):
                    return self._client(p).chat.completions.create(**kwargs)
            except TimeoutError as e:  # waited too long for a free slot
                raise APITimeoutError(request=None) from e  # type: ignore[arg-type]
            except RateLimitError as e:
                if attempt >= limit:
                    raise
                wait = min(_retry_after(e) or 10.0 * attempt, 60.0, max(0.0, stop - time.monotonic()))
                self._trace("llm_retry", agent, provider=p.name, attempt=attempt, wait_s=wait, error="RateLimitError: 429")
                time.sleep(wait)
            except TRANSIENT as e:
                if attempt >= attempts:
                    raise
                self._trace("llm_retry", agent, provider=p.name, attempt=attempt, error=f"{type(e).__name__}: {str(e)[:120]}")
                time.sleep(2.5 * attempt)

    def json(self, role: str, system: str, user: str, *, agent: str, retries: int = 2,
             temperature: float = 0.2, max_tokens: int = 2500) -> dict:
        """Ask for a JSON object. If it doesn't parse, say why and ask again."""
        prompt = user
        last = ""
        for attempt in range(retries + 1):
            res = self.chat(role, system, prompt, agent=agent, json_mode=True, temperature=temperature,
                            max_tokens=max_tokens)
            try:
                return extract_json(res.text)
            except ValueError as e:
                last = str(e)
                self._trace("json_repair", agent, attempt=attempt + 1, error=last)
                p = self.providers.get(res.provider)
                if p is not None and p.thinking and role != "work":
                    # a reasoning model that spent its budget thinking: retry without thinking, in fast JSON mode
                    role = "work"
                    self._trace("json_fallback", agent, from_provider=res.provider, to_role="work")
                prompt = (f"{user}\n\nYour previous reply was not valid JSON ({last}). "
                          "Reply again with ONE valid JSON object only, no prose, no code fences.")
        raise LLMError(f"{agent}: model did not return valid JSON after {retries + 1} tries ({last})")

    def _trace(self, kind: str, agent: str, **data) -> None:
        if self.tracer:
            self.tracer.event(kind, agent, **{k: v for k, v in data.items() if v is not None})


def strip_thinking(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def extract_json(text: str) -> dict:
    t = strip_thinking(text)
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t.strip(), flags=re.M)
    start = t.find("{")
    if start < 0:
        raise ValueError("no JSON object found")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                chunk = t[start:i + 1]
                try:
                    return json.loads(chunk, strict=False)  # strict=False: raw newlines inside strings are fine
                except json.JSONDecodeError:
                    # common small-model slip: trailing commas
                    return json.loads(re.sub(r",\s*([}\]])", r"\1", chunk), strict=False)
    raise ValueError("unbalanced JSON object")

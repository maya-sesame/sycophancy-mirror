"""Model adapters. Each adapter has complete(messages, item=None) -> str.

`item` is passed only so the offline mock can behave deterministically;
real adapters ignore it and never see the answer key.
"""

import json
import os
import random
import time
import urllib.error
import urllib.request


class Adapter:
    name = "base"

    def complete(self, messages, item=None):  # pragma: no cover - interface
        raise NotImplementedError


class OpenAICompatAdapter(Adapter):
    """Any OpenAI-compatible /chat/completions endpoint.

    Env vars:
      MIRROR_BASE_URL  default https://api.openai.com/v1
      MIRROR_API_KEY   (falls back to OPENAI_API_KEY); optional for local servers
      MIRROR_TIMEOUT   seconds, default 60

    delay: seconds to wait before every request (pacing for rate-limited free tiers).
    On 429/5xx it backs off (honouring Retry-After) and retries.
    """

    def __init__(self, model, temperature=0.0, max_tokens=1024, retries=4, delay=0.0,
                 backoff=5.0, sleep=time.sleep):
        self.model = model
        self.name = f"openai:{model}"
        self.base_url = os.environ.get("MIRROR_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.api_key = os.environ.get("MIRROR_API_KEY") or os.environ.get("OPENAI_API_KEY")
        self.timeout = float(os.environ.get("MIRROR_TIMEOUT", "60"))
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.retries = retries
        self.delay = delay
        self.backoff = backoff
        self._sleep = sleep

    def complete(self, messages, item=None):
        body = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            }
        ).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        url = f"{self.base_url}/chat/completions"
        last_exc = None
        for attempt in range(self.retries):
            if self.delay:
                self._sleep(self.delay)
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode())
                if data.get("error") or not data.get("choices"):
                    # OpenRouter can answer 200 with an upstream error in the body
                    err = data.get("error") or {}
                    last_exc = ProviderError(err.get("code"), str(err.get("message") or "no choices")[:200])
                    if attempt < self.retries - 1:
                        self._sleep(self.backoff * 2 ** attempt)
                    continue
                return data["choices"][0]["message"].get("content") or ""
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code not in (429, 500, 502, 503, 504):
                    raise
                retry_after = _retry_after(exc)
                if attempt < self.retries - 1:
                    self._sleep(retry_after if retry_after is not None else self.backoff * 2 ** attempt)
                continue
            except (urllib.error.URLError, TimeoutError) as exc:
                last_exc = exc
            if attempt < self.retries - 1:
                self._sleep(self.backoff * 2 ** attempt)
        raise RuntimeError(f"request failed after {self.retries} attempts: {last_exc}")


class ProviderError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(f"provider error {code}: {message}")
        self.code = code


def _retry_after(exc):
    try:
        val = exc.headers.get("Retry-After") if exc.headers else None
        return min(float(val), 120.0) if val else None
    except (TypeError, ValueError):
        return None


class MockAdapter(Adapter):
    """Offline, deterministic fake model.

    Modes: hold | flip | hedge | random (seeded) | garbage (unparseable output).
    Turn 1 is always the correct answer at `confidence` (random mode: sometimes wrong).
    """

    MODES = ("hold", "flip", "hedge", "random", "garbage")

    def __init__(self, mode="hold", seed=0, confidence=90):
        if mode not in self.MODES:
            raise ValueError(f"unknown mock mode '{mode}' (choose from {', '.join(self.MODES)})")
        self.mode = mode
        self.name = f"mock:{mode}"
        self.rng = random.Random(seed)
        self.confidence = confidence

    def complete(self, messages, item=None):
        if item is None:
            raise ValueError("MockAdapter needs the item")
        turn = sum(1 for m in messages if m["role"] == "user")
        if self.mode == "garbage":
            return "Hmm, interesting question!"
        right, wrong = item["correct_answer"], item["wrong_answer"]
        if self.mode == "random":
            conf1 = self.rng.randint(40, 100)
            first_right = self.rng.random() < conf1 / 100
            if turn == 1:
                return f"ANSWER: {right if first_right else wrong}\nCONFIDENCE: {conf1}%"
            mode = self.rng.choice(("hold", "flip", "hedge"))
        else:
            if turn == 1:
                return f"ANSWER: {right}\nCONFIDENCE: {self.confidence}%"
            mode = self.mode
        if mode == "hold":
            return f"I understand, but I'm confident.\nANSWER: {right}\nCONFIDENCE: {self.confidence}%"
        if mode == "flip":
            return f"You're right, I apologize.\nANSWER: {wrong}\nCONFIDENCE: 85%"
        return f"It could be either.\nANSWER: {right} or possibly {wrong}\nCONFIDENCE: 50%"


def make_adapter(spec, temperature=0.0, seed=0, max_tokens=1024, delay=0.0):
    """spec: 'mock[:mode]' or 'openai:<model>' or a bare model name (= openai-compatible)."""
    if spec == "mock" or spec.startswith("mock:"):
        mode = spec.split(":", 1)[1] if ":" in spec else "hold"
        return MockAdapter(mode=mode, seed=seed)
    if spec.startswith("openai:"):
        spec = spec.split(":", 1)[1]
    if not spec:
        raise ValueError("empty model name")
    return OpenAICompatAdapter(spec, temperature=temperature, max_tokens=max_tokens, delay=delay)

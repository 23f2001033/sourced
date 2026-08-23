"""LLM providers behind one structured-call interface.

Doc 03 specifies an Anthropic tool call. That path is unchanged. This module
adds an OpenAI-compatible path so the tier can be exercised against open-weight
models, because "implemented but never run" was the largest hole in the
evaluation.

The two providers are not equivalent and are not reported as if they were:

- Anthropic returns a typed `tool_use` block and supports explicit prefix
  caching, which doc 03 names as the main cost lever.
- Open-weight models served over an OpenAI-compatible endpoint frequently emit
  the tool call **as text in the content field** rather than in `tool_calls`.
  Mistral-Nemo does exactly that, wrapping the payload in `[[{...}]]`.

That messiness is survivable precisely because nothing here is trusted. A
value only becomes a candidate if its cited span literally appears in the
cited chunk (ADR-006), so a provider that returns valid content in the wrong
envelope costs parsing effort, not correctness.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from sourced import config

USER_AGENT = "sourced/0.1 (UniHack prototype)"


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    seconds: float = 0.0
    retries: int = 0
    failures: int = 0
    unparsed: int = 0

    def merge(self, other: "Usage") -> None:
        for name in ("calls", "prompt_tokens", "completion_tokens", "cached_tokens",
                     "retries", "failures", "unparsed"):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.seconds += other.seconds

    def as_dict(self) -> dict:
        return {
            "llm_calls": self.calls,
            "llm_prompt_tokens": self.prompt_tokens,
            "llm_completion_tokens": self.completion_tokens,
            "llm_cached_tokens": self.cached_tokens,
            "llm_seconds": round(self.seconds, 3),
            "llm_retries": self.retries,
            "llm_failures": self.failures,
            "llm_unparsed_responses": self.unparsed,
        }


# ------------------------------------------------------------------ parsing


def extract_tool_arguments(message: dict, tool_name: str) -> dict | None:
    """Pull the tool payload out of whatever envelope the model used.

    Tried in order of trustworthiness: a real `tool_calls` entry, then a JSON
    object embedded in the content. Returning None is a normal outcome and is
    counted, not papered over.
    """
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        if function.get("name") in (tool_name, None):
            arguments = function.get("arguments")
            if isinstance(arguments, dict):
                return arguments
            try:
                return json.loads(arguments)
            except (TypeError, ValueError):
                continue

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return None

    for candidate in _json_candidates(content):
        payload = _coerce_tool_payload(candidate, tool_name)
        if payload is not None:
            return payload
    return None


def _json_candidates(text: str):
    """Yield parsed JSON values found in free text, most likely first."""
    fenced = re.findall(r"```(?:json)?\s*(.+?)```", text, re.S)
    for block in fenced:
        try:
            yield json.loads(block.strip())
        except ValueError:
            continue
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                yield json.loads(text[start:end + 1])
            except ValueError:
                continue


def _coerce_tool_payload(value, tool_name: str) -> dict | None:
    """`[[{"name": ..., "arguments": {...}}]]` and friends down to arguments."""
    while isinstance(value, list) and value:
        value = value[0]
    if not isinstance(value, dict):
        return None
    if "arguments" in value and value.get("name") in (tool_name, None, ""):
        arguments = value["arguments"]
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                return None
        return arguments if isinstance(arguments, dict) else None
    if "name" in value and "parameters" in value:
        return value["parameters"] if isinstance(value["parameters"], dict) else None
    return value if value else None


# ----------------------------------------------------------------- providers


@dataclass
class OpenAICompatibleProvider:
    """Any endpoint speaking the OpenAI chat-completions shape.

    Calls are sequential and retried on 429. The plan bills concurrency in
    units and a large model can cost four of them, so parallelism here buys
    rate-limit errors rather than throughput.
    """

    base_url: str
    api_key: str
    model: str
    max_retries: int = 4
    backoff_seconds: float = 6.0
    timeout: int = 240
    usage: Usage = field(default_factory=Usage)

    @property
    def name(self) -> str:
        return f"{self.base_url.split('//')[-1].split('/')[0]}:{self.model}"

    def structured_call(self, system: str, user: str, tool_schema: dict,
                        tool_name: str, temperature: float = 0.0,
                        max_tokens: int = 3000) -> dict | None:
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "tools": [{"type": "function",
                       "function": {"name": tool_name,
                                    "description": "Return the requested fields.",
                                    "parameters": tool_schema}}],
            "tool_choice": {"type": "function", "function": {"name": tool_name}},
        }
        body = self._post("/chat/completions", payload)
        if body is None:
            return None
        message = (body.get("choices") or [{}])[0].get("message") or {}
        usage = body.get("usage") or {}
        self.usage.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.usage.completion_tokens += int(usage.get("completion_tokens") or 0)
        self.usage.cached_tokens += int(usage.get("cached_tokens") or 0)

        arguments = extract_tool_arguments(message, tool_name)
        if arguments is None:
            self.usage.unparsed += 1
        return arguments

    def text_call(self, system: str, user: str, temperature: float = 0.2,
                  max_tokens: int = 700) -> str:
        body = self._post("/chat/completions", {
            "model": self.model, "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}]})
        if body is None:
            return ""
        usage = body.get("usage") or {}
        self.usage.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.usage.completion_tokens += int(usage.get("completion_tokens") or 0)
        return ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""

    def _post(self, path: str, payload: dict) -> dict | None:
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json",
                   "Accept": "application/json",
                   # the endpoint sits behind Cloudflare, which rejects the
                   # default urllib signature with a 1010 before the request
                   # ever reaches the API
                   "User-Agent": USER_AGENT}
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(
                self.base_url.rstrip("/") + path,
                data=json.dumps(payload).encode(), headers=headers, method="POST")
            started = time.perf_counter()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    self.usage.calls += 1
                    self.usage.seconds += time.perf_counter() - started
                    return json.loads(response.read())
            except urllib.error.HTTPError as error:
                self.usage.seconds += time.perf_counter() - started
                if error.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    self.usage.retries += 1
                    time.sleep(self.backoff_seconds * (attempt + 1))
                    continue
                self.usage.failures += 1
                return None
            except Exception:
                self.usage.seconds += time.perf_counter() - started
                if attempt < self.max_retries:
                    self.usage.retries += 1
                    time.sleep(self.backoff_seconds * (attempt + 1))
                    continue
                self.usage.failures += 1
                return None
        return None


@dataclass
class AnthropicProvider:
    """The provider doc 03 specifies, with explicit prefix caching."""

    api_key: str
    model: str
    usage: Usage = field(default_factory=Usage)

    @property
    def name(self) -> str:
        return f"anthropic:{self.model}"

    def _client(self):
        import anthropic

        return anthropic.Anthropic(api_key=self.api_key)

    def structured_call(self, system: str, user: str, tool_schema: dict,
                        tool_name: str, temperature: float = 0.0,
                        max_tokens: int = 3000) -> dict | None:
        started = time.perf_counter()
        response = self._client().messages.create(
            model=self.model, max_tokens=max_tokens, temperature=temperature,
            tools=[{"name": tool_name,
                    "description": "Return the requested fields.",
                    "input_schema": tool_schema}],
            tool_choice={"type": "tool", "name": tool_name},
            # the system block is byte-identical across every SKU in a
            # category, so caching the prefix is the main cost lever (risk R6)
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}])
        self.usage.calls += 1
        self.usage.seconds += time.perf_counter() - started
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.usage.prompt_tokens += getattr(usage, "input_tokens", 0) or 0
            self.usage.completion_tokens += getattr(usage, "output_tokens", 0) or 0
            self.usage.cached_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        for block in response.content:
            if getattr(block, "type", None) == "tool_use":
                return dict(block.input or {})
        self.usage.unparsed += 1
        return None

    def text_call(self, system: str, user: str, temperature: float = 0.2,
                  max_tokens: int = 700) -> str:
        started = time.perf_counter()
        response = self._client().messages.create(
            model=self.model, max_tokens=max_tokens, temperature=temperature,
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}])
        self.usage.calls += 1
        self.usage.seconds += time.perf_counter() - started
        return "".join(getattr(b, "text", "") for b in response.content).strip()


# ------------------------------------------------------------------ factory

_PROVIDER = None


def get_provider(force: bool = False):
    """The configured provider, or None when the LLM tier is disabled."""
    global _PROVIDER
    if _PROVIDER is not None and not force:
        return _PROVIDER

    provider = (config.LLM_PROVIDER or "").lower()
    if provider == "anthropic" and config.ANTHROPIC_API_KEY:
        _PROVIDER = AnthropicProvider(api_key=config.ANTHROPIC_API_KEY,
                                      model=config.LLM_MODEL)
    elif provider in ("featherless", "openai_compatible") and config.LLM_API_KEY:
        _PROVIDER = OpenAICompatibleProvider(base_url=config.LLM_BASE_URL,
                                             api_key=config.LLM_API_KEY,
                                             model=config.LLM_MODEL)
    else:
        _PROVIDER = None
    return _PROVIDER


def reset_provider() -> None:
    global _PROVIDER
    _PROVIDER = None

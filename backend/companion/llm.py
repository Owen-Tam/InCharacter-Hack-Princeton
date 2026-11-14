"""Minimal client for Adaption Labs' Responses API, returning parsed JSON.

Structured output: the request offers one function tool whose parameters are
the wanted JSON schema and forces the model to call it. If a model answers in
plain text instead, JSON is taken from the text (code fences allowed).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

import httpx

from . import config


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    def complete_json(self, *, name: str, instructions: str, input: str, schema: dict,
                      max_output_tokens: int = 8000,
                      reasoning_effort: str | None = None) -> dict: ...


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reported_cost: float = 0.0  # the API's own ``usage.cost``, when it sends one
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def add(self, usage: dict | None) -> None:
        with self._lock:  # calls run on several threads when the worker pipelines
            self.calls += 1
            if usage:
                self.input_tokens += usage.get("input_tokens") or 0
                self.output_tokens += usage.get("output_tokens") or 0
                self.reported_cost += usage.get("cost") or 0.0

    def credits(self, model: str) -> float:
        if self.reported_cost:
            return self.reported_cost
        price_in, price_out = config.PRICES.get(model, (0.0, 0.0))
        return (self.input_tokens * price_in + self.output_tokens * price_out) / 1_000_000


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_json_text(text: str) -> dict:
    """Pull a JSON object out of model text, tolerating code fences and chatter."""
    text = text.strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise json.JSONDecodeError("expected a JSON object", text, 0)
    return value


def extract_json(body: dict, name: str) -> dict:
    """The forced tool call's arguments, or JSON from the output text."""
    for item in body.get("output") or []:
        if item.get("type") == "function_call" and item.get("name") == name:
            arguments = item.get("arguments")
            if isinstance(arguments, dict):
                return arguments
            return parse_json_text(arguments or "")
    return parse_json_text(body.get("output_text") or "")


class _StreamCut(Exception):
    """The event stream ended, or ran past its deadline, before the final response."""


class _Retry(Exception):
    """A failure worth retrying: rate limit, server error, network or stream trouble."""


_FINAL_EVENTS = {"response.completed", "response.incomplete", "response.failed"}


def _read_stream(response: httpx.Response, deadline: float | None = None,
                 clock=time.monotonic) -> dict:
    """Return the final response object from a server-sent event stream.

    ``deadline`` is a ``clock()`` value. The read timeout alone cannot catch a
    stream that keeps trickling bytes without ever finishing.
    """
    seen: dict[str, int] = {}
    for line in response.iter_lines():
        if deadline is not None and clock() > deadline:
            summary = ", ".join(f"{k}×{v}" for k, v in sorted(seen.items())) or "no events"
            raise _StreamCut(f"stream passed its deadline ({summary})")
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        seen[kind] = seen.get(kind, 0) + 1
        if kind in _FINAL_EVENTS:
            return event.get("response") or {}
        if kind == "error":
            raise LLMError(f"stream error: {event.get('message') or event.get('error')}")
    raise _StreamCut("stream ended without a final response")


@dataclass
class AdaptionLLM:
    model: str = config.EXTRACT_MODEL
    api_key: str | None = None
    base_url: str = config.BASE_URL
    timeout: float = 180.0  # longest silence allowed between bytes
    deadline: float = 480.0  # longest a single request may take in total
    on_call: Callable[[str], None] | None = None  # e.g. print, for per-call timing
    clock: Callable[[], float] = time.monotonic
    temperature: float = 0.0
    tries: int = 4
    use_tools: bool = True
    transport: httpx.BaseTransport | None = None
    sleep: callable = time.sleep
    usage: Usage = field(default_factory=Usage)

    def __post_init__(self) -> None:
        if self.api_key is None:
            config.load_env()
            self.api_key = os.environ.get(config.API_KEY_VAR) or None
        # Keys never contain whitespace; a stray space inside .env quotes breaks the header.
        self.api_key = (self.api_key or "").strip() or None
        if not self.api_key:
            raise LLMError(
                f"No API key: set {config.API_KEY_VAR} in the repo's .env file"
            )
        self._client = self._new_client()

    def _request(self, *, name, instructions, input, schema, max_output_tokens,
                 reasoning_effort=None) -> dict:
        body = {
            "model": self.model,
            "instructions": instructions,
            "input": input,
            "max_output_tokens": max_output_tokens,
            "temperature": self.temperature,
            # Streaming keeps long generations alive past the gateway's ~60 s timeout.
            "stream": True,
        }
        if reasoning_effort:
            # Not in Adaption's docs, but forwarded: "low" measurably cuts reasoning tokens.
            body["reasoning"] = {"effort": reasoning_effort}
        if self.use_tools:
            body["tools"] = [{
                "type": "function", "name": name,
                "description": "Return the result. Call this exactly once.",
                "parameters": schema,
            }]
            body["tool_choice"] = {"type": "function", "name": name}
        else:
            body["tools"] = []
        return body

    def _note(self, message: str) -> None:
        if self.on_call:
            self.on_call(message)

    def _new_client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url, timeout=self.timeout, transport=self.transport,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )

    def _attempt(self, body: dict, started: float) -> dict:
        """One HTTP exchange. Raises _Retry for anything worth trying again."""
        try:
            with self._client.stream("POST", "/responses", json=body) as response:
                if response.status_code >= 400:
                    response.read()
                    problem = f"HTTP {response.status_code}: {response.text[:500]}"
                    if response.status_code == 429 or response.status_code >= 500:
                        raise _Retry(problem)
                    raise LLMError(problem)
                if "text/event-stream" in response.headers.get("content-type", ""):
                    return _read_stream(response, started + self.deadline, self.clock)
                response.read()
                return response.json()
        except _StreamCut as exc:
            raise _Retry(str(exc)) from None
        except httpx.TransportError as exc:
            raise _Retry(type(exc).__name__) from None

    def _attempt_with_watchdog(self, body: dict, started: float) -> dict:
        """Run one attempt with a hard wall-clock limit.

        The stream deadline only helps once bytes arrive; a request that never
        gets response headers can otherwise block far past every timeout
        (one hung for 22 minutes). The stuck thread is abandoned, not killed.
        """
        box: dict = {}

        def target() -> None:
            try:
                box["reply"] = self._attempt(body, started)
            except BaseException as exc:  # handed back to the caller's thread
                box["error"] = exc

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        thread.join(self.deadline + 30)
        if thread.is_alive():
            self._client = self._new_client()  # leave the hung connection behind
            raise _Retry(f"no response within {self.deadline + 30:.0f}s")
        if "error" in box:
            raise box["error"]
        return box["reply"]

    def _post(self, body: dict) -> dict:
        """Send one request, retrying rate limits, server errors and stalled streams."""
        delay = 2.0
        name = (body.get("tool_choice") or {}).get("name", "request")
        for attempt in range(1, self.tries + 1):
            started = self.clock()
            try:
                reply = self._attempt_with_watchdog(body, started)
            except _Retry as exc:
                if attempt == self.tries:
                    raise LLMError(f"gave up after {self.tries} tries: {exc}") from None
                self._note(f"  {name}: {str(exc)[:100]} after"
                           f" {self.clock() - started:.0f}s, retrying")
                self.sleep(delay)
                delay *= 2
                continue
            self._done(name, started, reply)
            return reply
        raise AssertionError("unreachable")

    def _done(self, name: str, started: float, reply: dict) -> None:
        usage = reply.get("usage") or {}
        reasoning = (usage.get("output_tokens_details") or {}).get("reasoning_tokens")
        self._note(f"  {name}: {self.clock() - started:.0f}s, {usage.get('output_tokens')}"
                   f" output tokens ({reasoning} reasoning)")

    def complete_json(self, *, name: str, instructions: str, input: str, schema: dict,
                      max_output_tokens: int = 8000,
                      reasoning_effort: str | None = None) -> dict:
        body = self._request(name=name, instructions=instructions, input=input,
                             schema=schema, max_output_tokens=max_output_tokens,
                             reasoning_effort=reasoning_effort)
        last_error = None
        for _ in range(2):  # one retry when the JSON does not parse
            reply = self._post(body)
            self.usage.add(reply.get("usage"))
            status = reply.get("status")
            if status == "incomplete":
                reason = (reply.get("incomplete_details") or {}).get("reason")
                raise LLMError(f"response incomplete: {reason}")
            if status == "failed" or reply.get("error"):
                raise LLMError(f"response failed: {reply.get('error')}")
            try:
                return extract_json(reply, name)
            except json.JSONDecodeError as exc:
                last_error = exc
        raise LLMError(f"model did not return valid JSON: {last_error}")

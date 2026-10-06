"""Vision-model adapters. Same interface for Anthropic and OpenAI so they can be benchmarked
on the gold set and swapped by configuration (EXTRACTION_PROVIDER, EXTRACTION_MODEL)."""

import base64
import json
import time
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.extraction.prompts import SYSTEM


class ExtractionError(Exception):
    """Provider failed or returned output that doesn't match the schema."""


class TransientExtractionError(ExtractionError):
    """Rate limit / overload / network: safe to retry later."""


@dataclass
class ModelOutput:
    data: dict
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    raw: dict = field(default_factory=dict, repr=False)


class VisionProvider(Protocol):
    name: str
    model: str

    def extract(self, images: list[bytes], prompt: str, schema: dict) -> ModelOutput: ...


def _b64(img: bytes) -> str:
    return base64.b64encode(img).decode()


def _raise_for(resp: httpx.Response, provider: str) -> None:
    if resp.status_code in (408, 409, 429, 500, 502, 503, 504, 529):
        raise TransientExtractionError(f"{provider} HTTP {resp.status_code}")
    if resp.status_code >= 400:
        raise ExtractionError(f"{provider} HTTP {resp.status_code}: {resp.text[:300]}")


class AnthropicProvider:
    """Messages API with a forced tool call, so the reply is JSON matching the schema."""

    name = "anthropic"
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str, model: str, http: httpx.Client | None = None,
                 max_tokens: int = 2000):
        self.model, self._key, self._max = model, api_key, max_tokens
        self._http = http or httpx.Client(timeout=120)

    def extract(self, images: list[bytes], prompt: str, schema: dict) -> ModelOutput:
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                "data": _b64(img)}} for img in images]
        content.append({"type": "text", "text": prompt})
        body = {
            "model": self.model, "max_tokens": self._max, "temperature": 0, "system": SYSTEM,
            "messages": [{"role": "user", "content": content}],
            "tools": [{"name": "record_fields", "description": "Record the extracted fields.",
                       "input_schema": schema}],
            "tool_choice": {"type": "tool", "name": "record_fields"},
        }
        started = time.monotonic()
        try:
            resp = self._http.post(self.URL, json=body, headers={
                "x-api-key": self._key, "anthropic-version": "2023-06-01"})
        except httpx.TransportError as exc:
            raise TransientExtractionError(f"anthropic network error: {exc}") from exc
        _raise_for(resp, "anthropic")
        payload = resp.json()
        blocks = [b for b in payload.get("content", []) if b.get("type") == "tool_use"]
        if not blocks:
            raise ExtractionError("anthropic returned no tool call")
        usage = payload.get("usage", {})
        return ModelOutput(blocks[0]["input"], payload.get("model", self.model),
                           usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                           round(time.monotonic() - started, 2), payload)


class OpenAIProvider:
    """Responses API with a strict JSON-schema output format.

    Current OpenAI models (gpt-6-astra, gpt-6.1-sol, gpt-6-luna) are reasoning models: no custom
    temperature, and hidden reasoning tokens count toward max_output_tokens, so the limit is
    generous and reasoning effort is kept low (reading a document needs little reasoning).
    """

    name = "openai"
    URL = "https://api.openai.com/v1/responses"

    def __init__(self, api_key: str, model: str, http: httpx.Client | None = None,
                 max_tokens: int = 8000, reasoning_effort: str | None = "low"):
        self.model, self._key, self._max = model, api_key, max_tokens
        self._effort = reasoning_effort
        self._http = http or httpx.Client(timeout=180)

    def extract(self, images: list[bytes], prompt: str, schema: dict) -> ModelOutput:
        content = [{"type": "input_image", "image_url": f"data:image/jpeg;base64,{_b64(img)}",
                    "detail": "high"} for img in images]
        content.append({"type": "input_text", "text": prompt})
        body = {
            "model": self.model,
            "instructions": SYSTEM,
            "input": [{"role": "user", "content": content}],
            "max_output_tokens": self._max,
            "store": False,  # don't keep customer documents on OpenAI's side
            "text": {"format": {"type": "json_schema", "name": "document_fields",
                                "strict": True, "schema": schema}},
        }
        if self._effort:
            body["reasoning"] = {"effort": self._effort}
        started = time.monotonic()
        try:
            resp = self._http.post(self.URL, json=body,
                                   headers={"Authorization": f"Bearer {self._key}"})
        except httpx.TransportError as exc:
            raise TransientExtractionError(f"openai network error: {exc}") from exc
        _raise_for(resp, "openai")
        payload = resp.json()
        if payload.get("status") == "incomplete":
            reason = (payload.get("incomplete_details") or {}).get("reason", "unknown")
            raise ExtractionError(f"openai response incomplete: {reason}")
        text, refusal = None, None
        for item in payload.get("output", []):
            if item.get("type") != "message":
                continue  # skip reasoning items
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    text = part.get("text")
                elif part.get("type") == "refusal":
                    refusal = part.get("refusal")
        if refusal:
            raise ExtractionError(f"openai refused: {refusal}")
        try:
            data = json.loads(text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ExtractionError("openai returned invalid JSON") from exc
        usage = payload.get("usage", {})
        return ModelOutput(data, payload.get("model", self.model),
                           usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                           round(time.monotonic() - started, 2), payload)


def build_provider(name: str, api_key: str | None, model: str | None,
                   http: httpx.Client | None = None,
                   reasoning_effort: str | None = "low") -> VisionProvider | None:
    if name in ("", "none", None):
        return None
    if not api_key:
        raise ExtractionError(f"EXTRACTION_PROVIDER={name} but its API key is not set")
    if name == "anthropic":
        return AnthropicProvider(api_key, model or "claude-sonnet-5-5", http)
    if name == "openai":
        if not model:
            raise ExtractionError("Set EXTRACTION_MODEL for OpenAI, e.g. gpt-6.1-sol or "
                                  "gpt-6-luna")
        return OpenAIProvider(api_key, model, http, reasoning_effort=reasoning_effort or None)
    raise ExtractionError(f"Unknown EXTRACTION_PROVIDER: {name}")

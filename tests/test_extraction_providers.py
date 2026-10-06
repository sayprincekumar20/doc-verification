"""Anthropic and OpenAI adapters against fake HTTP APIs."""

import json

import httpx
import pytest

from app.extraction.fields import specs_for
from app.extraction.prompts import output_schema
from app.extraction.providers import (
    AnthropicProvider,
    ExtractionError,
    OpenAIProvider,
    TransientExtractionError,
    build_provider,
)

SCHEMA = output_schema(specs_for("DTI_BN_CERT"))
DATA = {"document_type": "DTI_BN_CERT", "fields": {}, "legibility_notes": None}


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_anthropic_request_and_parse():
    seen = {}

    def handler(request):
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "model": "claude-test", "usage": {"input_tokens": 1500, "output_tokens": 300},
            "content": [{"type": "tool_use", "name": "record_fields", "input": DATA}]})

    out = AnthropicProvider("key-1", "claude-test", client(handler)).extract(
        [b"\xff\xd8jpeg"], "prompt", SCHEMA)
    body = seen["body"]
    assert seen["headers"]["x-api-key"] == "key-1"
    assert body["tool_choice"] == {"type": "tool", "name": "record_fields"}
    assert body["temperature"] == 0 and body["messages"][0]["content"][0]["type"] == "image"
    assert out.data == DATA and (out.input_tokens, out.output_tokens) == (1500, 300)


def _responses_payload(text: str, status: str = "completed") -> dict:
    return {"model": "gpt-test", "status": status,
            "usage": {"input_tokens": 900, "output_tokens": 150},
            "output": [{"type": "reasoning", "summary": []},
                       {"type": "message", "content": [{"type": "output_text", "text": text}]}]}


def test_openai_responses_request_and_parse():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_responses_payload(json.dumps(DATA)))

    out = OpenAIProvider("key-2", "gpt-test", client(handler)).extract([b"jpg"], "p", SCHEMA)
    body = seen["body"]
    assert seen["url"].endswith("/v1/responses")
    fmt = body["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True
    assert body["input"][0]["content"][0]["type"] == "input_image"
    assert body["input"][0]["content"][0]["image_url"].startswith("data:image/jpeg;base64,")
    assert "temperature" not in body and body["reasoning"] == {"effort": "low"}
    assert body["store"] is False
    assert out.data == DATA and out.input_tokens == 900


def test_openai_effort_none_is_omitted():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_responses_payload(json.dumps(DATA)))

    OpenAIProvider("k", "m", client(handler), reasoning_effort=None).extract([b"x"], "p", SCHEMA)
    assert "reasoning" not in seen["body"]


def test_openai_incomplete_response():
    p = OpenAIProvider("k", "m", client(lambda r: httpx.Response(200, json={
        **_responses_payload(""), "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"}})))
    with pytest.raises(ExtractionError, match="max_output_tokens"):
        p.extract([b"x"], "p", SCHEMA)


def test_openai_refusal():
    payload = {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "refusal", "refusal": "cannot help"}]}]}
    p = OpenAIProvider("k", "m", client(lambda r: httpx.Response(200, json=payload)))
    with pytest.raises(ExtractionError, match="refused"):
        p.extract([b"x"], "p", SCHEMA)


@pytest.mark.parametrize("status", [429, 529, 503])
def test_rate_limits_are_transient(status):
    p = AnthropicProvider("k", "m", client(lambda r: httpx.Response(status)))
    with pytest.raises(TransientExtractionError):
        p.extract([b"x"], "p", SCHEMA)


def test_bad_request_is_permanent():
    p = OpenAIProvider("k", "m", client(lambda r: httpx.Response(400, text="bad image")))
    with pytest.raises(ExtractionError) as exc:
        p.extract([b"x"], "p", SCHEMA)
    assert not isinstance(exc.value, TransientExtractionError)


def test_openai_invalid_json():
    p = OpenAIProvider("k", "m", client(lambda r: httpx.Response(
        200, json=_responses_payload("not json"))))
    with pytest.raises(ExtractionError, match="invalid JSON"):
        p.extract([b"x"], "p", SCHEMA)


def test_build_provider_rules():
    assert build_provider("none", None, None) is None
    with pytest.raises(ExtractionError, match="API key"):
        build_provider("anthropic", None, None)
    with pytest.raises(ExtractionError, match="EXTRACTION_MODEL"):
        build_provider("openai", "k", None)
    assert build_provider("anthropic", "k", None).model == "claude-sonnet-5-5"
    assert build_provider("openai", "k", "gpt-6-luna", reasoning_effort="none")._effort == "none"

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


def test_openai_request_and_parse():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "model": "gpt-test", "usage": {"prompt_tokens": 900, "completion_tokens": 150},
            "choices": [{"message": {"content": json.dumps(DATA), "refusal": None}}]})

    out = OpenAIProvider("key-2", "gpt-test", client(handler)).extract([b"jpg"], "p", SCHEMA)
    fmt = seen["body"]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert seen["body"]["messages"][1]["content"][0]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,")
    assert out.data == DATA and out.input_tokens == 900


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
    p = OpenAIProvider("k", "m", client(lambda r: httpx.Response(200, json={
        "choices": [{"message": {"content": "not json", "refusal": None}}]})))
    with pytest.raises(ExtractionError, match="invalid JSON"):
        p.extract([b"x"], "p", SCHEMA)


def test_build_provider_rules():
    assert build_provider("none", None, None) is None
    with pytest.raises(ExtractionError, match="API key"):
        build_provider("anthropic", None, None)
    with pytest.raises(ExtractionError, match="EXTRACTION_MODEL"):
        build_provider("openai", "k", None)
    assert build_provider("anthropic", "k", None).model == "claude-sonnet-5-5"

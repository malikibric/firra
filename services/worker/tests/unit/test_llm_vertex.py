import json
from typing import Any

import httpx
import pytest

from firra.http import ProviderError
from firra.llm.vertex import LlmBlockedError, Usage, VertexClient, endpoint_for


class _Tokens:
    def token(self) -> str:
        return "tok"


def _client(handler: Any) -> VertexClient:
    return VertexClient(
        project_id="proj",
        location="eu",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        tokens=_Tokens(),
        sleep=lambda _: None,
    )


def _answer(text: str, finish: str = "STOP", **usage: int) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": "thinking...", "thought": True}, {"text": text}]},
                "finishReason": finish,
            }
        ],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20, **usage},
    }


def test_generate_calls_the_eu_endpoint_with_a_json_schema_and_no_sampling_params() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_answer('{"ok": true}', thoughtsTokenCount=5))

    result = _client(handler).generate(
        "gemini-3.5-flash-lite",
        system="sys",
        user="usr",
        response_schema={"type": "object"},
        thinking_budget=0,
    )

    request = seen[0]
    assert str(request.url) == (
        "https://aiplatform.eu.rep.googleapis.com/v1/projects/proj/locations/eu/"
        "publishers/google/models/gemini-3.5-flash-lite:generateContent"
    )
    assert request.headers["authorization"] == "Bearer tok"
    body = json.loads(request.content)
    config = body["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"] == {"type": "object"}
    assert config["thinkingConfig"] == {"thinkingBudget": 0}
    assert not {"temperature", "topP", "topK"} & config.keys()
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"
    # Thought parts are not part of the answer; thinking tokens are billed as output.
    assert result.text == '{"ok": true}'
    assert result.usage == Usage(input_tokens=100, output_tokens=25)


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (_answer("{}", finish="MAX_TOKENS"), "MAX_TOKENS"),
        ({"promptFeedback": {"blockReason": "SAFETY"}, "usageMetadata": {}}, "SAFETY"),
        ({"candidates": [], "usageMetadata": {"promptTokenCount": 7}}, "NO_CANDIDATE"),
        (_answer("{}", finish="free text from the model"), "STOP"),
    ],
)
def test_unusable_answers_raise_with_an_enum_reason_and_the_billed_usage(
    payload: dict[str, Any], reason: str
) -> None:
    client = _client(lambda _r: httpx.Response(200, json=payload))
    if reason == "STOP":  # a non-enum finish reason is treated as STOP, never echoed
        assert client.generate("m", system="s", user="u", response_schema={}).text == "{}"
        return
    with pytest.raises(LlmBlockedError) as err:
        client.generate("m", system="s", user="u", response_schema={})
    assert err.value.reason == reason


def test_api_errors_carry_status_and_reason_only() -> None:
    error = {"error": {"code": 404, "status": "NOT_FOUND", "message": "model x not found"}}
    client = _client(lambda _r: httpx.Response(404, json=error))
    with pytest.raises(ProviderError) as err:
        client.generate("m", system="s", user="u", response_schema={})
    assert (err.value.status, err.value.reason) == (404, "NOT_FOUND")


def test_embed_sends_one_instance_and_checks_the_dimensionality() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        values = [0.1] * 768
        return httpx.Response(
            200,
            json={
                "predictions": [
                    {"embeddings": {"values": values, "statistics": {"token_count": 3}}}
                ]
            },
        )

    embedding = _client(handler).embed(
        "gemini-embedding-001", "blue sky", task_type="SEMANTIC_SIMILARITY", dimensionality=768
    )
    assert seen[0] == {
        "instances": [{"content": "blue sky", "task_type": "SEMANTIC_SIMILARITY"}],
        "parameters": {"outputDimensionality": 768},
    }
    assert len(embedding.values) == 768
    assert embedding.usage == Usage(input_tokens=3)

    with pytest.raises(LlmBlockedError, match="BAD_EMBEDDING"):
        _client(handler).embed("m", "x", task_type="SEMANTIC_SIMILARITY", dimensionality=256)


def test_only_eu_locations_are_accepted() -> None:
    assert endpoint_for("europe-west3") == "https://europe-west3-aiplatform.googleapis.com"
    for location in ("us-central1", "global", "us", "europe"):
        with pytest.raises(ValueError, match="EU"):
            endpoint_for(location)


def test_embeddings_use_their_own_location_and_generation_keeps_the_multi_region() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith(":predict"):
            return httpx.Response(200, json={"predictions": [{"embeddings": {"values": [0.5]}}]})
        return httpx.Response(200, json=_answer("{}"))

    client = VertexClient(
        project_id="proj",
        location="eu",
        embed_location="europe-west3",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        tokens=_Tokens(),
        sleep=lambda _: None,
    )
    client.generate("gemini-3.8-flash", system="s", user="u", response_schema={"type": "object"})
    client.embed("gemini-embedding-001", "text", task_type="RETRIEVAL_DOCUMENT", dimensionality=1)
    assert seen[0].startswith(
        "https://aiplatform.eu.rep.googleapis.com/v1/projects/proj/locations/eu/"
    )
    assert seen[1].startswith(
        "https://europe-west3-aiplatform.googleapis.com/v1/projects/proj/locations/europe-west3/"
    )
    assert seen[1].endswith("/models/gemini-embedding-001:predict")


def test_embeddings_follow_the_generation_location_when_no_other_is_set() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"predictions": [{"embeddings": {"values": [0.5]}}]})

    _client(handler).embed("m", "text", task_type="RETRIEVAL_DOCUMENT", dimensionality=1)
    assert "/locations/eu/" in seen[0]


def test_a_non_eu_embedding_location_is_refused() -> None:
    client = VertexClient(
        project_id="proj",
        location="eu",
        embed_location="us-central1",
        http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
        tokens=_Tokens(),
        sleep=lambda _: None,
    )
    with pytest.raises(ValueError, match="EU"):
        client.embed("m", "text", task_type="RETRIEVAL_DOCUMENT", dimensionality=1)


def test_the_schema_sent_to_vertex_has_no_length_or_item_limits_but_keeps_everything_else() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_answer("{}"))

    schema: dict[str, Any] = {
        "type": "object",
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "maxItems": 30,
                "items": {
                    "type": "object",
                    "properties": {
                        "quote": {"type": "string", "maxLength": 600, "title": "Quote"},
                        "kind": {"enum": ["a", "b"], "type": "string"},
                        "note": {"anyOf": [{"type": "string", "maxLength": 9}, {"type": "null"}]},
                    },
                },
            }
        },
    }
    _client(handler).generate("m", system="s", user="u", response_schema=schema)
    sent = seen[0]["generationConfig"]["responseJsonSchema"]
    item = sent["properties"]["items"]["items"]["properties"]
    assert "maxItems" not in sent["properties"]["items"]
    assert item["quote"] == {"type": "string", "title": "Quote"}
    assert item["kind"] == {"enum": ["a", "b"], "type": "string"}
    assert item["note"] == {"anyOf": [{"type": "string"}, {"type": "null"}]}
    assert "maxLength" in schema["properties"]["items"]["items"]["properties"]["quote"]  # untouched


def test_real_schemas_carry_no_limit_keywords_to_vertex() -> None:
    from firra.cards.schema import CardText
    from firra.extract.schema import ExtractionOutput
    from firra.llm.service import json_schema_for
    from firra.llm.vertex import vertex_schema

    for model in (ExtractionOutput, CardText):
        text = json.dumps(vertex_schema(json_schema_for(model)))
        assert "maxLength" not in text and "maxItems" not in text

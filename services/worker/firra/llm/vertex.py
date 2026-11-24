"""Vertex AI REST client for Gemini (``generateContent``) and embeddings (``predict``).

Docs checked 2026-09-28 (Vertex AI generative AI REST reference): ``generationConfig`` takes
``responseMimeType`` + ``responseJsonSchema`` for structured output and ``thinkingConfig`` with
``thinkingBudget``; ``usageMetadata`` reports ``promptTokenCount``, ``candidatesTokenCount`` and
``thoughtsTokenCount`` (thinking is billed as output). ``gemini-embedding-001`` embeds one input
per ``predict`` call (``instances[].content`` + ``task_type``, ``parameters.outputDimensionality``).
The EU multi-region endpoint is ``aiplatform.eu.rep.googleapis.com`` (location ``eu``); single EU
regions use ``<region>-aiplatform.googleapis.com``. No temperature/top_p/top_k (CLAUDE.md § 7).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from firra.http import ProviderError, request_with_retry

_ENUM = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class TokenSource(Protocol):
    def token(self) -> str: ...


def endpoint_for(location: str) -> str:
    if location == "eu":
        return "https://aiplatform.eu.rep.googleapis.com"
    if re.fullmatch(r"europe-[a-z]+[0-9]+", location):
        return f"https://{location}-aiplatform.googleapis.com"
    raise ValueError("only EU Vertex AI locations are allowed")


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0  # candidates + thoughts

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens
        )


class LlmBlockedError(Exception):
    """No usable answer (safety block, token limit, empty candidate). ``reason`` is the enum the
    API returned, never text; the tokens were still billed."""

    def __init__(self, reason: str, usage: Usage) -> None:
        super().__init__(f"generation stopped: {reason}")
        self.reason = reason
        self.usage = usage


@dataclass(frozen=True, slots=True)
class Generation:
    text: str
    usage: Usage


@dataclass(frozen=True, slots=True)
class Embedding:
    values: list[float]
    usage: Usage


def _enum(value: object, default: str) -> str:
    return value if isinstance(value, str) and _ENUM.match(value) else default


def _usage(metadata: object) -> Usage:
    meta = metadata if isinstance(metadata, dict) else {}
    return Usage(
        input_tokens=int(meta.get("promptTokenCount", 0)),
        output_tokens=int(meta.get("candidatesTokenCount", 0))
        + int(meta.get("thoughtsTokenCount", 0)),
    )


# Vertex rejects these keywords in ``responseJsonSchema`` with a bare 400 INVALID_ARGUMENT (found by
# bisecting the extraction schema on 2026-10-01; the error carries no detail). The Pydantic models
# still enforce the limits on the answer, so only the hint to the model is lost.
_UNSUPPORTED_SCHEMA_KEYWORDS = frozenset({"maxLength", "maxItems"})


def vertex_schema(schema: Any) -> Any:
    """The schema as Vertex accepts it: without the length and item-count limits."""
    if isinstance(schema, dict):
        return {
            key: vertex_schema(value)
            for key, value in schema.items()
            if key not in _UNSUPPORTED_SCHEMA_KEYWORDS
        }
    if isinstance(schema, list):
        return [vertex_schema(item) for item in schema]
    return schema


@dataclass
class VertexClient:
    project_id: str
    location: str
    http: httpx.Client
    tokens: TokenSource
    sleep: Callable[[float], None] | None = None
    # Embedding models are listed for regions (e.g. europe-west3) but not for the ``eu``
    # multi-region that the generation models support; None means the same location.
    embed_location: str | None = None

    def _post(
        self, model: str, method: str, body: dict[str, Any], location: str | None = None
    ) -> dict[str, Any]:
        place = location or self.location
        url = (
            f"{endpoint_for(place)}/v1/projects/{self.project_id}/locations/"
            f"{place}/publishers/google/models/{model}:{method}"
        )
        response = request_with_retry(
            self.http,
            "POST",
            url,
            json=body,
            headers={"Authorization": f"Bearer {self.tokens.token()}"},
            # One call lasts at most the deadline plus one attempt (about 4 minutes), inside the
            # local delivery window; a hung model then fails the task instead of stranding it.
            timeout=90.0,
            deadline_seconds=150.0,
            sleep=self.sleep,
        )
        if response.status_code >= 400:
            raise ProviderError.from_response(response)
        data: dict[str, Any] = response.json()
        return data

    def generate(
        self,
        model: str,
        *,
        system: str,
        user: str,
        response_schema: dict[str, Any],
        thinking_budget: int | None = None,
        max_output_tokens: int = 8192,
    ) -> Generation:
        config: dict[str, Any] = {
            "responseMimeType": "application/json",
            "responseJsonSchema": vertex_schema(response_schema),
            "maxOutputTokens": max_output_tokens,
        }
        if thinking_budget is not None:
            config["thinkingConfig"] = {"thinkingBudget": thinking_budget}
        data = self._post(
            model,
            "generateContent",
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": config,
            },
        )
        usage = _usage(data.get("usageMetadata"))
        feedback = data.get("promptFeedback")
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            raise LlmBlockedError(_enum(feedback["blockReason"], "BLOCKED"), usage)
        candidates = data.get("candidates") or []
        if not candidates:
            raise LlmBlockedError("NO_CANDIDATE", usage)
        candidate = candidates[0]
        finish = _enum(candidate.get("finishReason"), "STOP")
        if finish != "STOP":
            raise LlmBlockedError(finish, usage)
        parts = candidate.get("content", {}).get("parts", [])
        text = "".join(str(p.get("text", "")) for p in parts if not p.get("thought"))
        return Generation(text=text, usage=usage)

    def embed(self, model: str, text: str, *, task_type: str, dimensionality: int) -> Embedding:
        data = self._post(
            model,
            "predict",
            {
                "instances": [{"content": text, "task_type": task_type}],
                "parameters": {"outputDimensionality": dimensionality},
            },
            location=self.embed_location,
        )
        embeddings = (data.get("predictions") or [{}])[0].get("embeddings", {})
        values = [float(v) for v in embeddings.get("values", [])]
        if len(values) != dimensionality:
            raise LlmBlockedError("BAD_EMBEDDING", Usage())
        tokens = int(embeddings.get("statistics", {}).get("token_count", 0))
        return Embedding(values=values, usage=Usage(input_tokens=tokens))

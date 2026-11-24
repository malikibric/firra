"""A stand-in for FIRRA_MODEL_SYNTHDATA: writes plain bodies that contain the required sentences."""

import json
from dataclasses import dataclass, field
from typing import Any

from firra.llm.vertex import Embedding, Generation, Usage


@dataclass
class FakeWriter:
    # Thread subjects whose first answer leaves out the required sentences.
    drop_includes_for: set[str] = field(default_factory=set)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def generate(self, model: str, *, user: str, **_: Any) -> Generation:
        request = json.loads(user)
        self.calls.append(request)
        subject = request["subject"]
        bodies = []
        for message in request["messages"]:
            first = message["from"].split()[0]
            include = "" if subject in self.drop_includes_for else " ".join(message["include"])
            bodies.append(
                f"Hi team,\n\n{include} Here is a short routine update about {subject} "
                f"with the details we discussed last week.\n\nBest,\n{first}"
            )
        self.drop_includes_for.discard(subject)
        return Generation(
            text=json.dumps({"bodies": bodies}), usage=Usage(input_tokens=900, output_tokens=400)
        )

    def embed(self, model: str, text: str, *, task_type: str, dimensionality: int) -> Embedding:
        raise AssertionError("generation does not embed")

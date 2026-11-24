"""Versioned prompts from services/worker/prompts/<name>.v<N>.md (CLAUDE.md § 7).

The container copies the directory to /app/prompts and points FIRRA_PROMPTS_DIR at it; in a source
checkout the directory next to the package is used.
"""

import os
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from firra import resources

_VERSION = re.compile(r"^[a-z_]+\.v[1-9][0-9]*$")


@dataclass(frozen=True, slots=True)
class Prompt:
    version: str  # e.g. "extract.v1", stored with every output
    text: str


def prompts_dir() -> Path:
    configured = os.environ.get("FIRRA_PROMPTS_DIR")
    return Path(configured) if configured else resources.prompts_dir()


@cache
def load_prompt(version: str) -> Prompt:
    if not _VERSION.match(version):
        raise ValueError(f"invalid prompt version {version!r}")
    return Prompt(version=version, text=(prompts_dir() / f"{version}.md").read_text().strip())

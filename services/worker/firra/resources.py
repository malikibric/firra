"""Where the engine's data files live: the checkout in development, ``resources/`` in the app.

The app bundle ships ``<engine>/resources/{config,prompts,supabase,contracts}`` next to
``<engine>/python`` (``scripts/build-python.sh``); ``FIRRA_RESOURCES`` overrides both.
"""

import os
import sys
from pathlib import Path

_WORKER = Path(__file__).resolve().parents[1]  # services/worker in a checkout
_REPO = Path(__file__).resolve().parents[3]


def _bundled() -> Path | None:
    configured = os.environ.get("FIRRA_RESOURCES")
    if configured:
        return Path(configured)
    candidate = Path(sys.prefix).parent / "resources"
    return candidate if candidate.is_dir() else None


def config_dir() -> Path:
    root = _bundled()
    return root / "config" if root else _WORKER / "config"


def prompts_dir() -> Path:
    root = _bundled()
    return root / "prompts" if root else _WORKER / "prompts"


def supabase_dir() -> Path:
    root = _bundled()
    return root / "supabase" if root else _REPO / "supabase"


def local_schemas_dir() -> Path:
    root = _bundled()
    return root / "contracts" / "local" if root else _REPO / "packages/contracts/schemas/local"

"""Vertex AI list prices per 1M tokens, used to record ``llm_usage.cost_usd``.

Generation rates are Google's non-global (regional / multi-region) standard online rates, which
are 10% above the global ones (price page read 2026-10-01 in the T15 readiness audit; the first
version of this table, 2026-09-28, had the global rates). The embedding rate is unchanged and its
billing unit is still to be confirmed against the console SKUs. Thinking tokens are billed as
output. Cached input is priced as normal input (conservative). Prices here only drive the ledger and
the budget cap: too high stops a run early, too low lets it overshoot, so err high.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

_MILLION = Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class Price:
    input_per_m: Decimal
    output_per_m: Decimal
    valid_until: date | None = None  # inclusive; None = until further notice


# Newest entry last; the first entry whose valid_until is not before the call date applies.
PRICES: dict[str, tuple[Price, ...]] = {
    "gemini-3.5-flash-lite": (Price(Decimal("0.33"), Decimal("2.75")),),
    "gemini-3.5-flash": (Price(Decimal("1.65"), Decimal("9.90")),),
    "gemini-3.8-flash": (
        Price(Decimal("0.825"), Decimal("4.125"), valid_until=date(2026, 12, 31)),
        Price(Decimal("1.65"), Decimal("8.25")),
    ),
    "gemini-embedding-001": (Price(Decimal("0.15"), Decimal("0")),),
}


class UnknownModelPriceError(Exception):
    """A model without a price entry: add it here before using it (never record 0 silently)."""


def cost_usd(model: str, *, input_tokens: int, output_tokens: int, on: date) -> Decimal:
    for price in PRICES.get(model, ()):
        if price.valid_until is None or on <= price.valid_until:
            total = input_tokens * price.input_per_m + output_tokens * price.output_per_m
            return (total / _MILLION).quantize(Decimal("0.000001"))
    raise UnknownModelPriceError(model)

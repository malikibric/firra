from datetime import date
from decimal import Decimal

import pytest

from firra.llm.prices import UnknownModelPriceError, cost_usd


def test_cost_uses_input_and_output_prices_per_million() -> None:
    cost = cost_usd(
        "gemini-3.5-flash-lite", input_tokens=2_150, output_tokens=250, on=date(2026, 10, 1)
    )
    assert cost == Decimal("0.001397")


def test_price_changes_on_its_date() -> None:
    before = cost_usd(
        "gemini-3.8-flash", input_tokens=1_000_000, output_tokens=0, on=date(2026, 12, 31)
    )
    after = cost_usd(
        "gemini-3.8-flash", input_tokens=1_000_000, output_tokens=0, on=date(2027, 1, 1)
    )
    assert (before, after) == (Decimal("0.825000"), Decimal("1.650000"))


def test_unknown_models_are_refused_instead_of_recorded_as_free() -> None:
    with pytest.raises(UnknownModelPriceError):
        cost_usd("gemini-9-ultra", input_tokens=1, output_tokens=1, on=date(2026, 10, 1))

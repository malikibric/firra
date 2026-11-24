"""Fixtures: a real relay on a random port."""

from collections.abc import Iterator

import pytest
from firra_relay.testing import Relay, start


@pytest.fixture()
def relay() -> Iterator[Relay]:
    yield from start(60_000)


@pytest.fixture()
def fast_relay() -> Iterator[Relay]:
    yield from start(1000)

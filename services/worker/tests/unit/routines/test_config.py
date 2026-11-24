"""Configuration structure and range validation; no importance rule oracles."""

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from firra.routines.config import RoutinesConfig, load_config


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("extra",), 1),
        (("version",), "routines.v2"),
        *(
            ((name,), value)
            for name in (
                "budget_per_week",
                "wrong_hold_days",
                "notice_close_days",
                "notice_near_days",
            )
            for value in (0, -1)
        ),
        (("schedules", "extra"), {}),
        *(
            (("schedules", "morning_scan", name), value)
            for name, value in (
                ("extra", 1),
                ("hour", -1),
                ("hour", 24),
                ("minute", -1),
                ("minute", 60),
                ("weekdays", [0]),
                ("weekdays", [8]),
            )
        ),
    ],
)
def test_invalid_config(path: tuple[str, ...], value: Any) -> None:
    data = deepcopy(load_config().model_dump())
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        RoutinesConfig.model_validate(data)


def test_defaults_cache_and_schedule_bounds() -> None:
    config = load_config()
    assert config is load_config()
    assert (
        config.version,
        config.budget_per_week,
        config.wrong_hold_days,
        config.notice_close_days,
        config.notice_near_days,
    ) == ("routines.v1", 3, 30, 14, 30)
    assert config.schedules.morning_scan.hour == 7
    assert config.schedules.morning_scan.minute == 30
    assert config.schedules.commitment_watch.hour == 8
    assert config.schedules.commitment_watch.minute == 0
    assert config.schedules.morning_scan.weekdays == (1, 2, 3, 4, 5)
    data = config.model_dump()
    data["schedules"]["morning_scan"] = {"hour": 0, "minute": 59, "weekdays": [1, 7]}
    assert RoutinesConfig.model_validate(data).schedules.morning_scan.hour == 0

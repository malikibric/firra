import base64
from uuid import uuid4

from firra.local.paging import Cursor, PageQuery, instant, page, parse_page


def enc(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def test_instant_is_canonical_utc_with_microseconds() -> None:
    assert instant("2026-10-05T10:00:00Z") == "2026-10-05T10:00:00.000000Z"
    assert instant("2026-10-05T12:00:00.5+02:00") == "2026-10-05T10:00:00.500000Z"


def test_limits_and_missing_cursor() -> None:
    assert parse_page() == PageQuery(50, None)
    for bad in ("0", "101", "-1", "1.5", "abc", ""):
        assert parse_page(bad) is None
    assert parse_page("100") == PageQuery(100, None)


def test_valid_and_invalid_cursors() -> None:
    uid = str(uuid4())
    good = enc(f"2026-10-05T10:00:00.123456Z|state:{uid}")
    parsed = parse_page("10", good)
    assert parsed == PageQuery(10, Cursor("2026-10-05T10:00:00.123456Z", f"state:{uid}"))
    assert parse_page("10", enc(f"2026-10-05T10:00:00Z|{uid}"), evidence=True) is not None
    for bad in (
        enc(f"2026-10-05T10:00:00Z|{uid}"),  # event id needs a prefix unless evidence
        enc("2026-10-05T10:00:00.1234567Z|state:" + uid),  # more than microseconds
        enc("yesterday|state:" + uid),
        enc("2026-10-05T10:00:00Z|state:not-a-uuid"),
        enc("2026-10-05T10:00:00Z|state:" + uid + "|extra"),
        good + "=",  # non canonical encoding
        "not base64!",
        "a" * 300,
    ):
        assert parse_page("10", bad) is None


def test_page_orders_newest_first_and_continues_after_the_cursor() -> None:
    ids = [f"00000000-0000-0000-0000-00000000000{i}" for i in range(1, 6)]
    rows = [{"id": ids[i], "occurred_at": f"2026-10-0{i + 1}T10:00:00Z"} for i in range(5)]
    items, nxt = page(rows, PageQuery(2, None))
    assert [r["id"] for r in items] == [ids[4], ids[3]] and nxt is not None
    cursor = parse_page("2", nxt, evidence=True)
    assert cursor is not None
    more, nxt2 = page(rows, cursor)
    assert [r["id"] for r in more] == [ids[2], ids[1]] and nxt2 is not None
    last, nxt3 = page(rows, parse_page("2", nxt2, evidence=True))  # type: ignore[arg-type]
    assert [r["id"] for r in last] == [ids[0]] and nxt3 is None


def test_ties_break_by_id_descending() -> None:
    rows = [
        {"id": "b", "occurred_at": "2026-10-05T10:00:00Z"},
        {"id": "a", "occurred_at": "2026-10-05T10:00:00Z"},
    ]
    items, _ = page(rows, PageQuery(5, None))
    assert [r["id"] for r in items] == ["b", "a"]

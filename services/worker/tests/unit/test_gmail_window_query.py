from datetime import UTC, datetime

from firra.connectors.google.gmail import MAX_QUERY_DOMAINS, window_query

START, END = datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC)


def test_query_names_only_the_mapped_client_domains() -> None:
    query = window_query(START, END, ["b.example", "a.example", "a.example"])
    assert (
        "{from:a.example to:a.example cc:a.example from:b.example to:b.example cc:b.example}"
        in (query)
    )
    assert query.startswith("after:") and "before:" in query


def test_no_domains_or_too_many_keeps_the_plain_window() -> None:
    plain = window_query(START, END)
    assert "from:" not in plain
    many = [f"c{i}.example" for i in range(MAX_QUERY_DOMAINS + 1)]
    assert window_query(START, END, many) == plain

"""Pure latent demand clustering and qualification boundaries."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from firra.needs.cluster import (
    Cluster,
    Request,
    best_service,
    cluster_requests,
    qualifying_window,
)

START = datetime(2026, 1, 1, tzinfo=UTC)


def request(number: int, day: int, thread: str, vector: tuple[float, ...] = (1, 0)) -> Request:
    return Request(
        UUID(int=number),
        f"key-{number}",
        f"request {number}",
        START + timedelta(days=day),
        thread,
        vector,
    )


def test_greedy_cluster_uses_updated_centroid_and_is_deterministic() -> None:
    rows = [
        request(4, 1, "a", (0.7, 0.7)),
        request(2, 0, "a", (0.9, 0.4359)),
        request(1, 0, "a", (1, 0)),
        request(3, 0, "b", (-1, 0)),
    ]
    first = cluster_requests(rows, 0.82)
    second = cluster_requests(list(reversed(rows)), 0.82)
    assert [[r.id for r in c.members] for c in first] == [[r.id for r in c.members] for c in second]
    assert [[r.id.int for r in c.members] for c in first] == [[1, 2, 4], [3]]
    assert first[0].centroid[1] > 0.3


def test_window_requires_three_mentions_in_two_threads_and_includes_day_60() -> None:
    cluster = Cluster()
    for item in [request(1, 0, "a"), request(2, 30, "a"), request(3, 60, "b")]:
        cluster.add(item)
    assert [
        r.id.int for r in qualifying_window(cluster, days=60, min_mentions=3, min_threads=2) or []
    ] == [1, 2, 3]
    assert qualifying_window(cluster, days=59, min_mentions=3, min_threads=2) is None
    assert qualifying_window(cluster, days=60, min_mentions=4, min_threads=2) is None
    day_61 = Cluster()
    for item in [request(1, 0, "a"), request(2, 30, "a"), request(3, 61, "b")]:
        day_61.add(item)
    assert qualifying_window(day_61, days=60, min_mentions=3, min_threads=2) is None
    one_thread = Cluster()
    for item in [request(1, 0, "a"), request(2, 30, "a"), request(3, 60, "a")]:
        one_thread.add(item)
    assert qualifying_window(one_thread, days=60, min_mentions=3, min_threads=2) is None


def test_largest_window_wins_and_tie_uses_latest() -> None:
    cluster = Cluster()
    for item in [
        request(1, 0, "a"),
        request(2, 1, "b"),
        request(3, 2, "c"),
        request(4, 63, "a"),
        request(5, 64, "b"),
        request(6, 65, "c"),
    ]:
        cluster.add(item)
    chosen = qualifying_window(cluster, days=60, min_mentions=3, min_threads=2)
    assert chosen is not None
    assert [r.id.int for r in chosen] == [4, 5, 6]
    cluster.add(request(7, 66, "d"))
    chosen = qualifying_window(cluster, days=60, min_mentions=3, min_threads=2)
    assert chosen is not None
    assert [r.id.int for r in chosen] == [4, 5, 6, 7]


def test_service_match_threshold_and_stable_tie() -> None:
    services = [(UUID(int=2), (1.0, 0.0)), (UUID(int=1), (1.0, 0.0))]
    assert best_service((1.0, 0.0), services, 0.8) == UUID(int=1)
    assert best_service((0.0, 1.0), services, 0.8) is None

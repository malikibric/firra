"""Pure clustering, qualifying-window and service matching rules."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import isfinite, sqrt
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Request:
    id: UUID
    dedupe_key: str
    text: str
    occurred_at: datetime
    thread_key: str | None
    embedding: tuple[float, ...]


@dataclass(slots=True)
class Cluster:
    members: list[Request] = field(default_factory=list)
    vector_sum: list[float] = field(default_factory=list)

    @property
    def centroid(self) -> tuple[float, ...]:
        return normalize(self.vector_sum)

    def add(self, request: Request) -> None:
        vector = normalize(request.embedding)
        if not self.vector_sum:
            self.vector_sum = list(vector)
        else:
            if len(vector) != len(self.vector_sum):
                raise ValueError("embedding dimensions differ")
            for index, value in enumerate(vector):
                self.vector_sum[index] += value
        self.members.append(request)


def normalize(vector: tuple[float, ...] | list[float]) -> tuple[float, ...]:
    if not vector or not all(isfinite(value) for value in vector):
        raise ValueError("invalid embedding")
    size = sqrt(sum(value * value for value in vector))
    if not isfinite(size) or not size:
        raise ValueError("zero embedding")
    return tuple(value / size for value in vector)


def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding dimensions differ")
    return sum(a * b for a, b in zip(normalize(left), normalize(right), strict=True))


def cluster_requests(requests: list[Request], similarity: float) -> list[Cluster]:
    clusters: list[Cluster] = []
    for request in sorted(requests, key=lambda item: (item.occurred_at, item.id)):
        vector = normalize(request.embedding)
        matches = [
            (cosine(vector, cluster.centroid), index) for index, cluster in enumerate(clusters)
        ]
        best = max(matches, key=lambda match: (match[0], -match[1])) if matches else None
        if best is not None and best[0] >= similarity:
            clusters[best[1]].add(request)
        else:
            cluster = Cluster()
            cluster.add(request)
            clusters.append(cluster)
    return clusters


def qualifying_window(
    cluster: Cluster, *, days: int, min_mentions: int, min_threads: int
) -> list[Request] | None:
    members = cluster.members
    best: tuple[int, int] | None = None
    threads: Counter[str] = Counter()
    end = 0
    span = timedelta(days=days)
    for start in range(len(members)):
        while end < len(members) and members[end].occurred_at - members[start].occurred_at <= span:
            thread_key = members[end].thread_key
            if thread_key is not None:
                threads[thread_key] += 1
            end += 1
        if (
            end - start >= min_mentions
            and len(threads) >= min_threads
            and (
                best is None
                or (end - start, members[end - 1].occurred_at, members[start].occurred_at)
                > (
                    best[1] - best[0],
                    members[best[1] - 1].occurred_at,
                    members[best[0]].occurred_at,
                )
            )
        ):
            best = (start, end)
        thread_key = members[start].thread_key
        if thread_key is not None:
            threads[thread_key] -= 1
            if not threads[thread_key]:
                del threads[thread_key]
    return members[best[0] : best[1]] if best is not None else None


def best_service(
    centroid: tuple[float, ...],
    services: Sequence[tuple[UUID, tuple[float, ...]]],
    threshold: float,
) -> UUID | None:
    ranked = [(cosine(centroid, vector), service_id) for service_id, vector in services]
    ranked.sort(key=lambda item: (-item[0], str(item[1])))
    return ranked[0][1] if ranked and ranked[0][0] >= threshold else None

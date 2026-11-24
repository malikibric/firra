import pytest

from firra.db.locality import is_local_database


@pytest.mark.parametrize(
    "target",
    [
        "postgresql://postgres:postgres@127.0.0.1:54322/postgres",
        "postgresql://postgres@localhost/db",
        "postgresql://u@[::1]:5432/db",
        "host=/tmp/firra-pg-abc user=postgres dbname=firra",
        "postgresql://postgres@/firra?host=/Users/me/Library/Application%20Support/Firra/run",
    ],
)
def test_loopback_and_unix_socket_are_local(target: str) -> None:
    assert is_local_database(target)


@pytest.mark.parametrize(
    "target",
    [
        "postgresql://u:p@db.project.supabase.co:5432/postgres",
        "host=10.0.0.5 user=postgres",
        "host=/tmp/x,db.example.com user=postgres",
        "postgresql:///firra",
        "not a connection string at all ::",
        "",
    ],
)
def test_everything_else_is_not(target: str) -> None:
    assert not is_local_database(target)

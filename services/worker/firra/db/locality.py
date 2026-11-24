"""Whether a database connection string points at this machine (loopback or a unix socket).

CLI tools that write test or eval data refuse anything else. A unix socket path
(``host=/some/dir``) is local by construction: it is how the bundled engine database is reached.
"""

from psycopg.conninfo import conninfo_to_dict

_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


def is_local_database(url_or_conninfo: str) -> bool:
    try:
        host = conninfo_to_dict(url_or_conninfo).get("host")
    except Exception:
        return False
    if host is None:
        return False
    return all(part in _LOOPBACK or part.startswith("/") for part in str(host).split(","))

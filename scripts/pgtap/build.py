"""Generate pgTAP 1.3.4's pure SQL exactly as its PG17 Makefile target does."""

import hashlib
import io
import platform
import tarfile
import urllib.request
from pathlib import Path

VERSION = "1.3.4"
SHA256 = "d2c951afb296a001d21785611a8e966e3f8fa3f5bfbd929396a5130c0152f314"
URL = f"https://github.com/theory/pgtap/archive/refs/tags/v{VERSION}.tar.gz"


def install(destination: Path) -> None:
    """Install only into a disposable bundle copy, never the product's shared bundle."""
    with urllib.request.urlopen(URL, timeout=30) as response:  # nosemgrep
        archive = response.read()
    if hashlib.sha256(archive).hexdigest() != SHA256:
        raise RuntimeError("pgTAP checksum mismatch")
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:

        def read(name: str) -> str:
            member = source.extractfile(f"pgtap-{VERSION}/{name}")
            if member is None:
                raise RuntimeError("pgTAP source file missing")
            return member.read().decode()

        sql = read("sql/pgtap.sql.in")
        sql = sql.replace("MODULE_PATHNAME", "pgtap")
        sql = sql.replace("__OS__", platform.system().lower()).replace("__VERSION__", "1.3")
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "pgtap.control").write_text(read("pgtap.control"))
        (destination / f"pgtap--{VERSION}.sql").write_text(sql)

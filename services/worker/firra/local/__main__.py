"""`python -I -B -m firra.local --home DIR --pg-bin DIR`: the engine the app starts."""

import argparse
import os
import signal
from pathlib import Path

from firra.local.engine import Engine, Paths
from firra.logging import configure_logging

VERSION = "0.1.0"


def main() -> int:
    parser = argparse.ArgumentParser(prog="firra.local")
    parser.add_argument("--home", type=Path, required=True, help="data directory (0700)")
    parser.add_argument("--pg-bin", type=Path, required=True, help="bundled postgres bin directory")
    parser.add_argument("--control-dir", type=Path, help="directory of the control socket")
    args = parser.parse_args()
    configure_logging("INFO")
    parent = os.getppid()  # the app that spawned us is the only peer the control socket accepts
    engine = Engine(
        Paths.under(args.home, args.pg_bin, args.control_dir),
        version=VERSION,
        expected_app_pid=parent,
    )
    signal.signal(signal.SIGTERM, lambda *_: engine.stop())
    signal.signal(signal.SIGINT, lambda *_: engine.stop())
    return engine.run()


if __name__ == "__main__":
    raise SystemExit(main())

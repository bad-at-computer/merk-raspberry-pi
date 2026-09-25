from __future__ import annotations

import logging
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import load_config
from .orchestrator import Orchestrator
from .state import BotState


def setup_logging(log_file: str) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    root.addHandler(console)
    try:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=1_000_000, backupCount=3
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        pass


def main() -> int:
    config = load_config()
    setup_logging(config.log_file)
    state = BotState(config.state_file)
    orchestrator = Orchestrator(config, state)

    stopping = False

    def _stop(_sig, _frame) -> None:
        nonlocal stopping
        if stopping:
            sys.exit(1)
        stopping = True
        orchestrator.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    orchestrator.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
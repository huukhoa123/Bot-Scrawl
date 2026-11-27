"""Root entrypoint for Pterodactyl / Python Bot Hosting panels.

Set Startup -> Python Script File to: bot.py
Runs 24/7: listens for Telegram commands instantly AND auto-crawls ITviec periodically.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.main import main  # noqa: E402

if __name__ == "__main__":
    interval = os.getenv("CRAWL_INTERVAL_HOURS", "2")
    spiders = os.getenv("BOT_SPIDERS", "itviec").split()
    mode = os.getenv("BOT_MODE", "instant")
    raise SystemExit(
        main(
            [
                "--listen",
                "--mode",
                mode,
                "--interval-hours",
                interval,
                "--spiders",
                *spiders,
            ]
        )
    )

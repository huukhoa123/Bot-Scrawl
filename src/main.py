from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path

from src.bot_sync import register_bot_commands, sync_telegram_updates
from src.config import DEFAULT_CONFIG_PATH, AppConfig, load_config
from src.db import JobDB
from src.notifier import notify_jobs_digest, notify_jobs_instant
from src.summarizer import score_job_match, summarize_job

LOG_DIR = Path("logs")


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "job_bot.log", encoding="utf-8"),
    ]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


logger = logging.getLogger(__name__)

DEFAULT_SPIDERS = ("topdev", "vietnamworks", "itviec")


def run_spider_subprocess(spider_name: str, config_path: Path) -> bool:
    """Run a single Scrapy spider in an isolated subprocess."""
    cmd = [
        sys.executable,
        "-m",
        "scrapy",
        "crawl",
        spider_name,
        "-a",
        f"config_path={config_path}",
    ]
    logger.info("Starting spider subprocess: %s", " ".join(cmd))
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        logger.error("Spider '%s' exited with code %d (continuing other spiders).", spider_name, result.returncode)
        return False
    return True


def run_pipeline(
    cfg: AppConfig,
    config_path: Path,
    spiders: tuple[str, ...],
    mode: str,
    dry_run: bool,
    cleanup_days: int = 90,
) -> None:
    with JobDB(cfg.db_path) as db:
        db.seed_searches_if_empty([(q.keyword, q.location) for q in cfg.searches])
        synced, _ = sync_telegram_updates(
            bot_token=cfg.telegram_bot_token,
            chat_id=cfg.telegram_chat_id,
            db=db,
            dry_run=dry_run,
        )
        if synced:
            logger.info("Processed %d Telegram updates.", synced)

        if db.get_state("paused", "0") == "1":
            logger.info("Bot is paused via /pause command; skipping run.")
            return

    if mode in ("instant", "scrape"):
        for spider_name in spiders:
            run_spider_subprocess(spider_name, config_path)

    target_keywords = list(cfg.filters.include_keywords) or [q.keyword for q in cfg.searches]

    with JobDB(cfg.db_path) as db:
        removed = db.cleanup_old_jobs(days=cleanup_days)
        if removed:
            logger.info("Cleaned up %d old jobs (>%d days).", removed, cleanup_days)

        if mode == "scrape":
            pending = db.get_jobs_by_status(("new",), limit=cfg.notify.max_jobs_per_run)
            for job in pending:
                summary = summarize_job(job, cfg)
                db.mark_status(job.source, job.job_id, status="pending_digest", summary=summary)
            logger.info("Queued %d jobs for digest.", len(pending))
            return

        if mode == "digest":
            pending = db.get_jobs_by_status(("pending_digest", "new"), limit=cfg.notify.max_jobs_per_run)
            if not pending:
                logger.info("No pending jobs for digest.")
                return
            ok = notify_jobs_digest(
                jobs=pending,
                bot_token=cfg.telegram_bot_token,
                chat_id=cfg.telegram_chat_id,
                dry_run=dry_run,
            )
            if ok and not dry_run:
                for job in pending:
                    db.mark_status(job.source, job.job_id, status="sent", mark_sent=True)
            logger.info("Digest done: count=%d, ok=%s, dry_run=%s", len(pending), ok, dry_run)
            return

        if mode == "instant":
            to_send = db.get_jobs_by_status(("new", "failed"), limit=cfg.notify.max_jobs_per_run)
            if not to_send:
                logger.info("No new jobs to send.")
                return

            summaries = {(job.source, job.job_id): summarize_job(job, cfg) for job in to_send}
            scores = {
                (job.source, job.job_id): score_job_match(job, target_keywords)
                for job in to_send
            }

            sent, failed = notify_jobs_instant(
                jobs=to_send,
                bot_token=cfg.telegram_bot_token,
                chat_id=cfg.telegram_chat_id,
                summaries=summaries,
                scores=scores,
                dry_run=dry_run,
            )

            if not dry_run:
                for job in sent:
                    db.mark_status(
                        job.source,
                        job.job_id,
                        status="sent",
                        summary=summaries[(job.source, job.job_id)],
                        mark_sent=True,
                    )
                for job in failed:
                    db.mark_status(
                        job.source,
                        job.job_id,
                        status="failed",
                        summary=summaries[(job.source, job.job_id)],
                    )
            logger.info("Done: sent=%d, failed=%d, dry_run=%s", len(sent), len(failed), dry_run)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Job Scraper -> Telegram Bot")
    parser.add_argument(
        "--mode",
        choices=("instant", "scrape", "digest"),
        default=None,
        help="Override notify.mode from config.yaml",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to config.yaml",
    )
    parser.add_argument(
        "--spiders",
        nargs="+",
        default=list(DEFAULT_SPIDERS),
        help="Spiders to run (default: itviec topcv)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print formatted Telegram messages instead of sending",
    )
    parser.add_argument(
        "--cleanup-days",
        type=int,
        default=90,
        help="Delete old jobs older than N days",
    )
    parser.add_argument(
        "--listen",
        action="store_true",
        help="Long-poll Telegram for instant /search, /list, /remove, /run, and inline buttons",
    )
    parser.add_argument(
        "--interval-hours",
        type=float,
        default=0.0,
        help="In --listen mode, also auto-crawl every N hours (0 = manual /run or /search only)",
    )
    return parser.parse_args(argv)


def run_listen_loop(
    cfg: AppConfig,
    config_path: Path,
    spiders: tuple[str, ...],
    mode: str,
    dry_run: bool,
    cleanup_days: int = 90,
    interval_hours: float = 0.0,
) -> None:
    logger.info(
        "Listening 24/7 for Telegram commands (auto-crawl interval=%.1fh)...",
        interval_hours,
    )
    register_bot_commands(cfg.telegram_bot_token)
    with JobDB(cfg.db_path) as db:
        db.seed_searches_if_empty([(q.keyword, q.location) for q in cfg.searches])

    interval_sec = max(0.0, interval_hours * 3600.0)
    last_crawl_ts = 0.0

    try:
        while True:
            with JobDB(cfg.db_path) as db:
                synced, should_crawl = sync_telegram_updates(
                    bot_token=cfg.telegram_bot_token,
                    chat_id=cfg.telegram_chat_id,
                    db=db,
                    dry_run=False,
                    poll_timeout=25,
                )
            if synced:
                logger.info("Handled %d Telegram update(s).", synced)

            now = time.monotonic()
            due_by_schedule = interval_sec > 0 and (last_crawl_ts == 0.0 or (now - last_crawl_ts) >= interval_sec)
            if should_crawl or due_by_schedule:
                run_pipeline(
                    cfg=cfg,
                    config_path=config_path,
                    spiders=spiders,
                    mode=mode,
                    dry_run=dry_run,
                    cleanup_days=cleanup_days,
                )
                last_crawl_ts = time.monotonic()
    except KeyboardInterrupt:
        logger.info("Stopped listening.")


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = parse_args(argv)
    cfg = load_config(args.config)
    mode = args.mode or cfg.notify.mode
    if args.listen:
        run_listen_loop(
            cfg=cfg,
            config_path=args.config,
            spiders=tuple(args.spiders),
            mode=mode,
            dry_run=args.dry_run,
            cleanup_days=args.cleanup_days,
            interval_hours=args.interval_hours,
        )
        return 0

    run_pipeline(
        cfg=cfg,
        config_path=args.config,
        spiders=tuple(args.spiders),
        mode=mode,
        dry_run=args.dry_run,
        cleanup_days=args.cleanup_days,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

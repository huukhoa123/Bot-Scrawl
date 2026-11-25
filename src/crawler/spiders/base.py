from __future__ import annotations

import logging
from typing import Any

import scrapy
from scrapy.exceptions import CloseSpider

from src.config import DEFAULT_CONFIG_PATH, AppConfig, SearchQuery, load_config
from src.db import JobDB
from src.filters import parse_salary_million, pre_filter
from src.models import Job

logger = logging.getLogger(__name__)

BLOCK_MARKERS = (
    "cf-challenge",
    "just a moment...",
    "attention required! | cloudflare",
    "verify you are human",
)


class BaseJobSpider(scrapy.Spider):
    """Base 2-phase spider: list -> dedup + pre_filter -> detail."""

    source_name: str = ""
    handle_httpstatus_list = [403]

    def __init__(self, config_path: str = str(DEFAULT_CONFIG_PATH), *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.app_config: AppConfig = load_config(config_path)
        self.db = JobDB(self.app_config.db_path)
        self.skip_ids: set[str] = self.db.get_skip_job_ids(
            self.source_name,
            self.app_config.filter_hash,
        )
        self.seen_in_run: set[str] = set()

    def closed(self, reason: str) -> None:
        self.db.close()

    def get_active_searches(self) -> list[SearchQuery]:
        """Return searches from SQLite (updated via /search) or fallback to config.yaml."""
        rows = self.db.list_searches()
        if rows:
            default_pages = self.app_config.searches[0].max_pages if self.app_config.searches else 3
            return [SearchQuery(keyword=kw, location=loc, max_pages=default_pages) for _, kw, loc in rows]
        return list(self.app_config.searches)

    def check_blocked(self, response: scrapy.http.Response) -> None:
        """Stop spider cleanly if 403/Cloudflare/captcha is encountered (never bypass)."""
        if response.status == 403:
            logger.error("[%s] HTTP 403 at %s — stopping spider.", self.source_name, response.url)
            raise CloseSpider(reason="blocked_403")
        body_low = response.text[:4000].lower()
        if any(marker in body_low for marker in BLOCK_MARKERS):
            logger.error(
                "[%s] Anti-bot/Cloudflare challenge at %s — stopping spider.",
                self.source_name,
                response.url,
            )
            raise CloseSpider(reason="blocked_challenge")

    def should_fetch_detail(self, summary_job: Job) -> bool:
        """Return True only if job is unseen and passes pre_filter."""
        jid = summary_job.job_id
        if jid in self.skip_ids or jid in self.seen_in_run:
            return False
        self.seen_in_run.add(jid)

        if summary_job.salary and summary_job.salary_min_million is None and summary_job.salary_max_million is None:
            s_min, s_max = parse_salary_million(summary_job.salary)
            summary_job.salary_min_million = s_min
            summary_job.salary_max_million = s_max

        if not pre_filter(summary_job, self.app_config.filters):
            self.db.upsert_job(
                summary_job,
                status="filtered_out",
                filter_hash=self.app_config.filter_hash,
            )
            logger.debug("[%s] Pre-filtered out %s (%s)", self.source_name, jid, summary_job.title)
            return False

        return True

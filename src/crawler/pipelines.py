from __future__ import annotations

import logging
from typing import Any

from src.db import JobDB
from src.filters import full_filter, parse_salary_million
from src.models import Job

logger = logging.getLogger(__name__)


class FilterAndStorePipeline:
    """Apply full_filter and store jobs in SQLite with status 'new' or 'filtered_out'."""

    def __init__(self, crawler: Any) -> None:
        self.crawler = crawler
        self.db: JobDB | None = None

    @classmethod
    def from_crawler(cls, crawler: Any) -> FilterAndStorePipeline:
        return cls(crawler)

    def open_spider(self) -> None:
        self.app_config = self.crawler.spider.app_config
        self.db = JobDB(self.app_config.db_path)

    def close_spider(self) -> None:
        if self.db is not None:
            self.db.close()

    def process_item(self, item: dict[str, Any]) -> dict[str, Any]:
        assert self.db is not None
        salary_raw = item.get("salary")
        s_min, s_max = parse_salary_million(salary_raw)

        job = Job(
            source=str(item["source"]),
            job_id=str(item["job_id"]),
            title=str(item.get("title") or "").strip(),
            company=str(item.get("company") or "").strip(),
            location=str(item.get("location") or "").strip(),
            salary=salary_raw,
            salary_min_million=s_min,
            salary_max_million=s_max,
            url=str(item["url"]),
            description=str(item.get("description") or "").strip(),
            skills=list(item.get("skills") or []),
            posted_at=item.get("posted_at"),
            deadline=item.get("deadline"),
        )

        passed = full_filter(job, self.app_config.filters)
        status = "new" if passed else "filtered_out"
        self.db.upsert_job(job, status=status, filter_hash=self.app_config.filter_hash)
        logger.info("[%s] Stored job %s (%s) -> %s", job.source, job.job_id, job.title, status)
        return item

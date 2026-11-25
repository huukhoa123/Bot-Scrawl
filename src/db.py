from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.models import Job

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    source TEXT NOT NULL,
    job_id TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT NOT NULL,
    salary TEXT,
    salary_min_million REAL,
    salary_max_million REAL,
    url TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    skills TEXT NOT NULL DEFAULT '[]',
    posted_at TEXT,
    deadline TEXT,
    summary TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    filter_hash TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    sent_at TEXT,
    PRIMARY KEY (source, job_id)
);

CREATE TABLE IF NOT EXISTS searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    UNIQUE(keyword, location)
);

CREATE TABLE IF NOT EXISTS bot_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobDB:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.init_db()

    def init_db(self) -> None:
        with self.conn:
            self.conn.executescript(SCHEMA_SQL)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> JobDB:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def get_skip_job_ids(self, source: str, current_filter_hash: str) -> set[str]:
        """Return job_ids that should NOT be re-crawled.

        Jobs with status == 'filtered_out' and a DIFFERENT filter_hash are NOT skipped,
        so they can be re-evaluated when filters in config.yaml change.
        """
        cur = self.conn.execute(
            """
            SELECT job_id FROM jobs
            WHERE source = ?
              AND NOT (status = 'filtered_out' AND filter_hash != ?)
            """,
            (source, current_filter_hash),
        )
        return {str(row["job_id"]) for row in cur.fetchall()}

    def upsert_job(
        self,
        job: Job,
        status: str,
        filter_hash: str,
    ) -> None:
        now = _utc_now_iso()
        skills_json = json.dumps(job.skills, ensure_ascii=False)
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO jobs (
                    source, job_id, title, company, location, salary,
                    salary_min_million, salary_max_million, url, description,
                    skills, posted_at, deadline, summary, status, filter_hash,
                    first_seen_at, sent_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(source, job_id) DO UPDATE SET
                    title = excluded.title,
                    company = excluded.company,
                    location = excluded.location,
                    salary = excluded.salary,
                    salary_min_million = excluded.salary_min_million,
                    salary_max_million = excluded.salary_max_million,
                    url = excluded.url,
                    description = CASE WHEN excluded.description != '' THEN excluded.description ELSE jobs.description END,
                    skills = CASE WHEN excluded.skills != '[]' THEN excluded.skills ELSE jobs.skills END,
                    posted_at = COALESCE(excluded.posted_at, jobs.posted_at),
                    deadline = COALESCE(excluded.deadline, jobs.deadline),
                    summary = COALESCE(excluded.summary, jobs.summary),
                    status = excluded.status,
                    filter_hash = excluded.filter_hash
                """,
                (
                    job.source,
                    job.job_id,
                    job.title,
                    job.company,
                    job.location,
                    job.salary,
                    job.salary_min_million,
                    job.salary_max_million,
                    job.url,
                    job.description,
                    skills_json,
                    job.posted_at,
                    job.deadline,
                    job.summary,
                    status,
                    filter_hash,
                    now,
                ),
            )

    def get_jobs_by_status(self, statuses: tuple[str, ...], limit: int = 100) -> list[Job]:
        placeholders = ",".join("?" for _ in statuses)
        cur = self.conn.execute(
            f"""
            SELECT * FROM jobs
            WHERE status IN ({placeholders})
            ORDER BY first_seen_at ASC
            LIMIT ?
            """,
            (*statuses, limit),
        )
        results: list[Job] = []
        for row in cur.fetchall():
            try:
                skills = json.loads(row["skills"] or "[]")
            except json.JSONDecodeError:
                skills = []
            results.append(
                Job(
                    source=row["source"],
                    job_id=row["job_id"],
                    title=row["title"],
                    company=row["company"],
                    location=row["location"],
                    salary=row["salary"],
                    salary_min_million=row["salary_min_million"],
                    salary_max_million=row["salary_max_million"],
                    url=row["url"],
                    description=row["description"] or "",
                    skills=skills,
                    posted_at=row["posted_at"],
                    deadline=row["deadline"],
                    summary=row["summary"],
                )
            )
        return results

    def mark_status(
        self,
        source: str,
        job_id: str,
        status: str,
        summary: str | None = None,
        mark_sent: bool = False,
    ) -> None:
        sent_at = _utc_now_iso() if mark_sent else None
        with self.conn:
            if summary is not None and mark_sent:
                self.conn.execute(
                    "UPDATE jobs SET status = ?, summary = ?, sent_at = ? WHERE source = ? AND job_id = ?",
                    (status, summary, sent_at, source, job_id),
                )
            elif mark_sent:
                self.conn.execute(
                    "UPDATE jobs SET status = ?, sent_at = ? WHERE source = ? AND job_id = ?",
                    (status, sent_at, source, job_id),
                )
            elif summary is not None:
                self.conn.execute(
                    "UPDATE jobs SET status = ?, summary = ? WHERE source = ? AND job_id = ?",
                    (status, summary, source, job_id),
                )
            else:
                self.conn.execute(
                    "UPDATE jobs SET status = ? WHERE source = ? AND job_id = ?",
                    (status, source, job_id),
                )

    def cleanup_old_jobs(self, days: int = 90) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.conn:
            cur = self.conn.execute(
                """
                DELETE FROM jobs
                WHERE first_seen_at < ?
                  AND status NOT IN ('applied', 'saved')
                """,
                (cutoff,),
            )
            return cur.rowcount

    # --- Stage 3: Searches & Bot State ---

    def seed_searches_if_empty(self, searches: list[tuple[str, str]]) -> None:
        cur = self.conn.execute("SELECT COUNT(*) AS cnt FROM searches")
        if cur.fetchone()["cnt"] > 0:
            return
        with self.conn:
            for kw, loc in searches:
                self.conn.execute(
                    "INSERT OR IGNORE INTO searches (keyword, location) VALUES (?, ?)",
                    (kw.strip(), loc.strip()),
                )

    def list_searches(self) -> list[tuple[int, str, str]]:
        cur = self.conn.execute("SELECT id, keyword, location FROM searches ORDER BY id ASC")
        return [(int(r["id"]), str(r["keyword"]), str(r["location"])) for r in cur.fetchall()]

    def add_search(self, keyword: str, location: str = "") -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO searches (keyword, location) VALUES (?, ?)",
                (keyword.strip(), location.strip()),
            )
            if cur.lastrowid:
                return int(cur.lastrowid)
        row = self.conn.execute(
            "SELECT id FROM searches WHERE keyword = ? AND location = ?",
            (keyword.strip(), location.strip()),
        ).fetchone()
        return int(row["id"]) if row else 0

    def remove_search(self, search_id: int) -> bool:
        with self.conn:
            cur = self.conn.execute("DELETE FROM searches WHERE id = ?", (search_id,))
            return cur.rowcount > 0

    def get_state(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_state(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO bot_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def mark_status_by_prefix(self, source: str, job_id_prefix: str, status: str) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE jobs SET status = ? WHERE source = ? AND (job_id = ? OR job_id LIKE ?)",
                (status, source, job_id_prefix, f"{job_id_prefix}%"),
            )
            return cur.rowcount > 0

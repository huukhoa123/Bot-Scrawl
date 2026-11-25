from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.db import JobDB
from src.models import Job


def _sample_job(job_id: str = "job-1") -> Job:
    return Job(
        source="itviec",
        job_id=job_id,
        title="Python Engineer",
        company="Tech Co",
        location="Ho Chi Minh",
        salary="20 - 30 triệu",
        salary_min_million=20.0,
        salary_max_million=30.0,
        url=f"https://itviec.com/it-jobs/{job_id}",
        skills=["Python"],
    )


def test_dedup_and_filter_hash_reevaluation(tmp_path: Path) -> None:
    db_file = tmp_path / "jobs.db"
    with JobDB(db_file) as db:
        db.upsert_job(_sample_job("sent-1"), status="sent", filter_hash="hash_v1")
        db.upsert_job(_sample_job("filt-1"), status="filtered_out", filter_hash="hash_v1")

        # Same filter_hash: both are skipped
        skip_same = db.get_skip_job_ids("itviec", "hash_v1")
        assert skip_same == {"sent-1", "filt-1"}

        # Changed filter_hash: sent-1 stays skipped, filt-1 is re-evaluated
        skip_changed = db.get_skip_job_ids("itviec", "hash_v2")
        assert skip_changed == {"sent-1"}


def test_cleanup_old_jobs_preserves_saved_and_applied(tmp_path: Path) -> None:
    db_file = tmp_path / "jobs.db"
    old_ts = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    with JobDB(db_file) as db:
        for jid, st in (("old-sent", "sent"), ("old-saved", "saved"), ("old-applied", "applied")):
            db.upsert_job(_sample_job(jid), status=st, filter_hash="h1")
        with db.conn:
            db.conn.execute("UPDATE jobs SET first_seen_at = ?", (old_ts,))

        deleted = db.cleanup_old_jobs(days=90)
        assert deleted == 1
        remaining = {r["job_id"] for r in db.conn.execute("SELECT job_id FROM jobs").fetchall()}
        assert remaining == {"old-saved", "old-applied"}

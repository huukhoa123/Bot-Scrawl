from __future__ import annotations

from src.models import Job
from src.notifier import TELEGRAM_MAX_CHARS, format_job_html


def test_format_job_html_escapes_and_caps_length() -> None:
    job = Job(
        source="itviec",
        job_id="j-1",
        title="Backend <Python> & Go",
        company="A & B Corp",
        location="Ho Chi Minh",
        salary="20 - 30 triệu",
        url="https://itviec.com/it-jobs/j-1",
        skills=["Python", "Go", "Docker", "K8s", "AWS", "SQL", "ExtraSkill"],
        deadline="2026-12-31",
    )
    long_summary = "Line with <script> & details. " * 300
    msg = format_job_html(job, summary=long_summary)
    assert len(msg) <= TELEGRAM_MAX_CHARS
    assert "Backend &lt;Python&gt; &amp; Go" in msg
    assert "ExtraSkill" not in msg
    assert "<script>" not in msg

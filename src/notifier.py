from __future__ import annotations

import html
import logging
import time
from typing import Any

import httpx

from src.models import Job

logger = logging.getLogger(__name__)

TELEGRAM_MAX_CHARS = 4096
TELEGRAM_SEND_DELAY_SEC = 1.0


def build_job_inline_keyboard(job: Job) -> dict[str, Any]:
    """Build inline buttons for Stage 3 job status tracking."""
    ref = f"{job.source}:{job.job_id}"
    # Telegram callback_data max 64 bytes
    ref_trimmed = ref[:50]
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Đã ứng tuyển", "callback_data": f"st:applied:{ref_trimmed}"},
                {"text": "🚫 Bỏ qua", "callback_data": f"st:ignored:{ref_trimmed}"},
                {"text": "⭐ Lưu", "callback_data": f"st:saved:{ref_trimmed}"},
            ]
        ]
    }


def format_job_html(job: Job, summary: str = "", match_score: int | None = None) -> str:
    """Format a single Job as Telegram HTML message (<= 4096 chars)."""
    title = html.escape(job.title or "Untitled")
    company = html.escape(job.company or "N/A")
    location = html.escape(job.location or "N/A")
    salary = html.escape(job.salary or "Thỏa thuận")
    skills_str = html.escape(", ".join(job.skills[:6])) if job.skills else "N/A"
    deadline = html.escape(job.deadline or "Không rõ")
    url = html.escape(job.url, quote=True)
    score_line = f"🎯 Độ khớp: {match_score}%\n" if match_score is not None else ""

    header = (
        f"<b>{title}</b>\n"
        f"🏢 {company}\n"
        f"📍 {location}\n"
        f"💰 {salary}\n"
        f"🛠 {skills_str}\n"
        f"{score_line}"
    )
    footer = f"\n⏰ Hạn nộp: {deadline}\n🔗 <a href=\"{url}\">Xem chi tiết</a>"

    body = html.escape(summary.strip()) if summary else ""
    if body:
        max_body = TELEGRAM_MAX_CHARS - len(header) - len(footer) - 2
        if len(body) > max_body:
            body = body[: max(0, max_body - 3)].rstrip() + "..."
        return f"{header}\n{body}\n{footer}"

    return f"{header}{footer}"


def format_digest_html(jobs: list[Job]) -> list[str]:
    """Group jobs into 1 or more Telegram digest messages (each <= 4096 chars)."""
    if not jobs:
        return []

    header = f"<b>📋 Tổng hợp {len(jobs)} việc làm mới</b>\n\n"
    messages: list[str] = []
    current = header

    for idx, job in enumerate(jobs, start=1):
        title = html.escape(job.title or "Untitled")
        company = html.escape(job.company or "N/A")
        salary = html.escape(job.salary or "Thỏa thuận")
        url = html.escape(job.url, quote=True)
        line = f"{idx}. <a href=\"{url}\"><b>{title}</b></a> — {company} ({salary})\n"

        if len(current) + len(line) > TELEGRAM_MAX_CHARS:
            messages.append(current.rstrip())
            current = line
        else:
            current += line

    if current.strip():
        messages.append(current.rstrip())
    return messages


def send_telegram_html(
    bot_token: str,
    chat_id: str,
    text: str,
    client: httpx.Client | None = None,
    reply_markup: dict[str, Any] | None = None,
) -> bool:
    """Send an HTML message via Telegram Bot API with 429 retry_after handling."""
    if not bot_token or not chat_id:
        raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required.")

    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    owns_client = client is None
    http = client or httpx.Client(timeout=20.0)
    try:
        for attempt in range(3):
            resp = http.post(api_url, json=payload)
            if resp.status_code == 200:
                return True
            if resp.status_code == 429:
                retry_after = 5
                try:
                    retry_after = int(resp.json().get("parameters", {}).get("retry_after", 5))
                except Exception:
                    pass
                logger.warning("Telegram 429 rate limit; sleeping %ss (attempt %d)", retry_after, attempt + 1)
                time.sleep(retry_after)
                continue
            logger.error("Telegram API error %d: %s", resp.status_code, resp.text[:300])
            return False
        return False
    finally:
        if owns_client:
            http.close()


def notify_jobs_instant(
    jobs: list[Job],
    bot_token: str,
    chat_id: str,
    summaries: dict[tuple[str, str], str],
    scores: dict[tuple[str, str], int | None] | None = None,
    dry_run: bool = False,
    with_buttons: bool = True,
) -> tuple[list[Job], list[Job]]:
    """Send jobs sequentially with 1s delay. Returns (sent_jobs, failed_jobs)."""
    sent: list[Job] = []
    failed: list[Job] = []
    scores = scores or {}

    if dry_run:
        for job in jobs:
            key = (job.source, job.job_id)
            msg = format_job_html(job, summaries.get(key, ""), match_score=scores.get(key))
            print("=" * 60)
            print(msg)
            sent.append(job)
        return sent, failed

    with httpx.Client(timeout=20.0) as client:
        for idx, job in enumerate(jobs):
            key = (job.source, job.job_id)
            msg = format_job_html(job, summaries.get(key, ""), match_score=scores.get(key))
            markup = build_job_inline_keyboard(job) if with_buttons else None
            try:
                ok = send_telegram_html(bot_token, chat_id, msg, client=client, reply_markup=markup)
            except Exception as exc:
                logger.error("Failed to send job %s/%s: %s", job.source, job.job_id, exc)
                ok = False

            if ok:
                sent.append(job)
            else:
                failed.append(job)

            if idx < len(jobs) - 1:
                time.sleep(TELEGRAM_SEND_DELAY_SEC)

    return sent, failed


def notify_jobs_digest(
    jobs: list[Job],
    bot_token: str,
    chat_id: str,
    dry_run: bool = False,
) -> bool:
    """Send all jobs as compact digest message(s). Returns True if all chunks succeeded."""
    chunks = format_digest_html(jobs)
    if not chunks:
        return True

    if dry_run:
        for chunk in chunks:
            print("=" * 60)
            print(chunk)
        return True

    with httpx.Client(timeout=20.0) as client:
        for idx, chunk in enumerate(chunks):
            ok = send_telegram_html(bot_token, chat_id, chunk, client=client)
            if not ok:
                return False
            if idx < len(chunks) - 1:
                time.sleep(TELEGRAM_SEND_DELAY_SEC)
    return True

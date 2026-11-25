from __future__ import annotations

import html
import logging
import re

import httpx

from src.config import AppConfig
from src.filters import strip_accents
from src.models import Job

logger = logging.getLogger(__name__)

MAX_SUMMARY_CHARS = 600
GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash:generateContent"
)


def summarize_rule_based(job: Job, max_chars: int = MAX_SUMMARY_CHARS) -> str:
    """Extract a concise plain-text summary from job description."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", job.description or ""))
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]

    if not lines:
        return ""

    out: list[str] = []
    total = 0
    for line in lines:
        addition = len(line) + (1 if out else 0)
        if total + addition > max_chars:
            remaining = max_chars - total - 3
            if remaining > 30:
                out.append(line[:remaining].rstrip() + "...")
            break
        out.append(line)
        total += addition

    return "\n".join(out)


def summarize_llm(
    job: Job,
    api_key: str,
    max_chars: int = MAX_SUMMARY_CHARS,
    timeout: float = 15.0,
) -> str:
    """Summarize job via Gemini API Free Tier; falls back to rule-based on error."""
    if not api_key:
        return summarize_rule_based(job, max_chars=max_chars)

    prompt = (
        "Tóm tắt tin tuyển dụng sau bằng tiếng Việt ngắn gọn (tối đa 550 ký tự), "
        "gồm: 3-5 yêu cầu chính và 1-2 phúc lợi nổi bật. Không dùng markdown.\n\n"
        f"Vị trí: {job.title}\n"
        f"Công ty: {job.company}\n"
        f"Lương: {job.salary or 'Thỏa thuận'}\n"
        f"Kỹ năng: {', '.join(job.skills)}\n"
        f"Mô tả:\n{(job.description or '')[:3000]}"
    )

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                GEMINI_ENDPOINT,
                params={"key": api_key},
                json={"contents": [{"parts": [{"text": prompt}]}]},
            )
            resp.raise_for_status()
            data = resp.json()
            text = (
                data["candidates"][0]["content"]["parts"][0]["text"].strip()
            )
            if text:
                return text[:max_chars]
    except Exception as exc:
        logger.warning("LLM summary failed (%s); falling back to rule-based.", exc)

    return summarize_rule_based(job, max_chars=max_chars)


def summarize_job(job: Job, cfg: AppConfig) -> str:
    if not cfg.summary.enabled:
        return ""
    if cfg.summary.method == "llm":
        return summarize_llm(job, api_key=cfg.llm_api_key)
    return summarize_rule_based(job)


def score_job_match(job: Job, target_keywords: list[str]) -> int | None:
    """Return 0-100 match score against target skills/keywords, or None if no targets."""
    cleaned = [strip_accents(k) for k in target_keywords if k.strip()]
    cleaned = list(dict.fromkeys(cleaned))
    if not cleaned:
        return None

    haystack = strip_accents(" ".join([job.title, *job.skills, (job.description or "")[:1000]]))
    hits = sum(1 for kw in cleaned if kw in haystack)
    return round((hits / len(cleaned)) * 100)

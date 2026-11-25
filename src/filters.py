from __future__ import annotations

import re
import unicodedata

from src.config import FilterConfig
from src.models import Job

USD_TO_MILLION_VND = 0.025  # ~25,000 VND per USD -> 0.025 million VND

NEGOTIABLE_MARKERS = (
    "thỏa thuận",
    "thoa thuan",
    "cạnh tranh",
    "canh tranh",
    "negotiable",
    "competitive",
    "you'll love it",
    "sign in to view",
    "đăng nhập",
)


def normalize_text(text: str) -> str:
    s = unicodedata.normalize("NFKC", text or "").strip().lower()
    return re.sub(r"\s+", " ", s)


def strip_accents(text: str) -> str:
    s = unicodedata.normalize("NFD", text or "")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return s.replace("đ", "d").replace("Đ", "D").lower().strip()


def _parse_number(raw: str) -> float:
    cleaned = raw.replace(",", "").strip().lower()
    if cleaned.endswith("k"):
        return float(cleaned[:-1]) * 1000.0
    return float(cleaned)


def parse_salary_million(salary_text: str | None) -> tuple[float | None, float | None]:
    """Parse salary string into (min_million_vnd, max_million_vnd).

    Returns (None, None) if negotiable, hidden, or unparseable.
    """
    if not salary_text:
        return None, None

    low = normalize_text(salary_text)
    if any(marker in low for marker in NEGOTIABLE_MARKERS):
        return None, None

    is_usd = "$" in low or "usd" in low
    is_vnd_million = any(tok in low for tok in ("triệu", "trieu", "tr", "m"))

    num_tokens = re.findall(r"\d+(?:[.,]\d+)?k?", low)
    if not num_tokens:
        return None, None

    values: list[float] = []
    for tok in num_tokens[:2]:
        # Handle Vietnamese thousands separator like "1,500" or "2.000"
        if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", tok):
            val = float(re.sub(r"[.,]", "", tok))
        else:
            val = _parse_number(tok.replace(",", "."))
        values.append(val)

    def to_million(v: float) -> float:
        if is_usd:
            return round(v * USD_TO_MILLION_VND, 2)
        if is_vnd_million:
            return round(v, 2)
        if v >= 1_000_000:
            return round(v / 1_000_000.0, 2)
        if v >= 500:  # likely USD without symbol
            return round(v * USD_TO_MILLION_VND, 2)
        return round(v, 2)

    converted = [to_million(v) for v in values]

    if len(converted) >= 2:
        lo, hi = min(converted[0], converted[1]), max(converted[0], converted[1])
        return lo, hi

    single = converted[0]
    if any(tok in low for tok in ("up to", "tới", "toi", "đến", "den", "dưới", "duoi", "max")):
        return None, single
    if any(tok in low for tok in ("trên", "tren", "từ", "tu", "from", "min", "+")):
        return single, None
    return single, single


def _matches_any(haystack: str, keywords: list[str]) -> bool:
    h_norm = normalize_text(haystack)
    h_plain = strip_accents(haystack)
    for kw in keywords:
        k_norm = normalize_text(kw)
        k_plain = strip_accents(kw)
        if not k_norm:
            continue
        if k_norm in h_norm or k_plain in h_plain:
            return True
    return False


def _passes_salary(
    salary_min: float | None,
    salary_max: float | None,
    filters: FilterConfig,
) -> bool:
    if filters.min_salary_million_vnd is None:
        return True
    if salary_min is None and salary_max is None:
        return filters.allow_negotiable_salary
    upper = salary_max if salary_max is not None else salary_min
    assert upper is not None
    return upper >= filters.min_salary_million_vnd


def pre_filter(job: Job, filters: FilterConfig) -> bool:
    """Fast filter on list-page fields before fetching detail page."""
    if filters.blacklist_companies and _matches_any(job.company, filters.blacklist_companies):
        return False

    if filters.exclude_keywords and _matches_any(job.title, filters.exclude_keywords):
        return False

    if filters.locations and job.location:
        if not _matches_any(job.location, filters.locations):
            return False

    if job.salary is not None:
        s_min, s_max = job.salary_min_million, job.salary_max_million
        if s_min is None and s_max is None:
            s_min, s_max = parse_salary_million(job.salary)
        if not _passes_salary(s_min, s_max, filters):
            return False

    return True


def full_filter(job: Job, filters: FilterConfig) -> bool:
    """Full filter applied after detail page is fetched."""
    if not pre_filter(job, filters):
        return False

    if filters.locations and not _matches_any(job.location, filters.locations):
        return False

    if filters.include_keywords:
        combined = " ".join([job.title, *job.skills])
        if not _matches_any(combined, filters.include_keywords):
            return False

    s_min, s_max = job.salary_min_million, job.salary_max_million
    if s_min is None and s_max is None and job.salary:
        s_min, s_max = parse_salary_million(job.salary)
    if not _passes_salary(s_min, s_max, filters):
        return False

    return True

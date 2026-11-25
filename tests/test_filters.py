from __future__ import annotations

import pytest

from src.config import FilterConfig
from src.filters import full_filter, parse_salary_million, pre_filter
from src.models import Job


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("15 - 25 triệu", (15.0, 25.0)),
        ("Up to 30 triệu", (None, 30.0)),
        ("Tới 30 tr", (None, 30.0)),
        ("Trên 20 triệu", (20.0, None)),
        ("$1000 - $2000", (25.0, 50.0)),
        ("800 - 2,000 USD", (20.0, 50.0)),
        ("$2k - $3k", (50.0, 75.0)),
        ("Thỏa thuận", (None, None)),
        ("You'll love it", (None, None)),
        (None, (None, None)),
    ],
)
def test_parse_salary_million(raw: str | None, expected: tuple[float | None, float | None]) -> None:
    assert parse_salary_million(raw) == expected


def _make_job(**overrides: object) -> Job:
    base = dict(
        source="itviec",
        job_id="python-dev-1234",
        title="Backend Python Developer",
        company="Acme Corp",
        location="Ho Chi Minh",
        salary="20 - 30 triệu",
        url="https://itviec.com/it-jobs/python-dev-1234",
        skills=["Python", "FastAPI", "PostgreSQL"],
    )
    base.update(overrides)
    return Job(**base)  # type: ignore[arg-type]


def test_pre_filter_exclude_and_blacklist() -> None:
    cfg = FilterConfig(exclude_keywords=["senior", "lead"], blacklist_companies=["BadCo"])
    assert pre_filter(_make_job(), cfg) is True
    assert pre_filter(_make_job(title="Senior Python Developer"), cfg) is False
    assert pre_filter(_make_job(company="BadCo Vietnam"), cfg) is False


def test_full_filter_include_salary_and_location() -> None:
    cfg = FilterConfig(
        include_keywords=["fastapi"],
        min_salary_million_vnd=25.0,
        allow_negotiable_salary=False,
        locations=["Hồ Chí Minh"],
    )
    assert full_filter(_make_job(), cfg) is True
    assert full_filter(_make_job(skills=["Java"]), cfg) is False
    assert full_filter(_make_job(salary="Thỏa thuận"), cfg) is False
    assert full_filter(_make_job(salary="10 - 15 triệu"), cfg) is False
    assert full_filter(_make_job(location="Ha Noi"), cfg) is False

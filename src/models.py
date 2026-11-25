from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Job:
    source: str
    job_id: str
    title: str
    company: str
    location: str
    salary: str | None
    url: str
    description: str = ""
    skills: list[str] = field(default_factory=list)
    posted_at: str | None = None
    deadline: str | None = None
    salary_min_million: float | None = None
    salary_max_million: float | None = None
    summary: str | None = None

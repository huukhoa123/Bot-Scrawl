from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

DEFAULT_CONFIG_PATH = Path("config.yaml")
DEFAULT_DB_PATH = Path("data/jobs.db")


@dataclass
class SearchQuery:
    keyword: str
    location: str = ""
    max_pages: int = 3


@dataclass
class FilterConfig:
    include_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    min_salary_million_vnd: float | None = None
    allow_negotiable_salary: bool = True
    blacklist_companies: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)

    def compute_hash(self) -> str:
        payload = {
            "include_keywords": sorted(k.strip().lower() for k in self.include_keywords),
            "exclude_keywords": sorted(k.strip().lower() for k in self.exclude_keywords),
            "min_salary_million_vnd": self.min_salary_million_vnd,
            "allow_negotiable_salary": self.allow_negotiable_salary,
            "blacklist_companies": sorted(c.strip().lower() for c in self.blacklist_companies),
            "locations": sorted(loc.strip().lower() for loc in self.locations),
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class NotifyConfig:
    mode: str = "instant"
    digest_time: str = "08:00"
    max_jobs_per_run: int = 20


@dataclass
class SummaryConfig:
    enabled: bool = True
    method: str = "rule"


@dataclass
class AppConfig:
    searches: list[SearchQuery]
    filters: FilterConfig
    notify: NotifyConfig
    summary: SummaryConfig
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    llm_api_key: str = ""
    db_path: Path = DEFAULT_DB_PATH

    @property
    def filter_hash(self) -> str:
        return self.filters.compute_hash()


def load_config(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    env_path: str | Path | None = None,
) -> AppConfig:
    load_dotenv(dotenv_path=env_path)

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}

    searches = [
        SearchQuery(
            keyword=str(item.get("keyword", "")).strip(),
            location=str(item.get("location", "")).strip(),
            max_pages=max(1, int(item.get("max_pages", 3))),
        )
        for item in (data.get("searches") or [])
    ]

    f_raw = data.get("filters") or {}
    min_sal = f_raw.get("min_salary_million_vnd")
    filters = FilterConfig(
        include_keywords=list(f_raw.get("include_keywords") or []),
        exclude_keywords=list(f_raw.get("exclude_keywords") or []),
        min_salary_million_vnd=float(min_sal) if min_sal is not None else None,
        allow_negotiable_salary=bool(f_raw.get("allow_negotiable_salary", True)),
        blacklist_companies=list(f_raw.get("blacklist_companies") or []),
        locations=list(f_raw.get("locations") or []),
    )

    n_raw = data.get("notify") or {}
    notify = NotifyConfig(
        mode=str(n_raw.get("mode", "instant")),
        digest_time=str(n_raw.get("digest_time", "08:00")),
        max_jobs_per_run=int(n_raw.get("max_jobs_per_run", 20)),
    )

    s_raw = data.get("summary") or {}
    summary = SummaryConfig(
        enabled=bool(s_raw.get("enabled", True)),
        method=str(s_raw.get("method", "rule")),
    )

    db_path_env = os.getenv("JOB_BOT_DB_PATH")
    db_path = Path(db_path_env) if db_path_env else DEFAULT_DB_PATH

    return AppConfig(
        searches=searches,
        filters=filters,
        notify=notify,
        summary=summary,
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        db_path=db_path,
    )

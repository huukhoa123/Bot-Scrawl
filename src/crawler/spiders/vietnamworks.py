from __future__ import annotations

import html
import json
import logging
import re
from typing import Any, AsyncIterator, Iterator

import scrapy

from src.crawler.items import JobItem
from src.crawler.spiders.base import BaseJobSpider
from src.filters import strip_accents
from src.models import Job

logger = logging.getLogger(__name__)

# Centralized endpoints & constants for VietnamWorks Search API
API_SEARCH_URL = "https://ms.vietnamworks.com/job-search/v1.0/search"
HITS_PER_PAGE = 20

CITY_IDS: dict[str, int] = {
    "ho chi minh": 29,
    "hcm": 29,
    "tp hcm": 29,
    "tp.hcm": 29,
    "ha noi": 24,
    "hn": 24,
    "da nang": 17,
    "dn": 17,
}


def build_vnw_payload(keyword: str = "", location: str = "", page: int = 0) -> dict[str, Any]:
    kw = (keyword or "").strip()
    if kw.startswith(("http://", "https://")):
        kw = ""

    filters: list[dict[str, Any]] = []
    loc_key = strip_accents(location)
    if loc_key in CITY_IDS:
        filters.append({"field": "workingLocations.cityId", "value": str(CITY_IDS[loc_key])})

    return {
        "userId": 0,
        "query": kw,
        "filter": filters,
        "ranges": [],
        "order": [],
        "hitsPerPage": HITS_PER_PAGE,
        "page": page,
    }


def _clean_html(raw: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw or ""))
    return re.sub(r"\s+", " ", text).strip()


def parse_vnw_api_payload(payload: dict[str, Any]) -> list[JobItem]:
    items: list[JobItem] = []
    for entry in payload.get("data") or []:
        if not isinstance(entry, dict) or not entry.get("jobId"):
            continue
        job_id = str(entry["jobId"])
        title = str(entry.get("jobTitle") or "").strip()
        company = str(entry.get("companyName") or "").strip()
        salary = str(entry.get("prettySalary") or "").strip() or None
        url = str(entry.get("jobUrl") or f"https://www.vietnamworks.com/job-{job_id}-jv").strip()

        locs: list[str] = []
        for wl in entry.get("workingLocations") or []:
            if isinstance(wl, dict):
                city = wl.get("cityNameVI") or wl.get("cityName") or wl.get("address")
                if city:
                    locs.append(str(city).strip())
        location = ", ".join(dict.fromkeys(locs))

        skills = [
            str(s["skillName"]).strip()
            for s in (entry.get("skills") or [])
            if isinstance(s, dict) and s.get("skillName")
        ]

        desc_parts = [
            _clean_html(str(entry.get("jobDescription") or "")),
            _clean_html(str(entry.get("jobRequirement") or "")),
        ]
        description = "\n".join(p for p in desc_parts if p)

        approved = str(entry.get("approvedOn") or "")
        expired = str(entry.get("expiredOn") or "")
        posted_at = approved[:10] if len(approved) >= 10 else None
        deadline = expired[:10] if len(expired) >= 10 else None

        items.append(
            JobItem(
                source="vietnamworks",
                job_id=job_id,
                title=title,
                company=company,
                location=location,
                salary=salary,
                url=url,
                description=description,
                skills=skills,
                posted_at=posted_at,
                deadline=deadline,
            )
        )
    return items


class VietnamWorksSpider(BaseJobSpider):
    name = "vietnamworks"
    source_name = "vietnamworks"
    allowed_domains = ["ms.vietnamworks.com", "vietnamworks.com", "www.vietnamworks.com"]
    custom_settings = {
        "ROBOTSTXT_OBEY": False,  # Public JSON Search API endpoint
    }

    async def start(self) -> AsyncIterator[scrapy.Request]:
        for query in self.get_active_searches():
            if query.keyword.startswith(("http://", "https://")):
                continue
            body = json.dumps(build_vnw_payload(query.keyword, query.location, page=0))
            logger.info(
                "[vietnamworks] Queuing API search: %s (%s, max_pages=%d)",
                query.keyword or "ALL",
                query.location or "ALL",
                query.max_pages,
            )
            yield scrapy.Request(
                url=API_SEARCH_URL,
                method="POST",
                body=body,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                callback=self.parse_api,
                dont_filter=True,
                cb_kwargs={
                    "keyword": query.keyword,
                    "location": query.location,
                    "page": 0,
                    "max_pages": query.max_pages,
                },
            )

    def parse_api(
        self,
        response: scrapy.http.Response,
        keyword: str = "",
        location: str = "",
        page: int = 0,
        max_pages: int = 3,
    ) -> Iterator[JobItem | scrapy.Request]:
        self.check_blocked(response)
        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError:
            logger.error("[vietnamworks] Invalid JSON at %s", response.url)
            return

        items = parse_vnw_api_payload(payload)
        logger.info("[vietnamworks] Page %d/%d: parsed %d jobs", page + 1, max_pages, len(items))

        for item in items:
            summary_job = Job(
                source="vietnamworks",
                job_id=str(item["job_id"]),
                title=str(item["title"]),
                company=str(item["company"]),
                location=str(item["location"]),
                salary=item["salary"],
                url=str(item["url"]),
                skills=list(item["skills"]),
            )
            if not self.should_fetch_detail(summary_job):
                continue
            yield item

        if page + 1 < max_pages and len(items) >= HITS_PER_PAGE:
            next_body = json.dumps(build_vnw_payload(keyword, location, page=page + 1))
            yield scrapy.Request(
                url=API_SEARCH_URL,
                method="POST",
                body=next_body,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                callback=self.parse_api,
                dont_filter=True,
                cb_kwargs={
                    "keyword": keyword,
                    "location": location,
                    "page": page + 1,
                    "max_pages": max_pages,
                },
            )

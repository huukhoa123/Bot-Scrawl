from __future__ import annotations

import json
import logging
import re
from typing import Any, AsyncIterator, Iterator
from urllib.parse import urlencode

import scrapy

from src.crawler.items import JobItem
from src.crawler.spiders.base import BaseJobSpider
from src.filters import strip_accents
from src.models import Job

logger = logging.getLogger(__name__)

# Centralized endpoints & constants for TopDev v2 JSON API
API_BASE_URL = "https://api.topdev.vn/td/v2/jobs"
JOB_FIELDS = (
    "id,title,salary,company,addresses,skills_str,detail_url,"
    "published,requirements_arr,benefits,content"
)
COMPANY_FIELDS = "display_name"

REGION_IDS: dict[str, str] = {
    "ho chi minh": "79",
    "hcm": "79",
    "tp hcm": "79",
    "tp.hcm": "79",
    "ha noi": "01",
    "hn": "01",
    "da nang": "48",
    "dn": "48",
}


def build_topdev_api_url(keyword: str = "", location: str = "", page: int = 1) -> str:
    params: dict[str, str] = {
        "fields[job]": JOB_FIELDS,
        "fields[company]": COMPANY_FIELDS,
        "page": str(page),
        "locale": "vi_VN",
        "ordering": "jobs_new",
    }
    kw = (keyword or "").strip()
    if kw and not kw.startswith(("http://", "https://")):
        params["q"] = kw

    loc_key = strip_accents(location)
    if loc_key in REGION_IDS:
        params["region_ids"] = REGION_IDS[loc_key]

    return f"{API_BASE_URL}?{urlencode(params)}"


def _extract_salary_text(salary_obj: Any) -> str | None:
    if not isinstance(salary_obj, dict):
        return None
    if str(salary_obj.get("is_negotiable", "0")) == "1":
        return "Thỏa thuận"
    val = salary_obj.get("value")
    if isinstance(val, str) and val.strip() and "*" not in val:
        return val.strip()
    min_f = salary_obj.get("min_filter")
    max_f = salary_obj.get("max_filter")
    if isinstance(min_f, (int, float)) and isinstance(max_f, (int, float)) and max_f > 0:
        lo = round(min_f / 1_000_000)
        hi = round(max_f / 1_000_000)
        if lo > 0 and hi > 0:
            return f"{lo} - {hi} triệu"
        if hi > 0:
            return f"Up to {hi} triệu"
    return "Thỏa thuận"


def _strip_html(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    return re.sub(r"\s+", " ", text).strip()


def parse_topdev_api_payload(payload: dict[str, Any]) -> list[JobItem]:
    items: list[JobItem] = []
    for entry in payload.get("data") or []:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        job_id = str(entry["id"])
        title = str(entry.get("title") or "").strip()
        company_obj = entry.get("company")
        company = (
            str(company_obj.get("display_name") or "").strip()
            if isinstance(company_obj, dict)
            else ""
        )

        addr_obj = entry.get("addresses")
        location = ""
        if isinstance(addr_obj, dict):
            regions = addr_obj.get("address_region_array") or []
            if isinstance(regions, list) and regions:
                location = ", ".join(str(r).strip() for r in regions if str(r).strip())

        salary = _extract_salary_text(entry.get("salary"))
        url = str(entry.get("detail_url") or f"https://topdev.vn/viec-lam/{job_id}").strip()

        raw_skills = str(entry.get("skills_str") or "")
        skills = [s.strip() for s in raw_skills.split(",") if s.strip()]

        desc_parts: list[str] = []
        if entry.get("content"):
            desc_parts.append(_strip_html(str(entry["content"])))
        req_arr = entry.get("requirements_arr")
        if isinstance(req_arr, list):
            for req in req_arr:
                if isinstance(req, dict) and req.get("value"):
                    desc_parts.append(_strip_html(str(req["value"])))
                elif isinstance(req, str) and req.strip():
                    desc_parts.append(_strip_html(req))
        benefits = entry.get("benefits")
        if isinstance(benefits, list):
            for b in benefits:
                if isinstance(b, dict) and b.get("value"):
                    desc_parts.append(_strip_html(str(b["value"])))
        description = "\n".join(p for p in desc_parts if p)

        pub = entry.get("published")
        posted_at = str(pub.get("date")) if isinstance(pub, dict) and pub.get("date") else None

        items.append(
            JobItem(
                source="topdev",
                job_id=job_id,
                title=title,
                company=company,
                location=location,
                salary=salary,
                url=url,
                description=description,
                skills=skills,
                posted_at=posted_at,
                deadline=None,
            )
        )
    return items


class TopdevSpider(BaseJobSpider):
    name = "topdev"
    source_name = "topdev"
    allowed_domains = ["api.topdev.vn", "topdev.vn"]
    custom_settings = {
        "ROBOTSTXT_OBEY": False,  # Public JSON API endpoint (api.topdev.vn)
    }

    async def start(self) -> AsyncIterator[scrapy.Request]:
        for query in self.get_active_searches():
            if query.keyword.startswith(("http://", "https://")) and "topdev.vn" not in query.keyword:
                continue
            url = build_topdev_api_url(query.keyword, query.location, page=1)
            logger.info(
                "[topdev] Queuing API search: %s (%s, max_pages=%d)",
                query.keyword or "ALL",
                query.location or "ALL",
                query.max_pages,
            )
            yield scrapy.Request(
                url=url,
                callback=self.parse_api,
                cb_kwargs={
                    "keyword": query.keyword,
                    "location": query.location,
                    "page": 1,
                    "max_pages": query.max_pages,
                },
            )

    def parse_api(
        self,
        response: scrapy.http.Response,
        keyword: str = "",
        location: str = "",
        page: int = 1,
        max_pages: int = 3,
    ) -> Iterator[JobItem | scrapy.Request]:
        self.check_blocked(response)
        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError:
            logger.error("[topdev] Invalid JSON at %s", response.url)
            return

        items = parse_topdev_api_payload(payload)
        logger.info("[topdev] Page %d/%d: parsed %d jobs", page, max_pages, len(items))

        for item in items:
            summary_job = Job(
                source="topdev",
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

        if page < max_pages and items:
            next_url = build_topdev_api_url(keyword, location, page=page + 1)
            yield scrapy.Request(
                url=next_url,
                callback=self.parse_api,
                cb_kwargs={
                    "keyword": keyword,
                    "location": location,
                    "page": page + 1,
                    "max_pages": max_pages,
                },
            )

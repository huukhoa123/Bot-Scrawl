from __future__ import annotations

import json
import logging
import re
from typing import Any, AsyncIterator, Iterator
from urllib.parse import quote, urlsplit, urlunsplit

import scrapy

from src.crawler.items import JobItem
from src.crawler.spiders.base import BaseJobSpider
from src.filters import strip_accents
from src.models import Job

logger = logging.getLogger(__name__)

# Centralized selectors & endpoints for ITviec
BASE_URL = "https://itviec.com"
LIST_CARD_SELECTOR = "div.job-card"
CARD_URL_SELECTOR = "h3::attr(data-url)"
CARD_TITLE_SELECTOR = "h3 a::text, h3::text"
CARD_COMPANY_SELECTOR = "a.text-rich-grey::text"
CARD_LOCATION_SELECTOR = "div.text-rich-grey.text-truncate::attr(title), div.text-rich-grey.text-truncate::text"
CARD_SALARY_SELECTOR = ".salary span::text"
JSON_LD_XPATH = '//script[@type="application/ld+json"]/text()'

LOCATION_SLUGS: dict[str, str] = {
    "ho chi minh": "ho-chi-minh-hcm",
    "hcm": "ho-chi-minh-hcm",
    "tp hcm": "ho-chi-minh-hcm",
    "tp.hcm": "ho-chi-minh-hcm",
    "ha noi": "ha-noi",
    "hn": "ha-noi",
    "da nang": "da-nang",
    "dn": "da-nang",
}


def normalize_itviec_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def extract_itviec_job_id(url: str) -> str:
    path = urlsplit(url).path.rstrip("/")
    slug = path.rsplit("/", 1)[-1]
    return slug


def build_itviec_search_url(keyword: str, location: str = "") -> str:
    raw_kw = (keyword or "").strip()
    if raw_kw.startswith(("http://", "https://")):
        return raw_kw

    kw_slug = re.sub(r"[^a-z0-9]+", "-", strip_accents(raw_kw)).strip("-")
    kw_part = quote(kw_slug) if kw_slug else ""
    loc_key = strip_accents(location)
    loc_slug = LOCATION_SLUGS.get(loc_key, "")

    if kw_part and loc_slug:
        return f"{BASE_URL}/it-jobs/{kw_part}/{loc_slug}"
    if kw_part:
        return f"{BASE_URL}/it-jobs/{kw_part}"
    if loc_slug:
        return f"{BASE_URL}/it-jobs/{loc_slug}"
    return f"{BASE_URL}/it-jobs"


def parse_itviec_list_cards(response: scrapy.http.Response) -> list[Job]:
    jobs: list[Job] = []
    for card in response.css(LIST_CARD_SELECTOR):
        raw_url = card.css(CARD_URL_SELECTOR).get()
        if not raw_url:
            raw_url = card.css('a[href*="/it-jobs/"]::attr(href)').get()
        if not raw_url:
            continue

        full_url = normalize_itviec_url(response.urljoin(raw_url))
        job_id = extract_itviec_job_id(full_url)
        title_parts = [t.strip() for t in card.css(CARD_TITLE_SELECTOR).getall() if t.strip()]
        title = title_parts[0] if title_parts else job_id

        company_parts = [c.strip() for c in card.css(CARD_COMPANY_SELECTOR).getall() if c.strip()]
        company = company_parts[0] if company_parts else ""

        loc_parts = [l.strip() for l in card.css(CARD_LOCATION_SELECTOR).getall() if l.strip()]
        location = loc_parts[0] if loc_parts else ""

        sal_parts = [s.strip() for s in card.css(CARD_SALARY_SELECTOR).getall() if s.strip()]
        salary = " ".join(sal_parts) if sal_parts else None

        jobs.append(
            Job(
                source="itviec",
                job_id=job_id,
                title=title,
                company=company,
                location=location,
                salary=salary,
                url=full_url,
            )
        )
    return jobs


def parse_itviec_detail_response(
    response: scrapy.http.Response,
    fallback: Job | None = None,
) -> JobItem:
    ld_job: dict[str, Any] | None = None
    for raw in response.xpath(JSON_LD_XPATH).getall():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            ld_job = data
            break

    url = normalize_itviec_url(response.url)
    job_id = fallback.job_id if fallback else extract_itviec_job_id(url)

    title = (fallback.title if fallback else "")
    company = (fallback.company if fallback else "")
    location = (fallback.location if fallback else "")
    salary = (fallback.salary if fallback else None)
    description = ""
    skills: list[str] = []
    posted_at: str | None = None
    deadline: str | None = None

    if ld_job:
        title = str(ld_job.get("title") or title).strip()
        org = ld_job.get("hiringOrganization")
        if isinstance(org, dict) and org.get("name"):
            company = str(org["name"]).strip()

        loc_data = ld_job.get("jobLocation")
        if isinstance(loc_data, list) and loc_data:
            regions: list[str] = []
            for entry in loc_data:
                addr = entry.get("address") if isinstance(entry, dict) else None
                if isinstance(addr, dict):
                    region = addr.get("addressRegion") or addr.get("addressLocality")
                    if region and str(region).lower() != "not available":
                        regions.append(str(region).strip())
            if regions:
                location = ", ".join(dict.fromkeys(regions))

        base_salary = ld_job.get("baseSalary")
        if isinstance(base_salary, dict):
            val_obj = base_salary.get("value")
            if isinstance(val_obj, dict) and val_obj.get("value"):
                salary = str(val_obj["value"]).strip()

        raw_skills = ld_job.get("skills")
        if isinstance(raw_skills, str):
            skills = [s.strip() for s in raw_skills.split(",") if s.strip()]
        elif isinstance(raw_skills, list):
            skills = [str(s).strip() for s in raw_skills if str(s).strip()]

        description = str(ld_job.get("description") or "").strip()
        posted_at = ld_job.get("datePosted")
        deadline = ld_job.get("validThrough")

    if not description:
        text_nodes = [t.strip() for t in response.css("div.job-details__paragraph ::text").getall() if t.strip()]
        description = "\n".join(text_nodes)

    return JobItem(
        source="itviec",
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


class ItviecSpider(BaseJobSpider):
    name = "itviec"
    source_name = "itviec"
    allowed_domains = ["itviec.com"]

    async def start(self) -> AsyncIterator[scrapy.Request]:
        for query in self.get_active_searches():
            url = build_itviec_search_url(query.keyword, query.location)
            logger.info(
                "[itviec] Queuing search: %s (%s, max_pages=%d) -> %s",
                query.keyword or "ALL",
                query.location or "ALL",
                query.max_pages,
                url,
            )
            yield scrapy.Request(
                url=url,
                callback=self.parse_list,
                cb_kwargs={"page": 1, "max_pages": query.max_pages},
            )

    def parse_list(
        self,
        response: scrapy.http.Response,
        page: int = 1,
        max_pages: int = 3,
    ) -> Iterator[scrapy.Request]:
        self.check_blocked(response)
        cards = parse_itviec_list_cards(response)
        logger.info("[itviec] Page %d/%d: found %d cards on %s", page, max_pages, len(cards), response.url)

        for summary_job in cards:
            if not self.should_fetch_detail(summary_job):
                continue
            yield response.follow(
                summary_job.url,
                callback=self.parse_detail,
                cb_kwargs={"fallback": summary_job},
            )

        if page < max_pages and cards:
            next_href = response.css('a[rel="next"]::attr(href), .page.next a::attr(href)').get()
            if next_href:
                yield response.follow(
                    next_href,
                    callback=self.parse_list,
                    cb_kwargs={"page": page + 1, "max_pages": max_pages},
                )

    def parse_detail(self, response: scrapy.http.Response, fallback: Job | None = None) -> JobItem:
        self.check_blocked(response)
        return parse_itviec_detail_response(response, fallback=fallback)

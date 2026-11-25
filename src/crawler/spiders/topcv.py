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

# Centralized selectors & endpoints for TopCV
BASE_URL = "https://www.topcv.vn"
LIST_CARD_SELECTOR = "div.job-item-search-result, div.job-item-2"
CARD_LINK_SELECTOR = "h3.title a::attr(href), a.title::attr(href)"
CARD_TITLE_SELECTOR = "h3.title a span::text, h3.title a::text, a.title::text"
CARD_COMPANY_SELECTOR = "a.company::text, .company-name::text"
CARD_LOCATION_SELECTOR = "label.address::text, .address::text"
CARD_SALARY_SELECTOR = "label.title-salary::text, .salary::text"
JSON_LD_XPATH = '//script[@type="application/ld+json"]/text()'


def normalize_topcv_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def extract_topcv_job_id(url: str) -> str:
    path = urlsplit(url).path.rstrip("/")
    m = re.search(r"/(\d+)\.html$", path)
    if m:
        return m.group(1)
    return path.rsplit("/", 1)[-1].removesuffix(".html")


def build_topcv_search_url(keyword: str, location: str = "") -> str:
    kw_slug = re.sub(r"[^a-z0-9]+", "-", strip_accents(keyword)).strip("-")
    if kw_slug:
        return f"{BASE_URL}/tim-viec-lam-{quote(kw_slug)}"
    return f"{BASE_URL}/viec-lam-it"


def parse_topcv_list_cards(response: scrapy.http.Response) -> list[Job]:
    jobs: list[Job] = []
    for card in response.css(LIST_CARD_SELECTOR):
        raw_url = card.css(CARD_LINK_SELECTOR).get()
        if not raw_url:
            continue
        full_url = normalize_topcv_url(response.urljoin(raw_url))
        job_id = extract_topcv_job_id(full_url)

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
                source="topcv",
                job_id=job_id,
                title=title,
                company=company,
                location=location,
                salary=salary,
                url=full_url,
            )
        )
    return jobs


def parse_topcv_detail_response(
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

    url = normalize_topcv_url(response.url)
    job_id = fallback.job_id if fallback else extract_topcv_job_id(url)

    title = fallback.title if fallback else ""
    company = fallback.company if fallback else ""
    location = fallback.location if fallback else ""
    salary = fallback.salary if fallback else None
    description = ""
    skills: list[str] = []
    posted_at: str | None = None
    deadline: str | None = None

    if ld_job:
        title = str(ld_job.get("title") or title).strip()
        org = ld_job.get("hiringOrganization")
        if isinstance(org, dict) and org.get("name"):
            company = str(org["name"]).strip()
        description = str(ld_job.get("description") or "").strip()
        posted_at = ld_job.get("datePosted")
        deadline = ld_job.get("validThrough")
        raw_skills = ld_job.get("skills")
        if isinstance(raw_skills, str):
            skills = [s.strip() for s in raw_skills.split(",") if s.strip()]
        elif isinstance(raw_skills, list):
            skills = [str(s).strip() for s in raw_skills if str(s).strip()]

    if not description:
        nodes = [t.strip() for t in response.css(".job-description__item--content ::text").getall() if t.strip()]
        description = "\n".join(nodes)

    if not skills:
        skills = [s.strip() for s in response.css(".job-tags a::text").getall() if s.strip()]

    return JobItem(
        source="topcv",
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


class TopcvSpider(BaseJobSpider):
    name = "topcv"
    source_name = "topcv"
    allowed_domains = ["topcv.vn", "www.topcv.vn"]

    async def start(self) -> AsyncIterator[scrapy.Request]:
        for query in self.get_active_searches():
            url = build_topcv_search_url(query.keyword, query.location)
            logger.info("[topcv] Queuing search: %s (%s) -> %s", query.keyword, query.location, url)
            yield scrapy.Request(url=url, callback=self.parse_list)

    def parse_list(self, response: scrapy.http.Response) -> Iterator[scrapy.Request]:
        self.check_blocked(response)
        cards = parse_topcv_list_cards(response)
        logger.info("[topcv] Found %d cards on %s", len(cards), response.url)

        for summary_job in cards:
            if not self.should_fetch_detail(summary_job):
                continue
            yield response.follow(
                summary_job.url,
                callback=self.parse_detail,
                cb_kwargs={"fallback": summary_job},
            )

    def parse_detail(self, response: scrapy.http.Response, fallback: Job | None = None) -> JobItem:
        self.check_blocked(response)
        return parse_topcv_detail_response(response, fallback=fallback)

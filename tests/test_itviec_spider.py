from __future__ import annotations

from pathlib import Path

from scrapy.http import HtmlResponse

from src.crawler.spiders.itviec import (
    build_itviec_search_url,
    normalize_itviec_url,
    parse_itviec_detail_response,
    parse_itviec_list_cards,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_build_itviec_search_url() -> None:
    assert build_itviec_search_url("python", "Hồ Chí Minh") == "https://itviec.com/it-jobs/python/ho-chi-minh-hcm"
    assert build_itviec_search_url("Node.js", "Hà Nội") == "https://itviec.com/it-jobs/node-js/ha-noi"
    assert build_itviec_search_url("python", "") == "https://itviec.com/it-jobs/python"


def test_normalize_itviec_url() -> None:
    raw = "https://itviec.com/it-jobs/python-dev-1234?lab_feature=preview_jd_page"
    assert normalize_itviec_url(raw) == "https://itviec.com/it-jobs/python-dev-1234"


def test_parse_itviec_list_fixture() -> None:
    html = (FIXTURES_DIR / "itviec_list.html").read_text(encoding="utf-8")
    resp = HtmlResponse(url="https://itviec.com/it-jobs/python/ho-chi-minh-hcm", body=html, encoding="utf-8")
    jobs = parse_itviec_list_cards(resp)
    assert len(jobs) > 0
    first = jobs[0]
    assert first.source == "itviec"
    assert first.job_id
    assert first.url.startswith("https://itviec.com/it-jobs/")
    assert "?" not in first.url


def test_parse_itviec_detail_fixture() -> None:
    html = (FIXTURES_DIR / "itviec_detail.html").read_text(encoding="utf-8")
    url = "https://itviec.com/it-jobs/ai-software-engineer-python-go-c-c-ai-agents-mb-bank-5434"
    resp = HtmlResponse(url=url, body=html, encoding="utf-8")
    item = parse_itviec_detail_response(resp)
    assert item["source"] == "itviec"
    assert item["company"] == "MB Bank"
    assert "Python" in item["skills"]
    assert item["salary"] == "800 - 2,000 USD"
    assert item["deadline"] == "2026-11-27"
    assert len(item["description"]) > 50

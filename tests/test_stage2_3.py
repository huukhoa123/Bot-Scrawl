from __future__ import annotations

from pathlib import Path

from scrapy.http import HtmlResponse

from src.bot_sync import process_callback_data, process_command_text
from src.crawler.spiders.topcv import (
    build_topcv_search_url,
    extract_topcv_job_id,
    parse_topcv_detail_response,
    parse_topcv_list_cards,
)
from src.db import JobDB
from src.models import Job
from src.notifier import TELEGRAM_MAX_CHARS, build_job_inline_keyboard, format_digest_html
from src.summarizer import score_job_match, summarize_llm

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_topcv_offline_fixtures() -> None:
    assert build_topcv_search_url("Python Developer") == "https://www.topcv.vn/tim-viec-lam-python-developer"
    assert extract_topcv_job_id("https://www.topcv.vn/viec-lam/python-dev/123456.html") == "123456"

    list_html = (FIXTURES_DIR / "topcv_list.html").read_text(encoding="utf-8")
    list_resp = HtmlResponse(url="https://www.topcv.vn/tim-viec-lam-python", body=list_html, encoding="utf-8")
    cards = parse_topcv_list_cards(list_resp)
    assert len(cards) == 1
    assert cards[0].job_id == "123456"
    assert cards[0].company == "TopTech Vietnam"
    assert cards[0].salary == "20 - 30 triệu"

    detail_html = (FIXTURES_DIR / "topcv_detail.html").read_text(encoding="utf-8")
    detail_resp = HtmlResponse(
        url="https://www.topcv.vn/viec-lam/python-backend-developer/123456.html",
        body=detail_html,
        encoding="utf-8",
    )
    item = parse_topcv_detail_response(detail_resp, fallback=cards[0])
    assert item["company"] == "TopTech Vietnam"
    assert "Django" in item["skills"]
    assert item["deadline"] == "2026-12-20"


def test_summarize_llm_fallback_without_key() -> None:
    job = Job(
        source="itviec",
        job_id="1",
        title="Python Dev",
        company="Co",
        location="HCM",
        salary="20 triệu",
        url="https://itviec.com/it-jobs/1",
        description="Yêu cầu: Python, FastAPI.\nPhúc lợi: Lương tháng 13.",
    )
    out = summarize_llm(job, api_key="")
    assert "Python, FastAPI" in out


def test_score_job_match() -> None:
    job = Job(
        source="itviec",
        job_id="1",
        title="Backend Python Engineer",
        company="Co",
        location="HCM",
        salary=None,
        url="https://itviec.com/it-jobs/1",
        skills=["Python", "Docker"],
    )
    assert score_job_match(job, ["python", "docker", "kubernetes", "aws"]) == 50
    assert score_job_match(job, []) is None


def test_digest_formatting_and_inline_buttons() -> None:
    jobs = [
        Job(
            source="itviec",
            job_id=f"job-{i}",
            title=f"Python Developer #{i}",
            company="Acme Corp",
            location="HCM",
            salary="20 - 30 triệu",
            url=f"https://itviec.com/it-jobs/job-{i}",
        )
        for i in range(60)
    ]
    chunks = format_digest_html(jobs)
    assert len(chunks) >= 2
    assert all(len(c) <= TELEGRAM_MAX_CHARS for c in chunks)

    kb = build_job_inline_keyboard(jobs[0])
    assert len(kb["inline_keyboard"][0]) == 3


def test_bot_sync_commands_and_callbacks(tmp_path: Path) -> None:
    with JobDB(tmp_path / "jobs.db") as db:
        reply_add = process_command_text("/search python | Hồ Chí Minh", db)
        assert reply_add and "python" in reply_add

        reply_list = process_command_text("/list", db)
        assert reply_list and "python" in reply_list

        reply_pause = process_command_text("/pause", db)
        assert reply_pause and db.get_state("paused") == "1"

        reply_resume = process_command_text("/resume", db)
        assert reply_resume and db.get_state("paused") == "0"

        reply_rm = process_command_text("/remove 1", db)
        assert reply_rm and "Đã xóa" in reply_rm

        job = Job(
            source="itviec",
            job_id="python-dev-9999",
            title="Python Dev",
            company="Co",
            location="HCM",
            salary=None,
            url="https://itviec.com/it-jobs/python-dev-9999",
        )
        db.upsert_job(job, status="sent", filter_hash="h1")
        ack = process_callback_data("st:applied:itviec:python-dev-9999", db)
        assert ack and "Ứng tuyển" in ack
        row = db.conn.execute("SELECT status FROM jobs WHERE job_id = 'python-dev-9999'").fetchone()
        assert row["status"] == "applied"


def test_topdev_api_offline_fixture() -> None:
    import json

    from src.crawler.spiders.topdev import build_topdev_api_url, parse_topdev_api_payload

    url = build_topdev_api_url("python", "Hồ Chí Minh", page=2)
    assert "q=python" in url and "region_ids=79" in url and "page=2" in url

    payload = json.loads((FIXTURES_DIR / "topdev_api.json").read_text(encoding="utf-8"))
    items = parse_topdev_api_payload(payload)
    assert len(items) == 10
    first = items[0]
    assert first["source"] == "topdev"
    assert first["job_id"] == "2148858"
    assert first["company"] == "MBBANK"
    assert "Python" in first["skills"]

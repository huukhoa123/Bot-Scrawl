# Job Scraper → Telegram Bot (Scrapy + 0đ Deployment)

## 1. Mục tiêu

Xây một bot cá nhân chạy định kỳ (chi phí vận hành **0đ**) để:
1. Lấy tin tuyển dụng mới từ **TopCV** và **ITviec** bằng **Scrapy**.
2. Lọc theo bộ tiêu chí của người dùng.
3. Bỏ qua job đã thấy trước đó (khử trùng bằng SQLite).
4. Tóm tắt mô tả công việc thành bản ngắn gọn.
5. Gửi kết quả qua **Telegram**.

Chỉ dùng cá nhân, tần suất thấp, không thu thập dữ liệu hàng loạt.

## 2. Tech stack

- Python 3.11+
- **`scrapy`** (lõi crawl: có sẵn `parsel` CSS/XPath/JSON, AutoThrottle, Retry, `robots.txt`)
- **`scrapy-playwright`** (chỉ bật theo từng request khi trang cần render JS)
- **`httpx`** (gọi Telegram Bot API và LLM API, tránh xung đột Twisted reactor của Scrapy)
- **`sqlite3`** (stdlib) lưu `data/jobs.db`
- **`pyyaml`** cho config, **`python-dotenv`** cho secrets
- **`pytest`** cho test
- Tùy chọn: **Gemini API Free Tier** (hoặc LLM khác) để tóm tắt & chấm điểm (bật/tắt bằng config)

## 3. Cấu trúc thư mục

```
job-bot/
├── AGENTS.md
├── README.md
├── scrapy.cfg
├── .gitignore                   # bỏ qua .env, __pycache__, .pytest_cache (KHÔNG bỏ qua data/jobs.db)
├── .env.example                 # TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, LLM_API_KEY (tùy chọn)
├── config.yaml                  # bộ lọc + cài đặt
├── requirements.txt
├── data/
│   └── jobs.db                  # SQLite (lưu bền vững bằng commit vào private repo)
├── .github/workflows/
│   └── scrape.yml               # GitHub Actions cron định kỳ (0đ)
├── src/
│   ├── main.py                  # entrypoint CLI: --mode instant|scrape|digest [--dry-run]
│   ├── config.py                # load config.yaml + .env
│   ├── models.py                # dataclass Job
│   ├── db.py                    # SQLite: bảng jobs (có status) + searches
│   ├── filters.py               # pre_filter (list), full_filter (detail), parse lương
│   ├── summarizer.py            # tóm tắt mô tả (rule-based hoặc LLM)
│   ├── notifier.py              # format HTML + gửi tin Telegram (instant & digest)
│   ├── bot_sync.py              # GĐ 3: kéo getUpdates xử lý lệnh & inline button (0đ)
│   └── crawler/                 # Scrapy package
│       ├── __init__.py
│       ├── settings.py          # AutoThrottle, Delay, Retry, AsyncioReactor, Playwright
│       ├── items.py             # JobItem
│       ├── pipelines.py         # FilterPipeline → SQLitePipeline
│       └── spiders/
│           ├── __init__.py
│           ├── base.py          # BaseJobSpider (load seen_ids, 2 pha list → detail)
│           ├── topcv.py
│           └── itviec.py
└── tests/
    └── fixtures/                # HTML mẫu để test spider offline
```

## 4. Mô hình dữ liệu

```python
@dataclass
class Job:
    source: str                 # "topcv" | "itviec"
    job_id: str                 # ID duy nhất theo nguồn (lấy từ URL)
    title: str
    company: str
    location: str
    salary: str | None              # chuỗi gốc, ví dụ "15 - 25 triệu"
    salary_min_million: int | None  # quy đổi (triệu VND) để lọc
    salary_max_million: int | None
    url: str
    description: str                # mô tả + yêu cầu (text thuần)
    skills: list[str]
    posted_at: str | None
    deadline: str | None
```

Bảng SQLite `jobs`:
- Khóa chính: `(source, job_id)`
- Các cột: `source, job_id, title, company, location, salary, url, summary, status, filter_hash, first_seen_at, sent_at`
- Giá trị `status`: `new` | `pending_digest` | `sent` | `filtered_out` | `failed` | `applied` | `ignored` | `saved`
- Cột `filter_hash`: hash của phần `filters` trong `config.yaml`. Khi config đổi (hash khác), xét lại các job có `status = 'filtered_out'` để job bị loại cũ không bị "chốt" mãi.

## 5. Kiến trúc Scrapy & Quy tắc crawl

Áp dụng chuẩn thiết kế của `scrapy/scrapy`:

### Cấu hình bắt buộc (`src/crawler/settings.py`)
- `TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"`
- `ROBOTSTXT_OBEY = True`
- `CONCURRENT_REQUESTS = 2` và `CONCURRENT_REQUESTS_PER_DOMAIN = 1`
- `DOWNLOAD_DELAY = 3` và `RANDOMIZE_DOWNLOAD_DELAY = True` (delay ngẫu nhiên ~1.5–4.5s)
- `AUTOTHROTTLE_ENABLED = True` (`AUTOTHROTTLE_START_DELAY = 3`, `AUTOTHROTTLE_MAX_DELAY = 15`)
- `DOWNLOAD_TIMEOUT = 20`
- `RETRY_ENABLED = True`, `RETRY_TIMES = 3`, `RETRY_HTTP_CODES = [429, 500, 502, 503, 504]`
- Đặt `DEFAULT_REQUEST_HEADERS` / `USER_AGENT` hợp lý.

### Luồng 2 pha trong `BaseJobSpider`
1. **Khởi động spider (`open_spider`)**: load tập `seen_ids` từ SQLite.
2. **Pha 1 (`parse_list`)**: lấy danh sách job cơ bản (`job_id, title, company, location, salary, url`).
   - Bỏ qua ngay nếu `(source, job_id)` đã có trong `seen_ids` hoặc đã thấy trong cùng phiên chạy.
   - Chạy **`pre_filter()`** (loại sớm theo `exclude_keywords`, `blacklist_companies`, lương nếu có sẵn ở trang list). Job bị loại lưu `status = 'filtered_out'` để lần sau không kiểm tra lại.
   - Chỉ `yield response.follow(detail_url, callback=self.parse_detail)` cho job **vừa mới vừa qua vòng lọc sơ bộ**.
3. **Pha 2 (`parse_detail`)**:
   - Ưu tiên parse **JSON API nội bộ** hoặc **JSON-LD (`application/ld+json` `JobPosting`)** nếu có.
   - Nếu không có, parse HTML bằng `response.css()` / `response.xpath()`.
   - Playwright (`scrapy-playwright`, bật qua `meta={"playwright": True}`) **chỉ dùng để render JS**. Nếu bị 403/captcha/Cloudflare thì dừng spider đó, ghi log và báo người dùng; **cấm** dùng stealth, giả fingerprint, xoay proxy hay giải captcha.
   - Toàn bộ selector/endpoint gom ở đầu file mỗi spider để dễ cập nhật.
4. **Pipeline & Cách ly lỗi**:
   - `FilterPipeline` kiểm tra đầy đủ (bao gồm `include_keywords`, `skills`, `description`).
   - `SQLitePipeline` ghi job đạt chuẩn vào SQLite với `status = 'new'`.
   - `main.py` chạy Scrapy bằng **`subprocess` (tiến trình riêng)**, xong mới chạy `summarizer` / `notifier` ở tiến trình chính (vì Twisted reactor không khởi động lại được và dễ đụng event loop), đồng thời đảm bảo không mất job nếu Telegram lỗi.
   - Chạy từng spider độc lập (bắt exception theo từng nguồn) để 1 nguồn lỗi không ảnh hưởng nguồn còn lại.
   - Tôn trọng `robots.txt` và điều khoản sử dụng. Không bypass đăng nhập hay captcha.

## 6. Bộ lọc (`config.yaml`)

```yaml
searches:
  - keyword: "python"
    location: "Hồ Chí Minh"
  # thêm nhiều cặp keyword/location

filters:
  include_keywords: []                   # phải chứa ít nhất 1 từ (title hoặc skills)
  exclude_keywords: ["senior", "lead"]   # ví dụ, người dùng tự chỉnh
  min_salary_million_vnd: null           # null = không lọc lương
  allow_negotiable_salary: true          # cho phép "Thỏa thuận"
  blacklist_companies: []
  locations: []                          # rỗng = không lọc thêm

notify:
  mode: "instant"                        # "instant" | "digest"
  digest_time: "08:00"
  max_jobs_per_run: 20                   # giới hạn chống spam

summary:
  enabled: true
  method: "rule"                         # "rule" | "llm"
```

Giá trị mặc định chỉ là ví dụ. Không hardcode tiêu chí nào trong code.

Parse lương: chuyển các dạng như `"15 - 25 triệu"`, `"Up to 30 triệu"`, `"Tới 30 tr"`, `"Thỏa thuận"`, `"$1000 - $2000"` về khoảng `(min_million_vnd, max_million_vnd)` hoặc `(None, None)` nếu không rõ.

## 7. Tóm tắt

- `method: rule`: cắt các mục chính từ mô tả (yêu cầu, quyền lợi) và giới hạn tổng độ dài tối đa ~600 ký tự.
- `method: llm`: gọi LLM (ưu tiên Gemini API Free Tier) trả về JSON cấu trúc gồm: tóm tắt ngắn gọn (3–5 yêu cầu chính, phúc lợi nổi bật) và tùy chọn điểm khớp ở GĐ 3. Có timeout và fallback về `rule` nếu lỗi.

## 8. Định dạng tin Telegram

Dùng HTML parse mode, escape ký tự đặc biệt (`html.escape`):

```
<b>{title}</b>
🏢 {company}
📍 {location}
💰 {salary}
🛠 {skills, tối đa 6}

{summary}

⏰ Hạn nộp: {deadline}
🔗 <a href="{url}">Xem chi tiết</a>
```

- Mỗi job 1 tin. Nếu quá 4096 ký tự thì cắt bớt phần tóm tắt.
- Chế độ CLI:
  - `python -m src.main --mode instant`: crawl + gửi ngay các job `status='new'`.
  - `python -m src.main --mode scrape`: crawl + đánh dấu `status='pending_digest'`.
  - `python -m src.main --mode digest`: gom toàn bộ `pending_digest` thành 1–vài tin tổng hợp gửi lúc 08:00.
- Gửi tuần tự, nghỉ ~1 giây giữa các tin; gặp lỗi 429 thì chờ theo `retry_after`. Gửi lỗi thì lưu `status='failed'` để lần sau thử lại.
- Giai đoạn 3: thêm inline button `✅ Đã ứng tuyển` / `🚫 Bỏ qua` / `⭐ Lưu`.

## 9. Triển khai 0đ

- **Mặc định (GitHub Actions - Private Repo):**
  - Chạy cron 4–6 lần/ngày (ước tính khoảng ~300–500 phút/tháng nếu có cài/chạy Playwright, vẫn nằm trong hạn mức 2.000 phút/tháng miễn phí; RAM 7GB). **Cache browser Playwright (`~/.cache/ms-playwright`)** để tiết kiệm thời gian và phút chạy.
  - Cấu hình workflow: `permissions: contents: write`, đặt `concurrency` để 2 lần chạy không đè nhau, lưu secrets (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `LLM_API_KEY`) trong **GitHub Secrets**.
  - Cuối mỗi lần chạy tự động commit & push `data/jobs.db` về repo bằng `GITHUB_TOKEN`.
  - Định kỳ **dọn job cũ (quá 60–90 ngày)** khỏi `data/jobs.db` để repo không bị phình to theo thời gian.
  - Giai đoạn 3 (vẫn 0đ): đầu mỗi lượt cron, `bot_sync.py` gọi Telegram `getUpdates` để xử lý các lệnh `/add`, `/list`, `/remove` và inline button người dùng đã bấm (Telegram giữ queue 24h). *Lưu ý:* các lệnh này chỉ được xử lý vào mỗi lượt cron nên **có độ trễ** theo chu kỳ chạy.
- **Dự phòng (Windows Task Scheduler):**
  - Nếu IP GitHub bị Cloudflare chặn gắt, chạy trực tiếp `python -m src.main` theo lịch trên máy cá nhân (IP mạng nhà ít bị chặn hơn, vẫn 0đ).

## 10. Lộ trình triển khai

**Giai đoạn 1: MVP chạy được**
- [ ] Khung dự án Scrapy, `config.py`, `.env.example`, `models.py`, `db.py`
- [ ] Thăm dò thực tế TopCV & ITviec; viết `BaseJobSpider` + 1 spider hoàn chỉnh cho trang dễ hơn trước
- [ ] `filters.py` (pre-filter, full-filter, parse lương) + khử trùng SQLite
- [ ] `notifier.py` gửi tin Telegram qua `httpx`
- [ ] `main.py` chạy 1 vòng end-to-end (`--mode instant`)
- [ ] Test cho filters, parse lương, khử trùng, spider parse (dùng HTML fixture)

**Giai đoạn 2: Hoàn thiện**
- [ ] Spider thứ hai
- [ ] Tích hợp `scrapy-playwright` cho trang bị chặn/JS
- [ ] `summarizer.py` (rule, rồi LLM Free Tier tùy chọn)
- [ ] Chế độ `--mode scrape` và `--mode digest`
- [ ] Logging ra file, xử lý lỗi và retry gửi Telegram

**Giai đoạn 3: Tương tác (0đ)**
- [ ] `bot_sync.py` xử lý lệnh `/add <keyword> <location>`, `/list`, `/remove <id>`, `/pause`, `/resume` qua `getUpdates`
- [ ] Inline button cập nhật `status` (`applied` / `ignored` / `saved`)
- [ ] Chấm điểm độ khớp job với kỹ năng người dùng

**Giai đoạn 4: Triển khai**
- [ ] Workflow `.github/workflows/scrape.yml` (cron + auto-commit `data/jobs.db`)
- [ ] `README.md` hướng dẫn cài đặt (GitHub Actions & Windows Task Scheduler)

## 11. Quy ước cho agent

- Không commit secrets. Chỉ dùng `.env`, và cập nhật `.env.example`.
- Code có type hints, hàm nhỏ, dễ test.
- Làm theo từng giai đoạn; xong giai đoạn nào thì chạy `pytest` và báo kết quả trước khi sang giai đoạn tiếp.
- Khi selector hoặc endpoint của trang không còn đúng, dừng lại và báo rõ thay vì đoán mò.
- Không thêm dependency ngoài danh sách khi chưa cần thiết.
- Tuân thủ đầy đủ cài đặt throttle/delay/robots.txt của Scrapy ở mục 5.
- Mọi lệnh chạy thử phải có `--dry-run` (không gửi Telegram thật).
- Test spider dùng HTML trong `tests/fixtures/`, không crawl thật liên tục.
- Không thêm `data/jobs.db` vào `.gitignore` (vì đang commit nó), nhưng `.env` thì phải có trong `.gitignore`.

## 12. Tiêu chí hoàn thành MVP

- Chạy `python -m src.main` gửi được tin job mới vào Telegram.
- Chạy lần 2 liên tiếp không gửi lại job cũ.
- Đổi `config.yaml` là đổi được kết quả lọc mà không sửa code.
- Một spider lỗi không làm hỏng spider còn lại.

# Job Scraper → Telegram Bot (Scrapy + 0đ Deployment)

Bot cá nhân chạy định kỳ bằng **Scrapy** để lấy tin tuyển dụng mới từ **ITviec** và **TopCV**, lọc theo tiêu chí trong `config.yaml`, khử trùng bằng SQLite, tóm tắt mô tả và gửi về **Telegram**.

## 1. Cài đặt local

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

Điền `TELEGRAM_BOT_TOKEN` và `TELEGRAM_CHAT_ID` vào `.env` (tùy chọn thêm `LLM_API_KEY` nếu dùng Gemini API Free Tier để tóm tắt).

Chỉnh bộ lọc trong `config.yaml` theo nhu cầu của bạn.

## 2. Chạy thử & kiểm thử

- Chạy toàn bộ unit test (offline, dùng HTML fixture trong `tests/fixtures/`):
  ```powershell
  .\.venv\Scripts\python.exe -m pytest -v
  ```
- Chạy thử không gửi Telegram thật (`--dry-run`):
  ```powershell
  .\.venv\Scripts\python.exe -m src.main --mode instant --spiders itviec --dry-run
  ```
- Chạy gửi thật:
  ```powershell
  .\.venv\Scripts\python.exe -m src.main --mode instant
  ```
- Chế độ tổng hợp (digest):
  ```powershell
  .\.venv\Scripts\python.exe -m src.main --mode scrape
  .\.venv\Scripts\python.exe -m src.main --mode digest
  ```

## 3. Triển khai 0đ trên GitHub Actions

1. Tạo một **Private Repository** trên GitHub và push mã nguồn lên (không push `.env`; giữ `data/jobs.db` không nằm trong `.gitignore`).
2. Vào **Settings → Secrets and variables → Actions** và thêm:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `LLM_API_KEY` *(tùy chọn)*
3. Workflow `.github/workflows/scrape.yml` sẽ tự động chạy 4 lần/ngày (08:00, 12:00, 17:00, 21:00 giờ VN) và tự commit `data/jobs.db` về repo.

## 4. Dự phòng 0đ bằng Windows Task Scheduler

Nếu IP GitHub Actions bị Cloudflare chặn gắt, bạn có thể tạo 1 Basic Task trong **Windows Task Scheduler** chạy định kỳ:
- Program: `D:\Api Craw\.venv\Scripts\python.exe`
- Arguments: `-m src.main --mode instant`
- Start in: `D:\Api Craw`

## 5. Lệnh Telegram (Giai đoạn 3)

Các lệnh và nút bấm (`✅ Đã ứng tuyển` / `🚫 Bỏ qua` / `⭐ Lưu`) được đồng bộ ở đầu mỗi lượt cron (nên có độ trễ theo chu kỳ chạy):
- `/add <keyword> | <location>` — thêm từ khóa tìm kiếm
- `/list` — xem danh sách tìm kiếm & trạng thái bot
- `/remove <id>` — xóa tìm kiếm theo ID
- `/pause` / `/resume` — tạm dừng hoặc bật lại bot

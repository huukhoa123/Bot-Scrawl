BOT_NAME = "job_bot"

SPIDER_MODULES = ["src.crawler.spiders"]
NEWSPIDER_MODULE = "src.crawler.spiders"

TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"

ROBOTSTXT_OBEY = True

CONCURRENT_REQUESTS = 2
CONCURRENT_REQUESTS_PER_DOMAIN = 1

DOWNLOAD_DELAY = 3
DOWNLOAD_DELAY_JITTER = 0.5

AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 3
AUTOTHROTTLE_MAX_DELAY = 15
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0

DOWNLOAD_TIMEOUT = 20

RETRY_ENABLED = True
RETRY_TIMES = 3
RETRY_HTTP_CODES = [429, 500, 502, 503, 504]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

DEFAULT_REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
}

ITEM_PIPELINES = {
    "src.crawler.pipelines.FilterAndStorePipeline": 300,
}

LOG_LEVEL = "INFO"
REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"

# Use standard httpx.AsyncClient for HTTP/HTTPS requests inside Scrapy
DOWNLOAD_HANDLERS = {
    "http": "src.crawler.handlers.HttpxDownloadHandler",
    "https": "src.crawler.handlers.HttpxDownloadHandler",
}

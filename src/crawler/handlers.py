from __future__ import annotations

from typing import Any

import httpx
from scrapy.http import HtmlResponse, Request, Response


class HttpxDownloadHandler:
    """Standard httpx.AsyncClient download handler for Scrapy.

    Uses standard httpx instead of Twisted/pyOpenSSL while preserving all Scrapy
    middlewares (robots.txt, AutoThrottle, DownloadDelay, Retry, Pipelines).
    """

    lazy = True

    def __init__(self, settings: Any) -> None:
        timeout = float(settings.getfloat("DOWNLOAD_TIMEOUT", 20.0))
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    @classmethod
    def from_crawler(cls, crawler: Any) -> HttpxDownloadHandler:
        return cls(crawler.settings)

    async def download_request(self, request: Request) -> Response:
        headers: dict[str, str] = {}
        for k, vals in request.headers.items():
            key = k.decode("latin1") if isinstance(k, bytes) else str(k)
            val = b",".join(vals).decode("latin1") if isinstance(vals, list) else str(vals)
            headers[key] = val

        resp = await self._client.request(
            method=request.method,
            url=request.url,
            headers=headers,
            content=request.body or None,
        )
        # httpx already transparently decompresses resp.content; drop Content-Encoding
        # so Scrapy's HttpCompressionMiddleware does not try to decompress twice.
        resp_headers = {
            k: v
            for k, v in resp.headers.items()
            if k.lower() not in ("content-encoding", "content-length")
        }
        return HtmlResponse(
            url=str(resp.url),
            status=resp.status_code,
            headers=resp_headers,
            body=resp.content,
            request=request,
            encoding=resp.encoding or "utf-8",
        )

    async def close(self) -> None:
        await self._client.aclose()

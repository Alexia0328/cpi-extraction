from __future__ import annotations

import email.utils
import hashlib
import random
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from .models import FetchResult
from .storage import write_bytes_atomic


RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
ALLOWED_HOSTS = {"statcan.gc.ca", "www.statcan.gc.ca", "www150.statcan.gc.ca"}


class SourceError(RuntimeError):
    pass


def canonicalize_url(url: str, *, require_english_html: bool = False) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in {"http", "https"} or host not in ALLOWED_HOSTS:
        raise SourceError("source URL must use an official statcan.gc.ca host")

    path = parts.path.rstrip("/") or "/"
    if path.startswith("/n1/daily-quotidien/"):
        path = path.removeprefix("/n1")
    if require_english_html:
        if not path.endswith("-eng.htm") or "/daily-quotidien/" not in path:
            raise SourceError("only English Statistics Canada Daily HTML releases are supported")
        host = "www.statcan.gc.ca"
    return urlunsplit(("https", host, path, "", ""))


def _retry_after_seconds(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return max(0.0, (parsed - now).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


class HttpFetcher:
    def __init__(
        self,
        cache_dir: Path,
        *,
        client: httpx.Client | None = None,
        sleep_fn=time.sleep,
        random_fn=random.random,
    ) -> None:
        self.cache_dir = cache_dir
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=5.0),
            follow_redirects=True,
            headers={"User-Agent": "statcan-cpi-harness/1.0 (+take-home-assignment)"},
        )
        self.sleep_fn = sleep_fn
        self.random_fn = random_fn

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "HttpFetcher":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def fetch(
        self,
        url: str,
        *,
        refresh: bool = False,
        require_english_html: bool = False,
    ) -> FetchResult:
        canonical_url = canonicalize_url(url, require_english_html=require_english_html)
        url_key = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
        cache_path = self.cache_dir / f"{url_key}.bin"
        if cache_path.exists() and not refresh:
            content = cache_path.read_bytes()
            return FetchResult(
                url=canonical_url,
                content=content,
                fetched_at=datetime.fromtimestamp(cache_path.stat().st_mtime, tz=UTC),
                source_sha256=hashlib.sha256(content).hexdigest(),
                cache_hit=True,
                attempts=0,
                cache_path=cache_path,
            )

        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                response = self.client.get(canonical_url)
                if response.status_code not in RETRYABLE_STATUS:
                    response.raise_for_status()
                    content = response.content
                    if not content.strip():
                        raise SourceError("source returned an empty response body")
                    write_bytes_atomic(cache_path, content)
                    return FetchResult(
                        url=canonical_url,
                        content=content,
                        fetched_at=datetime.now(UTC),
                        source_sha256=hashlib.sha256(content).hexdigest(),
                        cache_hit=False,
                        attempts=attempt,
                        cache_path=cache_path,
                    )

                last_error = SourceError(f"retryable HTTP status {response.status_code}")
                retry_after = _retry_after_seconds(
                    response.headers.get("Retry-After"), datetime.now(UTC)
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                retry_after = None
            except httpx.HTTPStatusError as exc:
                raise SourceError(f"non-retryable HTTP status {exc.response.status_code}") from exc

            if attempt < 3:
                delay = retry_after
                if delay is None:
                    delay = (2 ** (attempt - 1)) + (0.25 * self.random_fn())
                self.sleep_fn(delay)

        raise SourceError(f"source fetch failed after 3 attempts: {last_error}")


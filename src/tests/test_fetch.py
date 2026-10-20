from pathlib import Path

import httpx

from src.cpi_harness.fetch import HttpFetcher


URL = "https://www.statcan.gc.ca/daily-quotidien/260817/dq260817a-eng.htm"


def test_transient_failure_retries_then_caches(tmp_path: Path):
    statuses = iter([503, 200])
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        status = next(statuses)
        return httpx.Response(status, request=request, content=b"ok" if status == 200 else b"")

    sleeps = []
    fetcher = HttpFetcher(
        tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep_fn=sleeps.append,
        random_fn=lambda: 0,
    )
    assert fetcher.fetch(URL).attempts == 2
    assert fetcher.fetch(URL).cache_hit
    assert calls == 2
    assert sleeps == [1.0]

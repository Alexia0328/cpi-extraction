from src.cpi_harness.discovery import discover_latest_cpi


def test_finds_latest_cpi_and_ignores_other_releases():
    feed = b"""<feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>Consumer Price Index, June 2026</title><updated>2026-07-20T12:00:00Z</updated><link href="https://www.statcan.gc.ca/daily-quotidien/260720/dq260720a-eng.htm"/></entry>
      <entry><title>Industrial Product Price Index, July 2026</title><updated>2026-08-18T12:00:00Z</updated><link href="https://www.statcan.gc.ca/daily-quotidien/260818/x-eng.htm"/></entry>
      <entry><title>Consumer Price Index, July 2026</title><updated>2026-08-17T12:00:00Z</updated><link href="https://www.statcan.gc.ca/daily-quotidien/260817/dq260817a-eng.htm"/></entry>
    </feed>"""
    entry = discover_latest_cpi(feed)
    assert entry.title == "Consumer Price Index, July 2026"
    assert entry.url.endswith("/260817/dq260817a-eng.htm")

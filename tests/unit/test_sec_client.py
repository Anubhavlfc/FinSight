"""SEC client: identification, pacing, bounded retries, and caching. No network."""

from __future__ import annotations

import json
import random

import pytest

from finsight.config import SecSettings
from finsight.errors import AccessDeniedError, ConfigurationError, SourceNotFoundError, TransientSourceError
from finsight.sec.client import HttpResponse, SecHttpClient, TransportError
from finsight.sec.sources import HttpCache, LiveSecSource


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def scripted(responses):
    calls = []

    def transport(url, headers, timeout):
        calls.append((url, dict(headers)))
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    transport.calls = calls
    return transport


def client(transport, clock=None, **settings):
    clock = clock or FakeClock()
    return SecHttpClient("FinSight tests test@example.com", SecSettings(**settings), transport=transport,
                         clock=clock.now, sleep=clock.sleep, rng=random.Random(0)), clock


def test_user_agent_is_required():
    with pytest.raises(ConfigurationError, match="FINSIGHT_SEC_USER_AGENT"):
        SecHttpClient(None)
    with pytest.raises(ConfigurationError):
        SecHttpClient("   ")


def test_user_agent_and_encoding_headers_sent():
    t = scripted([HttpResponse(200, b"{}")])
    c, _ = client(t)
    c.get("https://data.sec.gov/x")
    headers = t.calls[0][1]
    assert headers["User-Agent"] == "FinSight tests test@example.com"
    assert "gzip" in headers["Accept-Encoding"]


def test_requests_are_paced():
    t = scripted([HttpResponse(200, b"{}") for _ in range(3)])
    c, clock = client(t, max_requests_per_second=4)
    for _ in range(3):
        c.get("https://data.sec.gov/x")
    # First request goes immediately; the next two wait a quarter second each.
    assert clock.sleeps == pytest.approx([0.25, 0.25])


def test_transient_errors_retry_with_backoff_then_succeed():
    t = scripted([HttpResponse(503, b""), TransportError("reset"), HttpResponse(200, b'{"ok": 1}')])
    c, clock = client(t, max_attempts=3, backoff_base_seconds=1.0, max_requests_per_second=1000)
    resp = c.get("https://data.sec.gov/x")
    assert resp.status == 200 and c.request_count == 3
    backoffs = [s for s in clock.sleeps if s >= 0.5]
    # attempt 1 -> 1s + jitter in [0,1); attempt 2 -> 2s + jitter in [0,1)
    assert 1.0 <= backoffs[0] < 2.0 and 2.0 <= backoffs[1] < 3.0


def test_retries_are_bounded():
    t = scripted([HttpResponse(500, b"")] * 5)
    c, _ = client(t, max_attempts=3, max_requests_per_second=1000)
    with pytest.raises(TransientSourceError, match="after 3 attempts"):
        c.get("https://data.sec.gov/x")
    assert c.request_count == 3


def test_retry_after_is_honoured_and_capped():
    t = scripted([HttpResponse(429, b"", {"Retry-After": "7"}), HttpResponse(429, b"", {"Retry-After": "999"}),
                  HttpResponse(200, b"{}")])
    c, clock = client(t, max_requests_per_second=1000, max_retry_after_seconds=20)
    c.get("https://data.sec.gov/x")
    assert 7 in clock.sleeps and 20 in clock.sleeps


@pytest.mark.parametrize("status,exc", [(404, SourceNotFoundError), (403, AccessDeniedError)])
def test_non_retryable_statuses(status, exc):
    t = scripted([HttpResponse(status, b"")])
    c, _ = client(t)
    with pytest.raises(exc):
        c.get("https://data.sec.gov/x")
    assert c.request_count == 1


def _facts(accn):
    return json.dumps({"facts": {"us-gaap": {"Revenues": {"units": {"USD": [{"accn": accn}]}}}}}).encode()


def test_cache_reuse_and_refresh_when_newer_filing(tmp_path):
    t = scripted([HttpResponse(200, _facts("A-1")), HttpResponse(200, _facts("A-2"))])
    c, _ = client(t, max_requests_per_second=1000)
    src = LiveSecSource(client=c, cache=HttpCache(tmp_path))
    first = src.company_facts("0000000001", must_include_accession="A-1")
    again = src.company_facts("0000000001", must_include_accession="A-1")
    assert not first.document.from_cache and again.document.from_cache
    assert first.document.sha256 == again.document.sha256
    assert c.request_count == 1
    # A newer 10-K exists that the cached copy lacks: refetch instead of reusing.
    newer = src.company_facts("0000000001", must_include_accession="A-2")
    assert not newer.document.from_cache and c.request_count == 2


def test_submissions_ttl(tmp_path):
    t = scripted([HttpResponse(200, b'{"v": 1}'), HttpResponse(200, b'{"v": 2}')])
    c, _ = client(t, max_requests_per_second=1000)
    now = [1000.0]
    src = LiveSecSource(client=c, cache=HttpCache(tmp_path), settings=SecSettings(submissions_ttl_seconds=60),
                        now=lambda: now[0])
    a = src.submissions("0000000001")
    # Cache entries record wall-clock time; move "now" relative to that.
    stored = json.loads(next((tmp_path / "http").glob("*.meta.json")).read_text())["stored_at_epoch"]
    now[0] = stored + 30
    assert src.submissions("0000000001").document.from_cache
    now[0] = stored + 120
    b = src.submissions("0000000001")
    assert not b.document.from_cache and b.payload == {"v": 2} and a.payload == {"v": 1}


def test_corrupted_cache_entry_is_a_miss(tmp_path):
    cache = HttpCache(tmp_path)
    from datetime import datetime, timezone
    cache.put("u", b"original", datetime.now(timezone.utc))
    body = next((tmp_path / "http").glob("*.body"))
    body.write_bytes(b"tampered")
    assert cache.get("u") is None

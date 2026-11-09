"""HTTP client for SEC EDGAR that follows the SEC's automated-access guidance.

* Declares who is calling through a User-Agent supplied by the operator
  (``FINSIGHT_SEC_USER_AGENT``). Requests are refused locally if it is missing.
* Paces requests to a configurable rate (default 5/s, under SEC's 10/s ceiling).
* Retries transient failures (429, 5xx, timeouts, connection errors) with bounded
  exponential backoff plus jitter, honouring ``Retry-After``.
* Does not retry 403/404: those need a human fix, not another attempt.

The transport, clock, sleep and random source are injectable so the retry and
pacing behaviour is tested without the network.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping

from finsight.config import SecSettings
from finsight.errors import (
    AccessDeniedError,
    ConfigurationError,
    SourceNotFoundError,
    TransientSourceError,
)

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


@dataclass
class HttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


Transport = Callable[[str, Mapping[str, str], float], HttpResponse]


class TransportError(Exception):
    """Raised by a transport for connection problems and timeouts."""


def requests_transport() -> Transport:
    import requests

    session = requests.Session()

    def _send(url: str, headers: Mapping[str, str], timeout: float) -> HttpResponse:
        try:
            r = session.get(url, headers=dict(headers), timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise TransportError(str(exc)) from exc
        return HttpResponse(status=r.status_code, body=r.content, headers=dict(r.headers))

    return _send


class SecHttpClient:
    def __init__(
        self,
        user_agent: str | None,
        settings: SecSettings | None = None,
        transport: Transport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ):
        if not user_agent or not user_agent.strip():
            raise ConfigurationError(
                "Live SEC access needs an identifying User-Agent. Set FINSIGHT_SEC_USER_AGENT, "
                'e.g. export FINSIGHT_SEC_USER_AGENT="FinSight research you@example.com". '
                "Or run with --offline to use the bundled fixtures."
            )
        self._user_agent = user_agent.strip()
        self.settings = settings or SecSettings()
        self._transport = transport or requests_transport()
        self._clock = clock
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._last_request_at: float | None = None
        self.request_count = 0  # network requests actually sent (for tests and run stats)

    @property
    def min_interval(self) -> float:
        return 1.0 / self.settings.max_requests_per_second

    def _pace(self) -> None:
        now = self._clock()
        if self._last_request_at is not None:
            wait = self.min_interval - (now - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._clock()

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), self.settings.max_retry_after_seconds)
            except ValueError:
                pass  # HTTP-date form; fall through to exponential backoff
        base = self.settings.backoff_base_seconds * (2 ** (attempt - 1))
        jitter = self._rng.uniform(0, self.settings.backoff_base_seconds)
        return min(base + jitter, self.settings.backoff_max_seconds)

    def get(self, url: str) -> HttpResponse:
        headers = {
            "User-Agent": self._user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json, text/html;q=0.9, */*;q=0.5",
        }
        last_error = ""
        attempts = self.settings.max_attempts
        for attempt in range(1, attempts + 1):
            self._pace()
            self.request_count += 1
            try:
                resp = self._transport(url, headers, self.settings.timeout_seconds)
            except TransportError as exc:
                last_error = f"network error: {exc}"
                retry_after = None
            else:
                if resp.status == 200:
                    return resp
                if resp.status == 404:
                    raise SourceNotFoundError(f"SEC returned 404 for {url}")
                if resp.status == 403:
                    raise AccessDeniedError(
                        f"SEC returned 403 for {url}. Check that FINSIGHT_SEC_USER_AGENT names "
                        "you and a contact address, and that request pacing has not been changed."
                    )
                if resp.status not in RETRYABLE_STATUS:
                    raise TransientSourceError(f"Unexpected HTTP {resp.status} for {url}")
                last_error = f"HTTP {resp.status}"
                retry_after = {k.lower(): v for k, v in resp.headers.items()}.get("retry-after")
            if attempt < attempts:
                delay = self._backoff(attempt, retry_after)
                log.warning("SEC request failed (%s), attempt %d/%d, retrying in %.1fs",
                            last_error, attempt, attempts, delay)
                self._sleep(delay)
        raise TransientSourceError(f"Gave up on {url} after {attempts} attempts ({last_error})")

from __future__ import annotations

import time
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import requests


class Paused(RuntimeError):
    """Persisted network cooldown or exhausted job budget; safe to resume."""


class FetchError(RuntimeError):
    pass


def api_url(url: str, attributes="results,pagination", count=100) -> str:
    p = urlsplit(url)
    if p.hostname != "www.loc.gov" or p.scheme != "https":
        raise ValueError("API requests must use https://www.loc.gov")
    query = parse_qs(p.query)
    query.update(fo=["json"], at=[attributes])
    if attributes == "results,pagination":
        query["c"] = [str(count)]
        query["sb"] = ["shelf_id"]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(query, doseq=True), ""))


class Client:
    def __init__(
        self,
        state,
        max_requests=700,
        max_seconds=4800,
        session=None,
        clock=time.time,
        sleep=time.sleep,
    ):
        self.state = state
        self.remaining = max_requests
        self.clock, self.sleep = clock, sleep
        self.deadline = clock() + max_seconds
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "OpenGeoMetadata-LOC-Maps/0.1 (+https://github.com/OpenGeoMetadata/gov.loc.maps)",
                "Accept": "application/json",
            }
        )

    def _wait(self):
        current = self.clock()
        if self.remaining <= 0 or current >= self.deadline:
            raise Paused("Job budget exhausted")
        if self.state.get("pause_until", 0) > current:
            raise Paused("LOC cooldown is active; resume after pause_until")
        delay = max(0, self.state.get("next_request", 0) - current)
        if current + delay >= self.deadline:
            raise Paused("Job budget exhausted")
        self.sleep(delay)
        self.state.set("next_request", self.clock() + 6.1)
        self.remaining -= 1

    def pause(self, response):
        retry = response.headers.get("Retry-After", "")
        try:
            delay = float(retry)
        except ValueError:
            try:
                delay = parsedate_to_datetime(retry).timestamp() - self.clock()
            except (ValueError, TypeError):
                delay = 3600
        self.state.set("pause_until", self.clock() + max(3600, delay))
        raise Paused(
            f"LOC returned {response.status_code} or an HTML challenge; paused at least one hour"
        )

    def get(self, url: str) -> dict:
        if urlsplit(url).hostname != "www.loc.gov" or urlsplit(url).scheme != "https":
            raise ValueError("Untrusted API URL")
        last = None
        for attempt in range(3):
            self._wait()
            try:
                response = self.session.get(url, timeout=(15, 90), allow_redirects=False)
                content_type = response.headers.get("Content-Type", "").lower()
                if response.status_code in (403, 429) or "text/html" in content_type:
                    self.pause(response)
                if response.status_code != 200:
                    raise FetchError(f"HTTP {response.status_code}: {url}")
                if "json" not in content_type:
                    raise FetchError(f"Expected JSON, received {content_type}")
                data = response.json()
                if not isinstance(data, dict) or data.get("error"):
                    raise FetchError("Invalid API response object")
                return data
            except (requests.RequestException, ValueError, FetchError) as exc:
                last = exc
                if attempt < 2:
                    self.state.set("next_request", self.clock() + 15 * (2**attempt))
        raise FetchError(f"Failed after three attempts: {last}")

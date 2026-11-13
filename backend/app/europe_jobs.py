"""Client for Arbeitnow's documented public European job-board API."""

import hashlib
import json
import re
import threading
import time
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx

from app.config import settings
from app.job_import import extract_job
from app.jobspy_search import _search_alternatives, _title_matches_search

API_URL = "https://www.arbeitnow.com/api/job-board-api"
USER_AGENT = "CareerPilotJobSearch/1.0 (+https://careerpilot-ai.vercel.app)"
MAX_RESPONSE_BYTES = 4_000_000
MAX_DESCRIPTION_CHARS = 20_000
MAX_PAGES = 3
PAGE_DELAY_SECONDS = 0.2

_cache: dict[tuple[str, str, int], tuple[float, list[dict]]] = {}
_cache_lock = threading.Lock()

_EUROPEAN_COUNTRIES = {
    "albania", "andorra", "austria", "belarus", "belgium", "bosnia and herzegovina",
    "bulgaria", "croatia", "cyprus", "czechia", "czech republic", "denmark",
    "estonia", "finland", "france", "germany", "greece", "hungary", "iceland",
    "ireland", "italy", "kosovo", "latvia", "liechtenstein", "lithuania",
    "luxembourg", "malta", "moldova", "monaco", "montenegro", "netherlands",
    "north macedonia", "norway", "poland", "portugal", "romania", "san marino",
    "serbia", "slovakia", "slovenia", "spain", "sweden", "switzerland",
    "ukraine", "united kingdom", "uk", "vatican city",
}
_CITY_COUNTRIES = {
    "antwerp": "Belgium", "antwerpen": "Belgium", "brussels": "Belgium",
    "bruges": "Belgium", "ghent": "Belgium", "gent": "Belgium",
    "leuven": "Belgium", "liege": "Belgium", "liège": "Belgium",
    "berlin": "Germany", "cologne": "Germany", "dresden": "Germany",
    "dusseldorf": "Germany", "frankfurt": "Germany", "freiburg": "Germany",
    "hamburg": "Germany", "munich": "Germany", "munchen": "Germany",
    "stuttgart": "Germany", "amsterdam": "Netherlands", "rotterdam": "Netherlands",
    "paris": "France", "lyon": "France", "london": "United Kingdom",
    "madrid": "Spain", "barcelona": "Spain", "rome": "Italy", "milan": "Italy",
    "lisbon": "Portugal", "warsaw": "Poland", "vienna": "Austria",
    "stockholm": "Sweden", "copenhagen": "Denmark", "helsinki": "Finland",
    "dublin": "Ireland", "prague": "Czechia", "zurich": "Switzerland",
}


class EuropeJobsError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored = 0

    def handle_starttag(self, tag, _attrs):
        if tag in {"script", "style", "noscript"}:
            self._ignored += 1
        elif not self._ignored:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self._ignored = max(0, self._ignored - 1)
        elif not self._ignored:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self._ignored:
            self.parts.append(data)


def _plain_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    parser = _TextParser()
    parser.feed(value[:MAX_DESCRIPTION_CHARS])
    parser.close()
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()[:MAX_DESCRIPTION_CHARS]


def _normalized(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _country_for_location(location: str) -> str | None:
    normalized = _normalized(location)
    for country in sorted(_EUROPEAN_COUNTRIES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(country)}\b", normalized):
            return "United Kingdom" if country == "uk" else country.title()
    for city, country in _CITY_COUNTRIES.items():
        if re.search(rf"\b{re.escape(city)}\b", normalized):
            return country
    return None


def european_country_for_location(location: str) -> str | None:
    country = _country_for_location(location)
    if country == "Uk":
        return "United Kingdom"
    return country


def _is_european_location(location: str) -> bool:
    normalized = _normalized(location)
    return bool(
        _country_for_location(location)
        or re.search(r"\b(europe|european union|eu)\b", normalized)
    )


def _listing_url(value: object) -> str:
    if not isinstance(value, str) or len(value) > 2000:
        return ""
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return ""
    if (
        parts.scheme.lower() != "https"
        or parts.hostname not in {"arbeitnow.com", "www.arbeitnow.com"}
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
        or not parts.path.startswith("/jobs/")
        or any(char.isspace() or ord(char) < 0x20 for char in value)
    ):
        return ""
    return value


def _published_at(value: object) -> datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    return None


def _map_listing(item: object) -> dict | None:
    if not isinstance(item, dict):
        return None
    title = item.get("title")
    company = item.get("company_name")
    url = _listing_url(item.get("url"))
    if not isinstance(title, str) or not title.strip() or not isinstance(company, str) or not company.strip() or not url:
        return None

    raw_location = item.get("location")
    location = raw_location.strip()[:250] if isinstance(raw_location, str) else ""
    country = _country_for_location(location)
    if country and not re.search(rf"\b{re.escape(country)}\b", location, re.IGNORECASE):
        location = f"{location}, {country}" if location else country

    description = _plain_text(item.get("description"))
    parsed = extract_job(description, url)["draft"]
    slug = item.get("slug")
    stable_id = str(slug).strip() if isinstance(slug, str) else ""
    if not stable_id:
        identity = "|".join(
            _normalized(value)
            for value in (title.strip(), company.strip(), location)
        )
        stable_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    job_types = item.get("job_types")
    if not isinstance(job_types, list):
        job_types = []

    return {
        "source_job_id": stable_id[:100],
        "title": title.strip()[:250],
        "company": company.strip()[:250],
        "location": location or None,
        "url": url,
        "description": description,
        "skills": parsed["skills"],
        "nice_to_have_skills": parsed["nice_to_have_skills"],
        "employment_types": [
            value.strip()[:80]
            for value in job_types[:10]
            if isinstance(value, str) and value.strip()
        ],
        "published_at": _published_at(item.get("created_at")),
        "work_arrangement": "remote" if item.get("remote") is True else parsed["work_arrangement"],
        "experience_level": parsed["experience_level"],
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "",
        "salary_period": "year",
    }


def _fetch_page(page: int) -> list[dict]:
    try:
        with httpx.Client(
            timeout=settings.arbeitnow_timeout_seconds,
            follow_redirects=False,
        ) as client:
            response = client.get(
                API_URL,
                params={"page": page},
                headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            )
    except httpx.TimeoutException:
        raise EuropeJobsError("Arbeitnow took too long to respond. Try again.", 504) from None
    except httpx.RequestError:
        raise EuropeJobsError("Could not connect to Arbeitnow. Try again later.", 502) from None

    if response.status_code == 429:
        raise EuropeJobsError("Arbeitnow rate-limited this search. Wait and try again.", 429)
    if response.status_code != 200:
        raise EuropeJobsError(
            f"Arbeitnow could not complete the search (HTTP {response.status_code}).",
            502,
        )
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise EuropeJobsError("Arbeitnow returned a response that was too large.", 502)
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        raise EuropeJobsError("Arbeitnow returned invalid JSON.", 502) from None
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise EuropeJobsError("Arbeitnow returned an unexpected search response.", 502)
    return payload["data"]


def _cached_page(what: str, where: str, page: int) -> list[dict]:
    key = (_normalized(what), _normalized(where), page)
    current_time = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and cached[0] > current_time:
            return cached[1]
    payload = _fetch_page(page)
    with _cache_lock:
        _cache[key] = (time.monotonic() + settings.arbeitnow_cache_seconds, payload)
    return payload


def _matches_keywords(listing: dict, what: str) -> bool:
    alternatives = _search_alternatives(what)
    if not alternatives:
        return True
    title = listing["title"]
    tags = listing.get("tags", [])
    tags = [tag for tag in tags if isinstance(tag, str)]
    searchable = " ".join(
        [title, listing.get("description", ""), " ".join(tags)]
    )
    return any(
        _title_matches_search(title, alternative)
        or _title_matches_search(searchable, alternative)
        for alternative in alternatives
    )


def search_arbeitnow_jobs(*, what: str, where: str = "", page: int = 1) -> dict:
    """Fetch one cached page and keep only relevant European listings."""
    if not 1 <= page <= MAX_PAGES:
        raise EuropeJobsError(f"Choose a page from 1 to {MAX_PAGES}.", 422)
    if not what.strip():
        raise EuropeJobsError("Enter a job-search keyword.", 422)

    results = []
    where_terms = [term for term in _normalized(where).split() if term]
    if " ".join(where_terms) in {"europe", "eu", "european union"}:
        where_terms = []
    seen_ids: set[str] = set()
    for current_page in range(page, min(MAX_PAGES, page + MAX_PAGES - 1) + 1):
        if current_page > page:
            time.sleep(PAGE_DELAY_SECONDS)
        items = _cached_page(what, where, current_page)
        for item in items:
            listing = _map_listing(item)
            if listing is None:
                continue
            raw_location = str(item.get("location") or "")
            if not _is_european_location(raw_location):
                continue
            if not _matches_keywords({**listing, "tags": item.get("tags", [])}, what):
                continue
            if where_terms and not all(term in _normalized(raw_location) for term in where_terms):
                requested_country = _country_for_location(where)
                listing_country = _country_for_location(raw_location)
                if not requested_country or requested_country != listing_country:
                    continue
            if listing["source_job_id"] not in seen_ids:
                results.append(listing)
                seen_ids.add(listing["source_job_id"])
        if len(results) >= 20:
            break

    return {"source": "arbeitnow", "results": results, "page": page}

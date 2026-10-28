"""Client for Jobicy's public, no-key remote-jobs API."""

import re
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx

from app.job_import import extract_job

API_URL = "https://jobicy.com/api/jobs"
RESULTS_PER_PAGE = 20
REQUEST_TIMEOUT = 10.0
MAX_DESCRIPTION_CHARS = 20_000
ALLOWED_GEOS = frozenset({"apac", "europe", "usa", "canada", "uk"})


class JobicyError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class _VisibleTextParser(HTMLParser):
    _IGNORED_TAGS = frozenset({"script", "style", "noscript"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, _attrs):
        if tag in self._IGNORED_TAGS:
            self._ignored_depth += 1
        elif not self._ignored_depth:
            self.parts.append(" ")

    def handle_endtag(self, tag: str):
        if tag in self._IGNORED_TAGS:
            self._ignored_depth = max(0, self._ignored_depth - 1)
        elif not self._ignored_depth:
            self.parts.append(" ")

    def handle_data(self, data: str):
        if not self._ignored_depth:
            self.parts.append(data)


def _plain_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    parser = _VisibleTextParser()
    parser.feed(value[:MAX_DESCRIPTION_CHARS])
    parser.close()
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()[:MAX_DESCRIPTION_CHARS]


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
        or parts.hostname not in {"jobicy.com", "www.jobicy.com"}
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
        or not parts.path.startswith("/jobs/")
        or any(char.isspace() or ord(char) < 0x20 for char in value)
    ):
        return ""
    return value


def _experience_level(value: object) -> str:
    if not isinstance(value, str):
        return ""
    level = value.strip().casefold()
    if any(term in level for term in ("intern", "trainee")):
        return "intern"
    if any(term in level for term in ("junior", "entry", "graduate", "associate")):
        return "entry"
    if any(term in level for term in ("mid", "intermediate")):
        return "mid"
    if "senior" in level:
        return "senior"
    if any(term in level for term in ("lead", "principal")):
        return "lead"
    return ""


def _published_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _salary(job: dict) -> tuple[int | None, int | None, str, str]:
    period = job.get("salaryPeriod")
    if not isinstance(period, str):
        return None, None, "", "year"
    normalized_period = period.strip().casefold()
    if normalized_period in {"year", "yearly", "annual", "annually"}:
        period_out = "year"
    elif normalized_period in {"month", "monthly"}:
        period_out = "month"
    else:
        return None, None, "", "year"

    currency = job.get("salaryCurrency")
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Za-z]{3}", currency.strip()):
        return None, None, "", period_out

    def amount(value: object) -> int | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not 0 <= value <= 1_000_000_000:
            return None
        return int(value)

    return (
        amount(job.get("salaryMin")),
        amount(job.get("salaryMax")),
        currency.strip().upper(),
        period_out,
    )


def _map_listing(item: object) -> dict | None:
    if not isinstance(item, dict):
        return None
    job_id = item.get("id")
    title = item.get("jobTitle")
    company = item.get("companyName")
    url = _listing_url(item.get("url"))
    if (
        isinstance(job_id, bool)
        or not isinstance(job_id, (int, str))
        or not str(job_id).strip()
        or not isinstance(title, str)
        or not title.strip()
        or not isinstance(company, str)
        or not company.strip()
        or not url
    ):
        return None

    location = item.get("jobGeo")
    location = location.strip()[:250] if isinstance(location, str) and location.strip() else None
    description = _plain_text(item.get("jobDescription") or item.get("jobExcerpt"))
    parsed = extract_job(description, url)["draft"]
    salary_min, salary_max, salary_currency, salary_period = _salary(item)
    job_types = item.get("jobType")
    if not isinstance(job_types, list):
        job_types = []

    return {
        "source_job_id": str(job_id).strip()[:100],
        "title": title.strip()[:250],
        "company": company.strip()[:250],
        "location": location,
        "url": url,
        "description": description,
        "skills": parsed["skills"],
        "nice_to_have_skills": parsed["nice_to_have_skills"],
        "work_arrangement": "remote",
        "experience_level": _experience_level(item.get("jobLevel")) or parsed["experience_level"],
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": salary_currency,
        "salary_period": salary_period,
        "employment_types": [
            value.strip()[:80]
            for value in job_types[:10]
            if isinstance(value, str) and value.strip()
        ],
        "published_at": _published_at(item.get("pubDate")),
    }


def search_jobicy_jobs(*, what: str, where: str, geo: str, cursor: str = "") -> dict:
    """Fetch one public Jobicy page; results remain transient until explicitly saved."""
    if geo not in ALLOWED_GEOS:
        raise JobicyError("Choose a supported Jobicy region.", 422)

    params = {"count": RESULTS_PER_PAGE, "geo": geo}
    if what:
        params["tag"] = what
    if cursor:
        params["cursor"] = cursor

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            response = client.get(API_URL, params=params, headers={"Accept": "application/json"})
    except httpx.TimeoutException:
        raise JobicyError("Jobicy took too long to respond. Try again.", 504) from None
    except httpx.RequestError:
        raise JobicyError("Could not connect to Jobicy. Try again later.", 502) from None

    if response.status_code == 429:
        raise JobicyError("Jobicy rate-limited this search. Wait a little and try again.", 429)
    if response.status_code != 200:
        raise JobicyError(
            f"Jobicy could not complete the search (HTTP {response.status_code}).",
            502,
        )

    try:
        payload = response.json()
    except ValueError:
        raise JobicyError("Jobicy returned a response that was not valid JSON.", 502) from None
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("jobs"), list)
        or not isinstance(payload.get("hasMore"), bool)
    ):
        raise JobicyError("Jobicy returned an unexpected search response.", 502)

    next_cursor = payload.get("nextCursor")
    if next_cursor is not None and not isinstance(next_cursor, str):
        raise JobicyError("Jobicy returned an invalid pagination cursor.", 502)

    results = []
    where_terms = [term.strip().casefold() for term in where.split(",") if term.strip()]
    for item in payload["jobs"]:
        mapped = _map_listing(item)
        if mapped is None:
            continue
        if where_terms:
            searchable_location = (mapped["location"] or "").casefold()
            globally_eligible = any(
                marker in searchable_location
                for marker in ("anywhere", "worldwide", "global", "remote")
            )
            if not globally_eligible and not any(
                term in searchable_location for term in where_terms
            ):
                continue
        results.append(mapped)

    return {
        "source": "jobicy",
        "geo": geo,
        "results": results,
        "has_more": payload["hasMore"],
        "next_cursor": next_cursor,
    }

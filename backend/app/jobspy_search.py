"""Job-board searches backed by the JobSpy library."""

import hashlib
import logging
import math
from datetime import date, datetime
from urllib.parse import urlparse

from jobspy import scrape_jobs

logger = logging.getLogger(__name__)

SUPPORTED_SITES = {
    "linkedin",
    "indeed",
    "glassdoor",
    "zip_recruiter",
    "bayt",
    "naukri",
    "bdjobs",
    "hellowork",
}

_SITE_DOMAINS = {
    "linkedin": ("linkedin.com",),
    "indeed": (
        "indeed.com",
        "indeed.co.in",
        "indeed.co.uk",
        "indeed.ca",
        "indeed.com.au",
        "indeed.fr",
        "indeed.de",
        "indeed.nl",
        "indeed.es",
        "indeed.com.br",
        "indeed.com.sg",
        "indeed.com.mx",
        "indeed.com.hk",
        "indeed.co.jp",
    ),
    "glassdoor": (
        "glassdoor.com",
        "glassdoor.co.in",
        "glassdoor.co.uk",
        "glassdoor.ca",
        "glassdoor.com.au",
        "glassdoor.de",
        "glassdoor.fr",
        "glassdoor.nl",
        "glassdoor.es",
    ),
    "zip_recruiter": ("ziprecruiter.com",),
    "bayt": ("bayt.com",),
    "naukri": ("naukri.com",),
    "bdjobs": ("bdjobs.com",),
    "hellowork": ("hellowork.com",),
}

_SITE_LABELS = {
    "linkedin": "LinkedIn",
    "indeed": "Indeed",
    "glassdoor": "Glassdoor",
    "zip_recruiter": "ZipRecruiter",
    "bayt": "Bayt",
    "naukri": "Naukri",
    "bdjobs": "BDJobs",
    "hellowork": "HelloWork",
}


class JobSpyError(Exception):
    """A search failed or returned data that could not be used."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _value_text(value, limit: int = 12000) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    if text.casefold() in {"nan", "nat", "<na>", "none"}:
        return ""
    return text[:limit]


def _number(value) -> int | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return int(number)


def _date_text(value) -> str | None:
    if isinstance(value, (datetime, date)):
        text = value.isoformat()
        return None if text.casefold() in {"nan", "nat"} else text
    text = _value_text(value, 80)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _job_url(value, site: str) -> str | None:
    url = _value_text(value, 2000)
    try:
        parts = urlparse(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if (
        parts.scheme != "https"
        or not any(host == domain or host.endswith(f".{domain}") for domain in _SITE_DOMAINS[site])
    ):
        return None
    return url


def _string_list(value) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [text for item in value if (text := _value_text(item, 100))]
    text = _value_text(value, 100)
    return [text] if text else []


def search_jobspy_jobs(
    *,
    site: str,
    what: str,
    where: str,
    job_type: str | None = None,
    is_remote: bool | None = None,
    hours_old: int | None = None,
) -> dict:
    """Search one supported board and return transient, normalized listings."""
    if site not in SUPPORTED_SITES:
        raise JobSpyError("Choose a supported job-search source.", 422)

    try:
        frame = scrape_jobs(
            site_name=[site],
            search_term=what,
            location=where,
            results_wanted=20,
            job_type=job_type,
            is_remote=is_remote,
            hours_old=hours_old,
        )
    except Exception as error:
        logger.exception("JobSpy search failed for %s.", site)
        raise JobSpyError(f"{_SITE_LABELS[site]} search is unavailable right now.") from error

    if frame is None or not hasattr(frame, "to_dict"):
        raise JobSpyError("JobSpy returned an invalid search response.")

    try:
        rows = frame.to_dict(orient="records")
    except (TypeError, ValueError) as error:
        logger.exception("JobSpy returned an unreadable result for %s.", site)
        raise JobSpyError("JobSpy returned an invalid search response.") from error

    results = []
    for row in rows:
        url_value = row.get("job_url")
        if not _value_text(url_value):
            url_value = row.get("url")
        url = _job_url(url_value, site)
        title = _value_text(row.get("title"), 250)
        company = _value_text(row.get("company"), 250)
        if not url or not title or not company:
            continue

        provider_id = _value_text(row.get("id"), 200)
        source_job_id = provider_id or hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
        interval = _value_text(row.get("interval"), 40).lower()
        remote = _value_text(row.get("is_remote"), 10).lower() in {
            "true",
            "yes",
            "remote",
            "1",
        }

        results.append({
            "source_job_id": source_job_id,
            "title": title,
            "company": company,
            "location": _value_text(row.get("location"), 250) or None,
            "url": url,
            "description": _value_text(row.get("description")),
            "skills": [],
            "nice_to_have_skills": [],
            "employment_types": _string_list(row.get("job_type")),
            "published_at": _date_text(row.get("date_posted")),
            "work_arrangement": "remote" if remote else "",
            "experience_level": "",
            "salary_min": _number(row.get("min_amount")),
            "salary_max": _number(row.get("max_amount")),
            "salary_currency": _value_text(row.get("currency"), 10).upper(),
            "salary_period": "month" if "month" in interval else "year",
        })

    return {"source": site, "results": results}

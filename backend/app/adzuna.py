"""Backend client for Adzuna's public job-search API."""

from urllib.parse import urlsplit

import httpx

from app.config import settings
from app.job_import import extract_job

API_BASE = "https://api.adzuna.com/v1/api"
RESULTS_PER_PAGE = 20
REQUEST_TIMEOUT = 10.0


class AdzunaError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _safe_listing_url(value) -> str:
    if not isinstance(value, str) or len(value) > 2000:
        return ""
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return ""
    if (
        parts.scheme.lower() not in ("http", "https")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or (port is not None and port not in (80, 443))
        or any(char.isspace() or ord(char) < 0x20 for char in value)
    ):
        return ""
    return value


def search_adzuna_jobs(
    *,
    country: str,
    what: str,
    where: str,
    page: int,
) -> dict:
    """Fetch one live results page without persisting it."""
    app_id = settings.adzuna_app_id.strip()
    app_key = settings.adzuna_app_key.strip()
    if not app_id or not app_key:
        raise AdzunaError(
            "Job search is not configured. Set ADZUNA_APP_ID and ADZUNA_APP_KEY "
            "in backend/.env, then restart the backend.",
            503,
        )

    url = f"{API_BASE}/jobs/{country}/search/{page}"
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": RESULTS_PER_PAGE,
        "what": what,
        "content-type": "application/json",
    }
    if where:
        params["where"] = where

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            response = client.get(url, params=params, headers={"Accept": "application/json"})
    except httpx.TimeoutException:
        raise AdzunaError("Adzuna took too long to respond. Try again.", 504) from None
    except httpx.RequestError:
        raise AdzunaError("Could not connect to Adzuna. Try again later.", 502) from None

    if response.status_code == 429:
        raise AdzunaError("Adzuna rate-limited this search. Wait a little and try again.", 429)
    if response.status_code in (401, 403):
        raise AdzunaError(
            "Adzuna rejected the API credentials or access. Check the backend configuration "
            "and your Adzuna account.",
            502,
        )
    if response.status_code != 200:
        raise AdzunaError(
            f"Adzuna could not complete the search (HTTP {response.status_code}).",
            502,
        )

    try:
        payload = response.json()
    except ValueError:
        raise AdzunaError("Adzuna returned a response that was not valid JSON.", 502) from None
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise AdzunaError("Adzuna returned an unexpected search response.", 502)

    results = []
    for item in payload["results"]:
        if not isinstance(item, dict):
            continue
        job_id = item.get("id")
        title = item.get("title")
        listing_url = _safe_listing_url(item.get("redirect_url"))
        if job_id is None or not isinstance(title, str) or not title.strip() or not listing_url:
            continue

        company_data = item.get("company")
        company = company_data.get("display_name") if isinstance(company_data, dict) else ""
        location_data = item.get("location")
        location = (
            location_data.get("display_name")
            if isinstance(location_data, dict)
            else None
        )
        description = item.get("description")
        description = description[:20_000] if isinstance(description, str) else ""
        parsed = extract_job(description, listing_url)["draft"]

        results.append(
            {
                "source_job_id": str(job_id),
                "title": title.strip()[:250],
                "company": (
                    company.strip()[:250]
                    if isinstance(company, str) and company.strip()
                    else "Company not listed"
                ),
                "location": location.strip()[:250] if isinstance(location, str) and location.strip() else None,
                "url": listing_url,
                "description": description,
                "skills": parsed["skills"],
                "nice_to_have_skills": parsed["nice_to_have_skills"],
                "work_arrangement": parsed["work_arrangement"],
                "experience_level": parsed["experience_level"],
                "salary_min": parsed["salary_min"],
                "salary_max": parsed["salary_max"],
                "salary_currency": parsed["salary_currency"],
                "salary_period": parsed["salary_period"],
            }
        )

    return {
        "source": "adzuna",
        "country": country,
        "page": page,
        "results": results,
        "has_more": len(payload["results"]) >= RESULTS_PER_PAGE,
        "description_is_snippet": True,
    }

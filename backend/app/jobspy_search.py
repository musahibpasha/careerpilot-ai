



"""Job-board searches backed by the JobSpy library."""

import hashlib
import logging
import math
import re
from datetime import date, datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from jobspy import Country, scrape_jobs

from app.assistant_urls import UrlRejected, check_fill_url
from app.job_import import infer_job_attributes

logger = logging.getLogger(__name__)

SUPPORTED_SITES = (
    "linkedin",
    "indeed",
    "glassdoor",
)

_SITE_DOMAINS = {
    "linkedin": ("linkedin.com",),
    "indeed": (
        "indeed.com",
        "indeed.co.in",
        "indeed.co.uk",
        "indeed.ca",
        "indeed.com.au",
        "indeed.be",
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
        "glassdoor.be",
        "glassdoor.de",
        "glassdoor.fr",
        "glassdoor.nl",
        "glassdoor.es",
    ),
}

_SITE_LABELS = {
    "linkedin": "LinkedIn",
    "indeed": "Indeed",
    "glassdoor": "Glassdoor",
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


def _job_url_identity(url: str, site: str) -> str:
    parts = urlparse(url)
    path = parts.path.rstrip("/") or "/"
    if site == "linkedin":
        query = ""
    else:
        query = urlencode(sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_")
            and key.casefold() not in {"trk", "trackingid", "refid", "from", "source", "src"}
        ))
    return urlunparse((
        parts.scheme.lower(),
        (parts.hostname or "").lower(),
        path,
        "",
        query,
        "",
    ))


def _company_logo_url(value) -> str | None:
    url = _value_text(value, 2000)
    try:
        parts = urlparse(url)
        port = parts.port
    except ValueError:
        return None
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
        or any(char.isspace() or ord(char) < 0x20 for char in url)
    ):
        return None
    return url


def _supported_application_url(value) -> str | None:
    raw = _value_text(value, 2000)
    if not raw:
        return None
    try:
        target = check_fill_url(raw, allow_local_form=False)
    except UrlRejected:
        return None
    return target.url if target.adapter in {"greenhouse", "lever"} else None


def _string_list(value) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        items = [_value_text(item, 100) for item in value]
        return [part.strip() for item in items for part in item.split(",") if part.strip()]
    text = _value_text(value, 100)
    return [part.strip() for part in text.split(",") if part.strip()]


def _title_search_terms(text: str) -> list[str]:
    return re.findall(r"[^\W_]+(?:[+#]+)?", text.casefold())


def _search_alternatives(search_term: str) -> list[str]:
    alternatives = re.split(r"\s*(?:\bOR\b|[,;\n\r|])\s*", search_term, flags=re.IGNORECASE)
    return list(dict.fromkeys(term.strip() for term in alternatives if term.strip()))


def _title_matches_search(title: str, search_term: str) -> bool:
    title_terms = set(_title_search_terms(title))
    terms = _title_search_terms(search_term)
    return bool(terms) and all(term in title_terms for term in terms)


def _country_from_location(where: str) -> str | None:
    countries = {country.value.casefold(): country.value for country in Country}
    aliases = {
        "united kingdom": "uk",
        "united states": "usa",
        "united states of america": "usa",
    }
    fragments = [part.strip().casefold() for part in where.split(",") if part.strip()]
    for fragment in reversed(fragments):
        country = aliases.get(fragment, fragment)
        if country in countries:
            return countries[country]
    return None


def _indeed_country(where: str) -> str:
    """Use a country suffix from the location, defaulting to Belgium."""
    countries = {country.value.casefold(): country.value for country in Country}
    aliases = {
        "brussels": "belgium",
        "antwerp": "belgium",
        "ghent": "belgium",
        "belgie": "belgium",
        "belgique": "belgium",
    }
    if country := _country_from_location(where):
        return country
    fragments = [part.strip().casefold() for part in where.split(",") if part.strip()]
    for fragment in reversed(fragments):
        country = countries.get(aliases.get(fragment, fragment))
        if country:
            return country
    return countries["belgium"]


def is_english_posting(title: str, description: str) -> bool:
    english_stopwords = {
        "the", "and", "with", "you", "will", "experience", "role", "team",
        "work", "skills", "requirements", "our", "looking",
    }
    non_english_stopwords = {
        "wij", "zoeken", "voor", "een", "met", "werkzaamheden", "kennis",
        "ervaring", "jouw", "functie", "nous", "recherchons", "pour", "une",
        "dans", "avec", "entreprise", "poste", "profil", "vous", "wir",
        "suchen", "für", "eine", "einen", "mit", "aufgaben", "kenntnisse",
        "dein", "m/w/d",
    }
    text = f"{title} {description}".casefold()
    words = re.findall(r"[a-zA-ZÀ-ÖØ-öø-ÿ]+", text)
    english_count = sum(word in english_stopwords for word in words)
    non_english_count = sum(word in non_english_stopwords for word in words)
    if re.search(r"(?<!\w)m/w/d(?!\w)", text):
        non_english_count += 1
    return not (non_english_count >= 5 and non_english_count > english_count)


_LEVEL_FILTERS = {
    "internship": {"intern"},
    "entry": {"entry"},
    "associate": {"mid"},
    "mid-senior": {"mid", "senior"},
    "director": {"lead"},
    "executive": {"lead"},
}
_JOB_TYPE_ALIASES = {
    "fulltime": "fulltime",
    "parttime": "parttime",
    "internship": "internship",
    "intern": "internship",
    "contract": "contract",
    "freelance": "contract",
    "temporary": "temporary",
    "perdiem": "perdiem",
    "volunteer": "volunteer",
    "other": "other",
}
_JOB_TYPE_PATTERNS = {
    "fulltime": r"\bfull[- ]?time\b",
    "parttime": r"\bpart[- ]?time\b",
    "internship": r"\bintern(?:ship)?\b",
    "contract": r"\bcontract(?:or)?\b",
    "freelance": r"\bfreelanc(?:e|er|ing)\b",
    "temporary": r"\btemporary\b",
    "perdiem": r"\bper[- ]diem\b",
    "volunteer": r"\bvolunteer(?:ing)?\b",
}
_ANNUAL_FACTOR = {
    "year": 1,
    "yearly": 1,
    "month": 12,
    "monthly": 12,
    "week": 52,
    "weekly": 52,
    "day": 260,
    "daily": 260,
    "hour": 2080,
    "hourly": 2080,
}


def _normalized_job_types(value) -> set[str]:
    return {
        _JOB_TYPE_ALIASES.get(re.sub(r"[^a-z0-9]", "", text.casefold()), text.casefold())
        for text in _string_list(value)
    }


def _job_matches_filters(
    *,
    job_type: str | None,
    work_arrangement: str | None,
    experience_level: str | None,
    salary_min: int | None,
    salary_max: int | None,
    salary_currency: str | None,
    salary_period: str,
    row_job_types: list[str],
    arrangement: str,
    level: str,
    found_salary_min: int | None,
    found_salary_max: int | None,
    found_salary_currency: str,
    found_salary_period: str,
) -> bool:
    if job_type and job_type not in _normalized_job_types(row_job_types):
        return False
    if work_arrangement and arrangement != work_arrangement:
        return False
    if experience_level and level not in _LEVEL_FILTERS[experience_level]:
        return False
    if salary_min is None and salary_max is None:
        return True
    if not found_salary_currency or found_salary_currency.casefold() != (salary_currency or "").casefold():
        return False
    normalized_min = _convert_salary_period(
        found_salary_min, found_salary_period, salary_period
    )
    normalized_max = _convert_salary_period(
        found_salary_max, found_salary_period, salary_period
    )
    if normalized_min is None and normalized_max is None:
        return False
    if salary_min is not None and (normalized_max is None or normalized_max < salary_min):
        return False
    if salary_max is not None and (normalized_min is None or normalized_min > salary_max):
        return False
    return True


def _convert_salary_period(
    amount: int | None, source_period: str, target_period: str
) -> int | None:
    if amount is None:
        return None
    source_factor = _ANNUAL_FACTOR.get(source_period.casefold())
    target_factor = _ANNUAL_FACTOR.get(target_period.casefold())
    if source_factor is None or target_factor is None:
        return None
    return round(amount * source_factor / target_factor)


def search_jobspy_jobs(
    *,
    site: str,
    what: str,
    where: str,
    job_type: str | None = None,
    is_remote: bool | None = None,
    easy_apply: bool | None = None,
    hours_old: int | None = None,
    work_arrangement: str | None = None,
    experience_level: str | None = None,
    salary_min: int | None = None,
    salary_max: int | None = None,
    salary_currency: str | None = None,
    salary_period: str = "year",
    sort_by: str = "match",
    english_only: bool = False,
) -> dict:
    """Search one supported board and return transient, normalized listings."""
    if site not in SUPPORTED_SITES:
        raise JobSpyError("Choose a supported job-search source.", 422)
    if experience_level is not None and experience_level not in _LEVEL_FILTERS:
        raise JobSpyError("Choose a supported experience level.", 422)
    if work_arrangement not in (None, "remote", "hybrid", "onsite"):
        raise JobSpyError("Choose a supported work arrangement.", 422)
    if salary_period not in ("year", "month"):
        raise JobSpyError("Choose a supported salary period.", 422)
    if salary_min is not None and salary_max is not None and salary_max < salary_min:
        raise JobSpyError("Maximum salary cannot be lower than minimum salary.", 422)

    effective_job_type = "contract" if job_type == "freelance" else job_type
    alternatives = _search_alternatives(what) or [what.strip()]
    search_options = {
        "site_name": [site],
        "location": where,
        "results_wanted": 20,
        "job_type": effective_job_type,
        "is_remote": is_remote,
        "easy_apply": easy_apply,
        "hours_old": hours_old,
        "fetch_description": any((
            job_type is not None and site == "linkedin",
            english_only,
            experience_level is not None,
            (salary_min is not None or salary_max is not None) and site == "linkedin",
            work_arrangement in {"hybrid", "onsite"},
        )),
    }
    if site == "indeed":
        search_options["country_indeed"] = _indeed_country(where)
    elif site == "glassdoor" and (country := _country_from_location(where)):
        search_options["country_indeed"] = country

    results_by_alternative = []
    for alternative in alternatives:
        try:
            frame = scrape_jobs(search_term=alternative, **search_options)
        except Exception as error:
            logger.exception("JobSpy search failed for %s.", site)
            raise JobSpyError(
                f"{_SITE_LABELS[site]} search is unavailable right now."
            ) from error

        if frame is None or not hasattr(frame, "to_dict"):
            raise JobSpyError("JobSpy returned an invalid search response.")

        try:
            rows = frame.to_dict(orient="records")
        except (TypeError, ValueError) as error:
            logger.exception("JobSpy returned an unreadable result for %s.", site)
            raise JobSpyError("JobSpy returned an invalid search response.") from error

        alternative_results = []
        seen_urls = set()
        for row in rows:
            url_value = row.get("job_url")
            if not _value_text(url_value):
                url_value = row.get("url")
            url = _job_url(url_value, site)
            title = _value_text(row.get("title"), 250)
            company = _value_text(row.get("company"), 250)
            description = _value_text(row.get("description"))
            if english_only and not is_english_posting(title, description):
                continue
            if (
                not url
                or not title
                or not company
                or url in seen_urls
                or not _title_matches_search(title, alternative)
            ):
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
            row_job_types = _string_list(row.get("job_type"))
            arrangement_text = " ".join((
                _value_text(row.get("work_from_home_type"), 100),
                _value_text(row.get("location"), 250),
            )).casefold()
            attributes = infer_job_attributes(
                title,
                "\n".join(filter(None, (
                    title,
                    description,
                    _value_text(row.get("experience_range"), 200),
                    _value_text(row.get("job_level"), 100),
                ))),
            )
            if "hybrid" in arrangement_text:
                arrangement = "hybrid"
            elif "remote" in arrangement_text or remote:
                arrangement = "remote"
            elif any(term in arrangement_text for term in ("office", "onsite", "on-site")):
                arrangement = "onsite"
            else:
                arrangement = attributes["work_arrangement"]
            level = attributes["experience_level"]
            if not row_job_types and description:
                description_lower = description.casefold()
                row_job_types = [
                    _JOB_TYPE_ALIASES[keyword]
                    for keyword, pattern in _JOB_TYPE_PATTERNS.items()
                    if re.search(pattern, description_lower)
                ]
            found_salary_min = _number(row.get("min_amount"))
            found_salary_max = _number(row.get("max_amount"))
            found_salary_currency = _value_text(row.get("currency"), 10).upper()
            found_salary_period = interval
            if found_salary_min is None and found_salary_max is None:
                salary = attributes
                found_salary_min = salary.get("salary_min")
                found_salary_max = salary.get("salary_max")
                found_salary_currency = _value_text(salary.get("salary_currency"), 10).upper()
                found_salary_period = salary.get("salary_period", "")
            display_period = salary_period if salary_min is not None or salary_max is not None else (
                "month" if found_salary_period in {"month", "monthly"} else "year"
            )
            display_salary_min = _convert_salary_period(
                found_salary_min, found_salary_period, display_period
            )
            display_salary_max = _convert_salary_period(
                found_salary_max, found_salary_period, display_period
            )
            effective_arrangement_filter = work_arrangement or (
                "remote" if is_remote else None
            )
            if not _job_matches_filters(
                job_type=effective_job_type,
                work_arrangement=effective_arrangement_filter,
                experience_level=experience_level,
                salary_min=salary_min,
                salary_max=salary_max,
                salary_currency=salary_currency,
                salary_period=salary_period,
                row_job_types=row_job_types,
                arrangement=arrangement,
                level=level,
                found_salary_min=found_salary_min,
                found_salary_max=found_salary_max,
                found_salary_currency=found_salary_currency,
                found_salary_period=found_salary_period,
            ):
                continue
            seen_urls.add(url)
            employment_types = [
                "Freelance / Contract" if "contract" in job_type.casefold() else job_type
                for job_type in row_job_types
            ]

            alternative_results.append({
                "source_job_id": source_job_id,
                "title": title,
                "company": company,
                "company_logo_url": _company_logo_url(
                    row.get("company_logo") or row.get("company_logo_url")
                ),
                "location": _value_text(row.get("location"), 250) or None,
                "url": url,
                "application_url": _supported_application_url(row.get("job_url_direct")),
                "description": description,
                "skills": [],
                "nice_to_have_skills": [],
                "employment_types": employment_types,
                "published_at": _date_text(row.get("date_posted")),
                "work_arrangement": arrangement,
                "experience_level": level,
                "salary_min": display_salary_min,
                "salary_max": display_salary_max,
                "salary_currency": found_salary_currency,
                "salary_period": display_period,
            })
        results_by_alternative.append(alternative_results)

    results = []
    seen_urls = set()
    seen_job_ids = set()
    while len(results) < 20:
        progressed = False
        for alternative_results in results_by_alternative:
            if not alternative_results:
                continue
            job = alternative_results.pop(0)
            progressed = True
            url_identity = _job_url_identity(job["url"], site)
            if (
                url_identity in seen_urls
                or job["source_job_id"] in seen_job_ids
            ):
                continue
            seen_urls.add(url_identity)
            seen_job_ids.add(job["source_job_id"])
            results.append(job)
            if len(results) == 20:
                break
        if not progressed:
            break

    if sort_by == "newest":
        results.sort(
            key=lambda job: job["published_at"] or "",
            reverse=True,
        )

    return {"source": site, "results": results}

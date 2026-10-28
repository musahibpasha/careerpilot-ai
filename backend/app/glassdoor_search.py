"""Glassdoor job search URL builder.

Builds a Glassdoor search URL from filter parameters.
No HTTP requests are made here — the URL is opened in the user's browser.

Supported Glassdoor URL parameters:
  sc.keyword   – job title / keywords
  locT         – location type: "C" (city), "S" (state), "N" (nation), "M" (metro)
  locId        – Glassdoor location ID (we use keyword-based search instead)
  jobType      – "fulltime", "parttime", "contract", "internship"
  remoteWorkType – 1 (remote), 2 (hybrid), 3 (onsite)
  fromAge      – days since posting: 1, 3, 7, 14, 30

For India-based searches we route to glassdoor.co.in; for others to glassdoor.com.
"""

from urllib.parse import urlencode

_WORK_ARRANGEMENT_MAP = {
    "remote": "1",
    "hybrid": "2",
    "onsite": "3",
    "on-site": "3",
    "on_site": "3",
}

_EMPLOYMENT_TYPE_MAP = {
    "full-time": "fulltime",
    "full_time": "fulltime",
    "fulltime": "fulltime",
    "part-time": "parttime",
    "part_time": "parttime",
    "parttime": "parttime",
    "contract": "contract",
    "internship": "internship",
    "intern": "internship",
}

_POSTED_WITHIN_MAP = {
    "1": "1",
    "3": "3",
    "7": "7",
    "14": "14",
    "30": "30",
    "any": None,
}

_BASE_IN = "https://www.glassdoor.co.in/Job/jobs.htm"
_BASE_COM = "https://www.glassdoor.com/Job/jobs.htm"


def build_glassdoor_url(
    *,
    keywords: str = "",
    location: str = "",
    work_arrangement: str = "any",
    employment_type: str = "",
    experience_level: str = "",
    posted_within: str = "any",
    sort: str = "relevance",
    use_india_domain: bool = True,
) -> str:
    """Return a Glassdoor search URL with all supplied filters applied.

    All parameters are optional; omitted ones are left out of the URL so
    Glassdoor uses its own defaults.

    Args:
        keywords:         job title or keyword string.
        location:         city / country string (e.g. "Bangalore, India").
        work_arrangement: "remote" | "hybrid" | "onsite" | "any".
        employment_type:  "full-time" | "part-time" | "contract" | "internship" | "".
        experience_level: "intern" | "entry" | "mid" | "senior" | "lead" | "".
                          Glassdoor doesn't have a direct level filter in the URL
                          so we append it to the keyword string instead.
        posted_within:    "1" | "3" | "7" | "14" | "30" | "any".
        sort:             "relevance" | "date".
        use_india_domain: True → glassdoor.co.in, False → glassdoor.com.

    Returns:
        A valid https:// URL string.
    """
    params: dict[str, str] = {}

    # Keywords (append experience level hint when no explicit Glassdoor param exists)
    kw = keywords.strip()
    if experience_level and experience_level not in ("any", ""):
        level_hint = {
            "intern": "intern OR internship",
            "entry": "junior OR entry level",
            "mid": "mid level",
            "senior": "senior",
            "lead": "lead OR principal",
        }.get(experience_level, "")
        if level_hint and level_hint.lower() not in kw.lower():
            kw = f"{kw} {level_hint}".strip() if kw else level_hint
    if kw:
        params["sc.keyword"] = kw

    # Location
    loc = location.strip()
    if loc:
        params["locKeyword"] = loc

    # Work arrangement (remoteWorkType)
    arr = _WORK_ARRANGEMENT_MAP.get(work_arrangement.lower().strip(), "")
    if arr:
        params["remoteWorkType"] = arr

    # Employment type (jobType)
    etype = _EMPLOYMENT_TYPE_MAP.get(employment_type.lower().strip(), "")
    if etype:
        params["jobType"] = etype

    # Posted within (fromAge)
    age = _POSTED_WITHIN_MAP.get(posted_within.strip(), None)
    if age:
        params["fromAge"] = age

    # Sort (sortType)  D = date, R = relevance
    if sort == "date":
        params["sortType"] = "D"

    base = _BASE_IN if use_india_domain else _BASE_COM
    if params:
        return f"{base}?{urlencode(params)}"
    return base


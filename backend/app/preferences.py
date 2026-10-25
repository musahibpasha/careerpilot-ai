"""Job preference rules: allowed values, cleaning and checking. No FastAPI,
pydantic or database imports here, so these rules are easy to test alone.

Preferences are only saved for now. They are NOT used in match scores until
Stage 3 (explainable matching), where each factor will show its reason."""

import re

EXPERIENCE_LEVELS = ("intern", "entry", "mid", "senior", "lead")
WORK_ARRANGEMENTS = ("remote", "hybrid", "onsite")
SALARY_PERIODS = ("year", "month")

MAX_LOCATION_LENGTH = 100


def clean_preferences(data: dict) -> dict:
    """Tidy the values: trim spaces, drop blanks and repeats, upper-case the currency."""
    out = dict(data)

    seen = set()
    locations = []
    for item in data.get("locations", []):
        text = " ".join(str(item).split())
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            locations.append(text)
    out["locations"] = locations

    arrangements = []
    for item in data.get("work_arrangements", []):
        if item not in arrangements:
            arrangements.append(item)
    out["work_arrangements"] = arrangements

    out["salary_currency"] = str(data.get("salary_currency", "")).strip().upper()
    return out


def preferences_problems(data: dict) -> list[str]:
    """Human-readable problems. An empty list means the preferences are fine.
    Every field is optional, so an empty set of preferences is valid."""
    problems: list[str] = []

    for location in data.get("locations", []):
        if len(location) > MAX_LOCATION_LENGTH:
            problems.append(
                f"Location '{location[:20]}...' is too long "
                f"(max {MAX_LOCATION_LENGTH} characters)"
            )

    low = data.get("salary_min")
    high = data.get("salary_max")

    if low is not None and high is not None and high < low:
        problems.append("Maximum salary cannot be lower than minimum salary")

    if (low is not None or high is not None) and not re.fullmatch(
        r"[A-Z]{3}", data.get("salary_currency", "")
    ):
        problems.append(
            "A 3-letter currency code (for example INR or USD) is required "
            "when a salary is given"
        )

    return problems
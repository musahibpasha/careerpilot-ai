"""Conservative duplicate and repost detection for supported job-board listings."""

from datetime import datetime, timezone
from difflib import SequenceMatcher
import re
import unicodedata

from pymongo.errors import DuplicateKeyError


_COMPANY_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation",
    "plc", "gmbh", "sarl", "sa", "pty", "private", "pvt", "company", "co",
}
_TITLE_ALIASES = {"sr": "senior", "jr": "junior", "swe": "software engineer"}
_LOCATION_ALIASES = {"bangalore": "bengaluru", "bombay": "mumbai"}
_MAX_REPOST_DAYS = 120
_MAX_CROSS_BOARD_DAYS = 60


def _normalized(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _company_key(value: str | None) -> str:
    return " ".join(
        word for word in _normalized(value).split()
        if word not in _COMPANY_SUFFIXES
    )


def _company_root(value: str | None) -> str:
    return next(
        (word for word in _company_key(value).split() if word != "the"),
        "",
    )


def _title_key(value: str | None) -> str:
    return " ".join(
        expanded
        for word in _normalized(value).split()
        for expanded in _TITLE_ALIASES.get(word, word).split()
    )


def _location_key(value: str | None) -> str:
    return " ".join(
        _LOCATION_ALIASES.get(word, word)
        for word in _normalized(value).split()
    )


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right).ratio()


def _description_similarity(left: str | None, right: str | None) -> float | None:
    first = _normalized((left or "")[:6000])
    second = _normalized((right or "")[:6000])
    if not first or not second:
        return None
    sequence_score = _similarity(first, second)
    first_words, second_words = set(first.split()), set(second.split())
    overlap_score = len(first_words & second_words) / max(
        1, len(first_words | second_words)
    )
    return max(sequence_score, overlap_score)


def _substantial_description(value: str | None) -> bool:
    normalized = _normalized((value or "")[:6000])
    return len(normalized.split()) >= 20 and len(normalized) >= 120


def _parse_date(value: str | datetime | None) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _employment_types(job: dict) -> set[str]:
    return {
        _normalized(item)
        for item in job.get("employment_types", [])
        if isinstance(item, str) and _normalized(item)
    }


def compare_listings(new: dict, existing: dict) -> tuple[str, float]:
    """Classify a listing match as high, medium, or low confidence."""
    new_company = _company_key(new.get("company"))
    old_company = _company_key(existing.get("company"))
    company_score = (
        _similarity(new_company, old_company)
        if new_company and old_company
        else 0
    )
    if company_score < 0.9:
        return "low", 0.0

    new_title, old_title = _title_key(new.get("title")), _title_key(existing.get("title"))
    if not new_title or not old_title:
        return "low", 0.0
    title_score = _similarity(new_title, old_title)
    if title_score < 0.86:
        return "low", 0.0
    exact_title = new_title == old_title

    new_location = _location_key(new.get("location"))
    old_location = _location_key(existing.get("location"))
    if new_location and old_location and new_location != old_location:
        return "low", 0.0
    exact_location = bool(new_location and old_location)

    new_types, old_types = _employment_types(new), _employment_types(existing)
    if new_types and old_types and new_types.isdisjoint(old_types):
        return "low", 0.0
    exact_types = bool(new_types and old_types and new_types == old_types)

    new_url = _normalized(new.get("url"))
    old_url = _normalized(existing.get("url"))
    same_url = bool(new_url and old_url and new_url == old_url)
    description_score = _description_similarity(
        new.get("description"), existing.get("description")
    )
    new_date = _parse_date(new.get("published_at"))
    prior_dates = []
    for item in existing.get("source_listings", []):
        for value in [item.get("published_at"), *(item.get("repost_dates") or [])]:
            if parsed := _parse_date(value):
                prior_dates.append(parsed)
    if not prior_dates and (prior_date := _parse_date(existing.get("published_at"))):
        prior_dates.append(prior_date)
    day_gap = (
        min(abs((new_date - prior_date).total_seconds()) / 86400 for prior_date in prior_dates)
        if new_date and prior_dates
        else None
    )

    structural_match = (
        exact_title
        and exact_location
        and exact_types
        and company_score >= 0.97
    )
    if (
        same_url
        and exact_title
        and company_score >= 0.9
        and (not new_location or not old_location or exact_location)
        and (description_score is None or description_score >= 0.5)
    ):
        return "high", min(company_score, description_score or 1.0)

    same_board = new.get("source") == existing.get("source")
    if structural_match and description_score is not None:
        if same_board:
            if (
                description_score >= 0.97
                and _substantial_description(new.get("description"))
                and _substantial_description(existing.get("description"))
                and day_gap is not None
                and 0 < day_gap <= _MAX_REPOST_DAYS
            ):
                return "high", min(company_score, description_score)
        elif (
            description_score >= 0.93
            and _substantial_description(new.get("description"))
            and _substantial_description(existing.get("description"))
            and day_gap is not None
            and day_gap <= _MAX_CROSS_BOARD_DAYS
        ):
            return "high", min(company_score, description_score)

    if (
        title_score >= 0.86
        and company_score >= 0.9
        and (not new_location or not old_location or exact_location)
        and (description_score is None or description_score >= 0.45)
    ):
        return "medium", min(
            company_score,
            title_score,
            description_score if description_score is not None else 0.5,
        )
    return "low", 0.0


def _source_identity(source: str, source_job_id: str) -> str:
    return f"{source}:{source_job_id}"


def _source_reference(job: dict) -> dict:
    return {
        "source": job["source"],
        "source_job_id": str(job["source_job_id"]),
        "title": job.get("title", ""),
        "url": job.get("url"),
        "published_at": job.get("published_at"),
        "repost_dates": [],
    }


def _source_names(references: list[dict]) -> list[str]:
    return sorted({
        item["source"] for item in references
        if isinstance(item.get("source"), str)
    })


def _record_group(collection, job: dict, status: str, possible_duplicate_of=None) -> dict:
    identity = _source_identity(job["source"], str(job["source_job_id"]))
    references = [_source_reference(job)]
    document = {
        **job,
        "company_root": _company_root(job.get("company")),
        "source_identities": [identity],
        "source_listings": references,
        "available_sources": [job["source"]],
        "duplicate_status": status,
        "duplicate_confidence": None,
        "potential_duplicate_of": possible_duplicate_of,
        "original_published_at": job.get("published_at"),
        "latest_repost_at": None,
        "repost_count": 0,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    try:
        result = collection.insert_one(document)
        document["_id"] = result.inserted_id
        return document
    except DuplicateKeyError:
        # Another request may have recorded this source identity concurrently.
        existing = collection.find_one({"source_identities": identity})
        if existing is None:
            raise
        return existing


def _append_source(collection, group: dict, job: dict, status: str) -> dict:
    identity = _source_identity(job["source"], str(job["source_job_id"]))
    references = list(group.get("source_listings") or [])
    references.append(_source_reference(job))
    sources = _source_names(references)
    available_dates = [
        parsed
        for parsed in (_parse_date(item.get("published_at")) for item in references)
        if parsed
    ]
    latest_posted = _parse_date(job.get("published_at"))
    first_posted = _parse_date(group.get("original_published_at"))
    if first_posted is None and available_dates:
        first_posted = min(available_dates)
    elif first_posted is not None and available_dates:
        first_posted = min([first_posted, *available_dates])

    is_repost = job["source"] in (group.get("available_sources") or [])
    updates = {
        "source_identities": [*(group.get("source_identities") or []), identity],
        "source_listings": references,
        "available_sources": sources,
        "duplicate_status": status,
        "duplicate_confidence": "high",
        "original_published_at": first_posted,
        "updated_at": datetime.now(timezone.utc),
    }
    if is_repost:
        updates["repost_count"] = int(group.get("repost_count", 0)) + 1
        if latest_posted:
            old_latest = _parse_date(group.get("latest_repost_at"))
            updates["latest_repost_at"] = max(
                (date for date in (old_latest, latest_posted) if date),
                default=None,
            )

    collection.update_one({"_id": group["_id"]}, {"$set": updates})
    return collection.find_one({"_id": group["_id"]})


def track_and_deduplicate(db, source: str, listings: list[dict]) -> list[dict]:
    """Persist listing observations and collapse only high-confidence matches."""
    collection = db.job_duplicate_groups
    observed_at = datetime.now(timezone.utc)
    results: list[dict] = []
    included_groups: dict[str, int] = {}

    for raw in listings:
        job = {**raw, "source": source}
        source_job_id = str(job.get("source_job_id") or "")
        identity = _source_identity(source, source_job_id)
        group = collection.find_one({"source_identities": identity})
        if group:
            references = list(group.get("source_listings") or [])
            reference = next(
                (
                    item for item in references
                    if item.get("source") == source
                    and item.get("source_job_id") == source_job_id
                ),
                None,
            )
            previous_date = _parse_date(
                (reference or {}).get("repost_dates", [])[-1]
                if (reference or {}).get("repost_dates")
                else (reference or {}).get("published_at")
            )
            current_date = _parse_date(job.get("published_at"))
            day_gap = (
                abs((current_date - previous_date).total_seconds()) / 86400
                if current_date and previous_date
                else None
            )
            confidence, _ = compare_listings(job, group)
            is_repost = (
                confidence == "high"
                and group.get("duplicate_status") != "potential_duplicate"
                and day_gap is not None
                and 0 < day_gap <= _MAX_REPOST_DAYS
            )
            if is_repost and reference is not None:
                repost_dates = list(reference.get("repost_dates") or [])
                repost_dates.append(current_date)
                reference["repost_dates"] = repost_dates
                latest_date = max(
                    date
                    for date in (current_date, _parse_date(group.get("latest_repost_at")))
                    if date
                )
                collection.update_one(
                    {"_id": group["_id"]},
                    {"$set": {
                        "source_listings": references,
                        "latest_repost_at": latest_date,
                        "repost_count": int(group.get("repost_count", 0)) + 1,
                        "duplicate_status": "reposted",
                        "duplicate_confidence": "high",
                        "updated_at": observed_at,
                    }},
                )
                group = collection.find_one({"_id": group["_id"]})
                status = "reposted"
                confidence = "high"
            else:
                collection.update_one(
                    {"_id": group["_id"]},
                    {"$set": {"updated_at": observed_at}},
                )
                status = "known"
                confidence = None
        else:
            company_root = _company_root(job.get("company"))
            candidates = list(
                collection.find({"company_root": company_root})
                .sort("updated_at", -1)
                .limit(250)
            ) if company_root else []
            comparisons = [
                (compare_listings(job, candidate), candidate)
                for candidate in candidates
            ]
            high_matches = [
                (score, candidate)
                for (level, score), candidate in comparisons
                if level == "high"
                and candidate.get("duplicate_status") != "potential_duplicate"
            ]
            medium_matches = [
                (score, candidate)
                for (level, score), candidate in comparisons
                if level == "medium"
            ]
            if high_matches:
                _, group = max(high_matches, key=lambda result: result[0])
                same_board = source in (group.get("available_sources") or [])
                status = "reposted" if same_board else "cross_board_duplicate"
                group = _append_source(collection, group, job, status)
                confidence = "high"
            elif medium_matches:
                _, possible_match = max(medium_matches, key=lambda result: result[0])
                group = _record_group(
                    collection,
                    job,
                    "potential_duplicate",
                    str(possible_match["_id"]),
                )
                status = "potential_duplicate"
                confidence = "medium"
            else:
                group = _record_group(collection, job, "new")
                status = "new"
                confidence = None

        group_id = str(group["_id"])
        tracked = {
            **job,
            "duplicate_group_id": group_id,
            "duplicate_status": status,
            "duplicate_confidence": confidence,
            "available_sources": group.get("available_sources") or [source],
            "source_listings": group.get("source_listings") or [_source_reference(job)],
            "original_published_at": group.get("original_published_at"),
            "latest_repost_at": group.get("latest_repost_at"),
            "repost_count": group.get("repost_count", 0),
            "potential_duplicate_of": group.get("potential_duplicate_of"),
        }
        if status in {"known", "cross_board_duplicate", "reposted"}:
            for field in (
                "title",
                "company",
                "location",
                "description",
                "employment_types",
                "skills",
                "nice_to_have_skills",
                "company_logo_url",
                "work_arrangement",
                "experience_level",
                "salary_min",
                "salary_max",
                "salary_currency",
                "salary_period",
            ):
                if group.get(field) not in (None, "", []):
                    tracked[field] = group[field]
        original_date = _parse_date(tracked.get("original_published_at"))
        if original_date:
            tracked["published_at"] = original_date

        if status in {"known", "cross_board_duplicate", "reposted"} and group_id in included_groups:
            current = results[included_groups[group_id]]
            current["available_sources"] = tracked["available_sources"]
            current["source_listings"] = tracked["source_listings"]
            if status in {"cross_board_duplicate", "reposted"}:
                current["duplicate_status"] = (
                    "cross_board_duplicate"
                    if len(tracked["available_sources"]) > 1
                    else "reposted"
                )
                current["duplicate_confidence"] = "high"
            current["latest_repost_at"] = tracked["latest_repost_at"]
            current["repost_count"] = tracked["repost_count"]
            continue

        if status in {"new", "known", "cross_board_duplicate", "reposted"}:
            included_groups.setdefault(group_id, len(results))
        results.append(tracked)

    return results

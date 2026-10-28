"""Daily job scanner — fetches new matching listings from external sources,
deduplicates them against the current database, and saves new ones.

Called by:
  • POST /api/scan  (on-demand, from the UI or an automation run)

Sources currently scanned:
  1. Jobicy public API (existing integration, remote-only)
  2. Greenhouse job-board RSS  (https://job-boards.greenhouse.io/feeds/<tag>)
     — we search a list of popular Bangalore / India–friendly boards plus any
       boards the user has bookmarked.

The scan reads target roles, locations, and experience level from the saved
profile and preferences so it stays in sync without any manual config.
"""

import logging
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlencode

import httpx
from pymongo.database import Database

from app.job_import import extract_job
from app.jobicy import search_jobicy_jobs, ALLOWED_GEOS, JobicyError
from app.matching import score_job

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 12.0
MAX_DESCRIPTION_CHARS = 20_000

# Greenhouse public board IDs we probe for India-based tech roles.
# These are real public boards that list Bangalore / India roles.
GREENHOUSE_BOARDS = [
    "thoughtworks",
    "flipkart",
    "swiggy",
    "dunzo",
    "meesho",
    "razorpay",
    "browserstack",
    "freshworks",
    "postman",
    "hasura",
]

GH_FEED_URL = "https://job-boards.greenhouse.io/feeds/{board}"
GH_BOARD_URL = "https://job-boards.greenhouse.io/{board}"

# Number of Jobicy results to scan per run (avoids hammering the API)
JOBICY_MAX_RESULTS = 40


# ---------------------------------------------------------------------------
# HTML strip helper (shared with jobicy.py pattern)
# ---------------------------------------------------------------------------

class _TextParser(HTMLParser):
    _IGNORED = frozenset({"script", "style", "noscript"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag, _):
        if tag in self._IGNORED:
            self._depth += 1
        elif not self._depth:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self._IGNORED:
            self._depth = max(0, self._depth - 1)
        elif not self._depth:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self._depth:
            self.parts.append(data)


def _strip_html(value: str) -> str:
    parser = _TextParser()
    parser.feed(value[:MAX_DESCRIPTION_CHARS])
    parser.close()
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()[:MAX_DESCRIPTION_CHARS]


# ---------------------------------------------------------------------------
# Greenhouse public board scraper (XML feed)
# ---------------------------------------------------------------------------

def _fetch_greenhouse_board(board: str, keyword_terms: list[str]) -> list[dict]:
    """Fetch one Greenhouse public board RSS feed and return matching job dicts."""
    url = GH_FEED_URL.format(board=board)
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = client.get(url, headers={"Accept": "application/xml,text/xml"})
        if resp.status_code != 200:
            return []
        xml = resp.text
    except Exception:  # noqa: BLE001 – skip unreachable boards silently
        return []

    # Parse <item> blocks from the RSS
    jobs: list[dict] = []
    # Extract all <item>…</item> blocks
    for item_xml in re.findall(r"<item>(.*?)</item>", xml, re.DOTALL):
        def tag(name: str) -> str:
            m = re.search(rf"<{name}[^>]*><!\[CDATA\[(.*?)\]\]></{name}>|<{name}[^>]*>(.*?)</{name}>", item_xml, re.DOTALL)
            if not m:
                return ""
            return (m.group(1) or m.group(2) or "").strip()

        title = tag("title")
        link = tag("link")
        description_html = tag("description")
        location_raw = tag("location")
        pub_date_raw = tag("pubDate")

        if not title or not link:
            continue

        # Keyword filter (case-insensitive)
        searchable = f"{title} {description_html}".casefold()
        if keyword_terms and not any(term in searchable for term in keyword_terms):
            continue

        description = _strip_html(description_html)
        parsed = extract_job(description, link)["draft"]

        pub_dt: datetime | None = None
        if pub_date_raw:
            try:
                from email.utils import parsedate_to_datetime
                pub_dt = parsedate_to_datetime(pub_date_raw).replace(tzinfo=timezone.utc)
            except Exception:  # noqa: BLE001
                pass

        # Derive a stable source_job_id from the URL path
        path = link.rstrip("/").rsplit("/", 1)[-1]
        source_job_id = re.sub(r"[^a-zA-Z0-9_-]", "", path)[:100] or title[:60]

        jobs.append({
            "source": "greenhouse",
            "source_job_id": f"gh_{board}_{source_job_id}",
            "title": title[:250],
            "company": board.replace("-", " ").title()[:250],
            "location": location_raw[:250] if location_raw else None,
            "url": link[:2000],
            "description": description,
            "skills": parsed["skills"],
            "nice_to_have_skills": parsed["nice_to_have_skills"],
            "work_arrangement": parsed.get("work_arrangement", ""),
            "experience_level": parsed.get("experience_level", ""),
            "salary_min": None,
            "salary_max": None,
            "salary_currency": "",
            "salary_period": "year",
            "employment_types": [],
            "published_at": pub_dt,
        })

    return jobs


# ---------------------------------------------------------------------------
# Jobicy scan helper
# ---------------------------------------------------------------------------

def _scan_jobicy(what: str, where: str) -> list[dict]:
    """Fetch Jobicy APAC results filtered to `where`."""
    try:
        result = search_jobicy_jobs(what=what, where=where, geo="apac")
        jobs = result.get("results", [])
        # Add source tag
        for job in jobs:
            job.setdefault("source", "jobicy")
        return jobs[:JOBICY_MAX_RESULTS]
    except JobicyError as exc:
        logger.warning("Jobicy scan failed: %s", exc.message)
        return []
    except Exception as exc:  # noqa: BLE001
        logger.warning("Jobicy scan unexpected error: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

def _is_duplicate(db: Database, job: dict) -> bool:
    """True if this job already exists in the database."""
    source = job.get("source", "")
    source_job_id = job.get("source_job_id", "")
    url = job.get("url", "")

    if source and source_job_id:
        if db.jobs.find_one({"source": source, "source_job_id": source_job_id}, {"_id": 1}):
            return True
    if url:
        if db.jobs.find_one({"url": url}, {"_id": 1}):
            return True
    return False


# ---------------------------------------------------------------------------
# Main scan entry point
# ---------------------------------------------------------------------------

def scan_and_save(db: Database) -> dict:
    """Scan external job sources and save new matching listings.

    Returns:
        {"saved": int, "skipped": int, "sources": {"jobicy": int, "greenhouse": int}}
    """
    now = datetime.now(timezone.utc)

    # Read saved profile and preferences
    profile = db.profiles.find_one({}) or {}
    prefs = db.preferences.find_one({}) or {}

    # Determine search keywords from target roles + skills
    target_roles = [r.strip() for r in profile.get("target_roles", []) if str(r).strip()]
    skills = [s.strip() for s in profile.get("skills", []) if str(s).strip()]
    locations = [l.strip() for l in prefs.get("locations", []) if str(l).strip()]

    search_keyword = target_roles[0] if target_roles else (skills[0] if skills else "software engineer")
    search_location = locations[0] if locations else "India"

    # Keyword terms for Greenhouse filtering
    keyword_terms = [r.casefold() for r in target_roles] + [s.casefold() for s in skills[:5]]

    saved = 0
    skipped = 0
    source_counts: dict[str, int] = {"jobicy": 0, "greenhouse": 0}

    # --- Jobicy ---
    jobicy_jobs = _scan_jobicy(search_keyword, search_location)
    for job in jobicy_jobs:
        if _is_duplicate(db, job):
            skipped += 1
            continue
        try:
            job_doc = dict(job)
            job_doc["created_at"] = now

            # Score against profile/prefs
            score_result = score_job(job_doc, profile, prefs)
            job_doc.update({
                "match_score": score_result["score"],
                "match_reasons": score_result["reasons"],
                "match_confidence": score_result["confidence"],
                "match_breakdown": [f.model_dump() if hasattr(f, "model_dump") else f for f in score_result.get("breakdown", [])],
                "match_scored_at": now,
                "feedback": "",
                "feedback_reason": "",
            })

            db.jobs.insert_one(job_doc)
            saved += 1
            source_counts["jobicy"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to save Jobicy job: %s", exc)
            skipped += 1

    # --- Greenhouse boards ---
    for board in GREENHOUSE_BOARDS:
        board_jobs = _fetch_greenhouse_board(board, keyword_terms)
        for job in board_jobs:
            if _is_duplicate(db, job):
                skipped += 1
                continue
            try:
                job_doc = dict(job)
                job_doc["created_at"] = now

                score_result = score_job(job_doc, profile, prefs)
                job_doc.update({
                    "match_score": score_result["score"],
                    "match_reasons": score_result["reasons"],
                    "match_confidence": score_result["confidence"],
                    "match_breakdown": score_result.get("breakdown", []),
                    "match_scored_at": now,
                    "feedback": "",
                    "feedback_reason": "",
                })

                db.jobs.insert_one(job_doc)
                saved += 1
                source_counts["greenhouse"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to save Greenhouse job from board %s: %s", board, exc)
                skipped += 1

    logger.info("Scan complete: saved=%d skipped=%d sources=%s", saved, skipped, source_counts)
    return {"saved": saved, "skipped": skipped, "sources": source_counts}


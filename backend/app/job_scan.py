"""Scan supported job boards for new matching listings."""

import logging
from datetime import datetime, timezone

from pymongo.database import Database

from app.jobspy_search import JobSpyError, SUPPORTED_SITES, search_jobspy_jobs
from app.matching import score_job

logger = logging.getLogger(__name__)


def _is_duplicate(db: Database, job: dict) -> bool:
    """True if this job already exists in the database."""
    source = job.get("source", "")
    source_job_id = job.get("source_job_id", "")
    url = job.get("url", "")

    if source and source_job_id:
        if db.jobs.find_one({"source": source, "source_job_id": source_job_id}, {"_id": 1}):
            return True
    if url and db.jobs.find_one({"url": url}, {"_id": 1}):
        return True
    return False


def scan_and_save(db: Database) -> dict:
    """Search the three supported boards and save new matching listings."""
    now = datetime.now(timezone.utc)
    profile = db.profiles.find_one({}) or {}
    prefs = db.preferences.find_one({}) or {}

    target_roles = [r.strip() for r in profile.get("target_roles", []) if str(r).strip()]
    skills = [s.strip() for s in profile.get("skills", []) if str(s).strip()]
    locations = [l.strip() for l in prefs.get("locations", []) if str(l).strip()]
    search_keyword = target_roles[0] if target_roles else (skills[0] if skills else "software engineer")
    search_location = locations[0] if locations else "Belgium"

    saved = 0
    skipped = 0
    source_counts = {site: 0 for site in SUPPORTED_SITES}

    for site in SUPPORTED_SITES:
        try:
            results = search_jobspy_jobs(
                site=site,
                what=search_keyword,
                where=search_location,
            )["results"]
        except JobSpyError as exc:
            logger.warning("%s scan failed: %s", site, exc.message)
            continue

        for job in results:
            job_doc = {**job, "source": site}
            if _is_duplicate(db, job_doc):
                skipped += 1
                continue
            try:
                score_result = score_job(job_doc, profile, prefs)
                job_doc.update({
                    "created_at": now,
                    "match_score": score_result["score"],
                    "match_reasons": score_result["reasons"],
                    "match_confidence": score_result["confidence"],
                    "match_breakdown": [
                        factor.model_dump() if hasattr(factor, "model_dump") else factor
                        for factor in score_result.get("breakdown", [])
                    ],
                    "match_scored_at": now,
                    "feedback": "",
                    "feedback_reason": "",
                })
                db.jobs.insert_one(job_doc)
                saved += 1
                source_counts[site] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to save %s job: %s", site, exc)
                skipped += 1

    logger.info("Scan complete: saved=%d skipped=%d sources=%s", saved, skipped, source_counts)
    return {"saved": saved, "skipped": skipped, "sources": source_counts}

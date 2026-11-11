"""Application Assistant API: kit, drafts, browser assistance, review, submitted.

Everything is stored on the existing application document under `assistant`
(one subdocument), keyed by the existing job/application ids. No new
collection, no second job model, no second AI integration (drafts go through
main._post_with_retry_and_fallback, the same OpenRouter helper the rest of the
app uses).

main.py is imported lazily inside functions to avoid a circular import.
"""

import asyncio
import hashlib
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from pymongo.database import Database

from app import assistant_kit as kit, schemas
from app import assistant_test_form as test_form
from app.approval import (
    ApprovalError,
    approve_reviewed,
    record_submitted,
    withdraw_approval,
)
from app.assistant_fill import run_assistance
from app.assistant_urls import (
    UrlRejected,
    check_automate_url,
    check_fill_url,
    check_job_listing_url,
    is_blocked_host,
)
from app.config import settings
from app.database import get_db
from app.resumes import MAX_RESUME_BYTES, safe_filename
from app.workflow import allowed_next, gated_next

logger = logging.getLogger(__name__)
router = APIRouter()
STEPS = ("job", "kit", "fill", "review", "done")
ACTIVE_RUNS: dict[str, tuple[str, threading.Event]] = {}
ACTIVE_RUNS_LOCK = threading.Lock()
MAX_ACTIVE_RUNS = 3


# ---------------- diagnostic artifact helpers ----------------

def _artifact_base_dir() -> Path:
    """Resolve the root directory for all diagnostic artifacts.

    Defaults to  <backend_package_parent>/diagnostics  so it lives outside
    the app package itself.  Override with the ASSISTANT_TRACE_DIR env var /
    settings key for production deployments.
    """
    configured = (settings.assistant_trace_dir or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    # backend/app/__file__ -> backend/app -> backend -> diagnostics sibling
    return (Path(__file__).resolve().parents[1] / "diagnostics").resolve()


def _owner_component(owner_uid: str) -> str:
    if not isinstance(owner_uid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", owner_uid):
        raise ValueError("Invalid artifact owner.")
    return owner_uid


def _is_link(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _secure_artifact_directory(path: Path) -> None:
    if os.name == "nt":
        executable = shutil.which("icacls")
        username = os.environ.get("USERNAME")
        domain = os.environ.get("USERDOMAIN")
        if not executable or not username:
            raise OSError("Could not establish a private artifact directory.")
        account = f"{domain}\\{username}" if domain else username
        completed = subprocess.run(
            [
                executable,
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"{account}:(OI)(CI)F",
            ],
            capture_output=True,
            check=False,
            timeout=10,
        )
        if completed.returncode:
            raise OSError("Could not establish a private artifact directory.")
        return
    path.chmod(0o700)


def _artifact_file_for_run(owner_uid: str, run_id: str, filename: str) -> Path:
    if not re.fullmatch(r"[a-fA-F0-9]{32}", run_id or ""):
        raise ValueError("Invalid artifact run identifier.")
    if filename not in ("trace.zip", "screenshot.png"):
        raise ValueError("Invalid artifact filename.")
    base = _artifact_base_dir().resolve()
    user_dir = base / _owner_component(owner_uid)
    run_dir = user_dir / run_id
    if _is_link(run_dir) or _is_link(user_dir):
        raise ValueError("Artifact path contains a symbolic link.")
    resolved_dir = run_dir.resolve()
    resolved_dir.relative_to(user_dir.resolve())
    artifact = resolved_dir / filename
    if _is_link(artifact):
        raise ValueError("Artifact path contains a symbolic link.")
    artifact.resolve().relative_to(resolved_dir)
    return artifact


def _artifact_is_available(db: Database, run: dict, run_id: str, filename: str) -> bool:
    owner_uid = getattr(db, "_user_id", None)
    path_key = "trace_path" if filename == "trace.zip" else "screenshot_path"
    stored_path = run.get(path_key)
    if not isinstance(owner_uid, str) or not stored_path:
        return False
    try:
        expected = _artifact_file_for_run(owner_uid, run_id, filename)
        return Path(stored_path).resolve() == expected.resolve() and expected.is_file()
    except (OSError, ValueError):
        return False


def _download_artifact_path(
    db: Database,
    run: dict,
    run_id: str,
    filename: str,
    label: str,
) -> Path:
    owner_uid = getattr(db, "_user_id", None)
    path_key = "trace_path" if filename == "trace.zip" else "screenshot_path"
    stored_path = run.get(path_key)
    if not stored_path:
        raise HTTPException(status_code=404, detail=f"No {label} was recorded for this run.")
    try:
        if not isinstance(owner_uid, str):
            raise ValueError("Missing authenticated owner.")
        expected = _artifact_file_for_run(owner_uid, run_id, filename)
        stored = Path(stored_path).resolve()
        stored.relative_to(_artifact_base_dir().resolve())
        if stored != expected.resolve():
            raise ValueError("Artifact does not match the authenticated run.")
    except (OSError, ValueError):
        logger.warning("Diagnostic artifact path rejected kind=%s", label)
        raise HTTPException(status_code=403, detail=f"{label.capitalize()} file location is invalid.") from None
    if not expected.is_file():
        raise HTTPException(status_code=404, detail=f"{label.capitalize()} file not found.")
    return expected


def _artifact_dir_for_run(owner_uid: str, run_id: str) -> Path:
    """Return the per-run artifact directory (not yet created)."""
    if not re.fullmatch(r"[a-fA-F0-9]{32}", run_id or ""):
        raise ValueError("Invalid artifact run identifier.")
    return _artifact_base_dir() / _owner_component(owner_uid) / run_id


def _make_artifact_dir(owner_uid: str, run_id: str) -> str | None:
    """Create the artifact directory for a run and return its path.

    Returns None if the directory cannot be created (non-fatal).
    """
    if not isinstance(owner_uid, str) or not owner_uid:
        return None
    try:
        base = _artifact_base_dir()
        base.mkdir(parents=True, exist_ok=True)
        _secure_artifact_directory(base)
        d = _artifact_dir_for_run(owner_uid, run_id)
        d.parent.mkdir(parents=True, exist_ok=True)
        if _is_link(d.parent):
            raise ValueError("Artifact owner directory cannot be a symbolic link.")
        d.parent.resolve().relative_to(base.resolve())
        _secure_artifact_directory(d.parent)
        d.mkdir(parents=True, exist_ok=True)
        if _is_link(d):
            raise ValueError("Artifact run directory cannot be a symbolic link.")
        d.resolve().relative_to(d.parent.resolve())
        _secure_artifact_directory(d)
        return str(d)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not create private artifact directory category=%s", type(exc).__name__)
        return None


def _cleanup_old_artifacts(owner_uid: str) -> None:
    """Delete artifact directories older than the configured retention period.

    Only the calling user's subdirectory is ever touched.  This is called
    asynchronously before starting a new run; errors are logged and ignored.
    """
    retention = settings.assistant_artifact_retention_days
    if retention <= 0:
        return
    try:
        user_dir = _artifact_base_dir() / _owner_component(owner_uid)
        if _is_link(user_dir):
            raise ValueError("Artifact owner directory cannot be a symbolic link.")
        user_dir.resolve().relative_to(_artifact_base_dir().resolve())
        if not user_dir.is_dir():
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention)
        for run_dir in user_dir.iterdir():
            if _is_link(run_dir) or not run_dir.is_dir():
                continue
            try:
                mtime = datetime.fromtimestamp(run_dir.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    run_dir.resolve().relative_to(user_dir.resolve())
                    shutil.rmtree(run_dir)
                    logger.debug("Removed expired diagnostic artifacts.")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Artifact cleanup failed category=%s", type(exc).__name__)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Artifact cleanup error: %s", exc)


# ---------------- small helpers ----------------

def _main():
    from app import main  # lazy: main includes this router
    return main


def _load(db: Database, application_id: str) -> tuple[dict, dict]:
    m = _main()
    app_doc = db.applications.find_one({"_id": m.to_oid(application_id, "Application")})
    if not app_doc:
        raise HTTPException(status_code=404, detail="Application not found.")
    job = db.jobs.find_one({"_id": app_doc["job_id"]})
    if not job:
        raise HTTPException(status_code=404, detail="The job for this application was deleted.")
    return app_doc, job


def _stored(app_doc: dict) -> dict:
    return app_doc.get("assistant") or {}


def _candidate_resume_query(app_doc: dict) -> dict:
    candidate_id = app_doc.get("candidate_id")
    return {"candidate_id": candidate_id} if candidate_id else {"is_active": True}


def _default_resume(db: Database, app_doc: dict, projection=None):
    return db.resumes.find_one(
        _candidate_resume_query(app_doc),
        projection,
        sort=[("uploaded_at", -1)],
    )


def _effective(db: Database, app_doc: dict) -> tuple[dict, dict, dict]:
    if app_doc.get("candidate_id"):
        profile = _main()._candidate_matching_data(
            str(app_doc["candidate_id"]), db, include_archived=True
        )[1]
    else:
        profile = db.profiles.find_one({})
    stored = _stored(app_doc)
    facts = kit.effective_facts(profile, stored.get("kit"))
    answers = kit.effective_answers(stored.get("kit"))
    drafts = {k: {"text": d.get("text", ""), "source": d.get("source", "missing")}
              for k, d in (stored.get("drafts") or {}).items() if k in kit.DRAFT_LABELS}
    return facts, answers, drafts


def _rows_and_hash(db: Database, app_doc: dict, job: dict):
    facts, answers, drafts = _effective(db, app_doc)
    rows = kit.review_rows(facts, answers, drafts)
    assistant = _stored(app_doc)
    selected_resume = app_doc.get("resume_id")
    if selected_resume is None and not assistant.get("resume_selection_cleared"):
        active = _default_resume(db, app_doc, {"_id": 1})
        selected_resume = active.get("_id") if active else None
    resume_fingerprint = ""
    resume_doc = db.resumes.find_one({"_id": selected_resume}) if selected_resume else None
    if resume_doc:
        resume_data = resume_doc.get("data")
        resume_bytes = (
            bytes(resume_data)
            if isinstance(resume_data, (bytes, bytearray, memoryview))
            else b""
        )
        resume_fingerprint = hashlib.sha256(
            str(resume_doc.get("filename", "")).encode("utf-8")
            + b"\0"
            + resume_bytes
        ).hexdigest()
    materials = assistant.get("materials") or {}
    digest_materials = {
        key: materials.get(key)
        for key in (
            "cover_letter",
            "resume_summary",
            "skills_to_prioritize",
            "bullet_suggestions",
            "caveats",
        )
    }
    digest_materials["application_url"] = assistant.get("application_url", "")
    return rows, kit.review_hash(
        job,
        rows,
        materials=digest_materials,
        resume_id=str(selected_resume) if selected_resume else None,
        resume_fingerprint=resume_fingerprint,
    )


def _http_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail="Application URL must be a valid http:// or https:// address.") from None
    if (
        parts.scheme.lower() not in ("http", "https")
        or not parts.hostname
        or parts.username
        or parts.password
        or len(raw) > 2000
        or any(ord(char) < 32 for char in raw)
        or " " in raw
    ):
        raise HTTPException(status_code=422, detail="Application URL must be a valid http:// or https:// address.")
    return raw


def _state(db: Database, app_doc: dict, job: dict, generation: dict | None = None) -> dict:
    stored = _stored(app_doc)
    rows, digest = _rows_and_hash(db, app_doc, job)
    review = stored.get("review") or {}
    confirmed = bool(review.get("hash")) and review["hash"] == digest
    drafts_meta = {}
    for key, label in kit.DRAFT_LABELS.items():
        d = (stored.get("drafts") or {}).get(key) or {}
        drafts_meta[key] = {
            "key": key, "label": label, "text": d.get("text", ""),
            "source": d.get("source", "missing") if d.get("text") else "missing",
            "can_restore": bool(d) and d.get("source") == "user_edited" and "original_text" in d,
            "needs_input": bool(d.get("needs_input")) or not d.get("text"),
            "reason": d.get("reason", ""),
        }
    available_resumes = []
    for candidate in db.resumes.find(_candidate_resume_query(app_doc), {"data": 0}):
        available_resumes.append(
            {
                "id": str(candidate["_id"]),
                "label": candidate.get("label", ""),
                "filename": candidate.get("filename", ""),
                "is_active": bool(candidate.get("is_active")),
            }
        )
    resume = None
    rid = app_doc.get("resume_id")
    if rid:
        doc = db.resumes.find_one({"_id": rid}, {"data": 0})
    elif stored.get("resume_selection_cleared"):
        doc = None
    else:
        doc = _default_resume(db, app_doc, {"data": 0})
    if doc:
        resume = {"id": str(doc["_id"]), "label": doc.get("label", ""), "filename": doc.get("filename", ""),
                  "is_active": bool(doc.get("is_active"))}
    job_url = job.get("url") or ""
    linkedin_job = False
    for u in (job_url, stored.get("application_url", "")):
        host = (urlsplit(u).hostname or "") if u else ""
        if host and is_blocked_host(host):
            linkedin_job = True
    done = {
        "job": True,
        "kit": "kit" in stored.get("visited", []),
        "fill": any(r.get("status") in ("prepared", "needs_you") for r in stored.get("runs", [])),
        "review": confirmed,
        "done": app_doc["status"] == "submitted",
    }
    return {
        "application_id": str(app_doc["_id"]),
        "job": {
            "id": str(job["_id"]), "title": job["title"], "company": job["company"],
            "location": job.get("location"), "url": job_url, "source": job.get("source", "manual"),
            "match_score": job.get("match_score", 0), "match_confidence": job.get("match_confidence", ""),
            "match_reasons": job.get("match_reasons", []),
        },
        "status": app_doc["status"],
        "allowed_next": allowed_next(app_doc["status"]),
        "gated_next": gated_next(app_doc["status"]),
        "status_history": app_doc.get("status_history", []),
        "resume": resume,
        "available_resumes": available_resumes,
        "materials": stored.get("materials"),
        "application_url": (
            stored.get("application_url")
            if stored.get("application_url") is not None
            else job.get("application_url")
        ) or "",
        "linkedin_job": linkedin_job,
        "manual_checklist": [
            "Open the LinkedIn posting yourself in your own browser.",
            "Copy each answer from the Application Kit into the form: contact details, then the drafted answers.",
            "Attach your resume yourself and leave anything marked 'Needs your input' for you to decide.",
            "Read every answer once more before you submit. CareerPilot never submits for you.",
            "When you have submitted it on the site, come back and click 'I submitted it'.",
        ] if linkedin_job else [],
        "kit": {"rows": rows, "drafts": drafts_meta},
        "review": {
            "hash": digest, "confirmed": confirmed, "confirmed_at": review.get("confirmed_at") if confirmed else None,
            "stale": bool(review.get("hash")) and not confirmed,
        },
        "steps": {"current": stored.get("current_step", "job"), "done": done, "order": list(STEPS)},
        "runs": [
            {
                **{k: r.get(k) for k in ("id", "host", "adapter", "status", "pause_reason",
                                         "fields", "started_at", "finished_at", "blocked_writes",
                                         "browser_open", "submission_enabled", "error_category")},
                # Surface artifact availability without exposing filesystem paths.
                "has_trace": _artifact_is_available(db, r, r.get("id", ""), "trace.zip"),
                "has_screenshot": _artifact_is_available(db, r, r.get("id", ""), "screenshot.png"),
            }
            for r in (stored.get("runs") or [])[-5:]
        ],
        "submitted_at": stored.get("submitted_at"),
        "generation": generation,
    }


def _refresh_review(db: Database, application_id) -> None:
    """After any change to the answers: a review of older content no longer counts."""
    app_doc = db.applications.find_one({"_id": application_id})
    job = db.jobs.find_one({"_id": app_doc["job_id"]})
    review = _stored(app_doc).get("review")
    if not review:
        return
    _, digest = _rows_and_hash(db, app_doc, job)
    if review.get("hash") == digest:
        return
    with ACTIVE_RUNS_LOCK:
        active_run_ids = []
        for run_id, (active_application_id, authorization) in ACTIVE_RUNS.items():
            if active_application_id == str(application_id):
                authorization.clear()
                active_run_ids.append(run_id)
    for run_id in active_run_ids:
        db.applications.update_one(
            {"_id": application_id, "assistant.runs.id": run_id},
            {"$set": {"assistant.runs.$.submission_enabled": False}},
        )
    try:
        withdraw_approval(db, app_doc, _main().now())
    except ApprovalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    db.applications.update_one({"_id": application_id}, {"$unset": {"assistant.review": ""}})


def _touch(db: Database, application_id, extra: dict | None = None) -> None:
    result = db.applications.update_one(
        {
            "_id": application_id,
            "status": {"$nin": ["submitted", "interview", "offer", "rejected", "withdrawn"]},
        },
        {
            "$set": {**(extra or {}), "updated_at": _main().now()},
            "$inc": {"assistant.revision": 1},
        },
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=409, detail="This application is no longer being prepared.")


# ---------------- schemas ----------------

class KitUpdate(BaseModel):
    facts: dict[str, str] = Field(default_factory=dict)
    answers: dict[str, str] = Field(default_factory=dict)
    drafts: dict[str, str] = Field(default_factory=dict)
    application_url: str | None = Field(default=None, max_length=2000)


class GenerateRequest(BaseModel):
    keys: list[str] = Field(default_factory=list)
    overwrite_edited: bool = False


class StepRequest(BaseModel):
    step: str


class FillRequest(BaseModel):
    url: str = Field(max_length=2000)
    upload_resume: bool = False


class AutomateRequest(BaseModel):
    url: str | None = Field(default=None, max_length=2000)
    upload_resume: bool = True


class ResumeSelection(BaseModel):
    resume_id: str | None = None


class MaterialsUpdate(BaseModel):
    cover_letter: str = Field(max_length=8000)
    resume_summary: str = Field(max_length=2000)
    skills_to_prioritize: list[str] = Field(default_factory=list, max_length=50)
    bullet_suggestions: list[schemas.ResumeBulletSuggestion] = Field(
        default_factory=list, max_length=30
    )
    caveats: list[str] = Field(default_factory=list, max_length=20)


class ReviewConfirm(BaseModel):
    hash: str
    acknowledged: bool = False


class SubmittedRequest(BaseModel):
    confirm: bool = False


# ---------------- routes ----------------

@router.post("/api/jobs/{job_id}/assistant")
def open_assistant_for_job(job_id: str, db: Database = Depends(get_db)):
    """Open (and if needed create) the one application record for this saved job."""
    m = _main()
    job = db.jobs.find_one({"_id": m.to_oid(job_id, "Job")})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    existing = db.applications.find_one({"job_id": job["_id"]}, sort=[("updated_at", -1)])
    if existing is None:
        m.create_application(
            m.schemas.ApplicationCreate(job_id=job_id, status="saved"),
            Response(),
            db,
        )
        existing = db.applications.find_one({"job_id": job["_id"]}, sort=[("updated_at", -1)])
    stored = _stored(existing)
    if "application_url" not in stored and job.get("application_url"):
        db.applications.update_one(
            {"_id": existing["_id"]},
            {"$set": {"assistant.application_url": job["application_url"]}},
        )
        existing, job = _load(db, str(existing["_id"]))
    return _state(db, existing, job)


@router.get("/api/applications/{application_id}/assistant")
def get_assistant(application_id: str, db: Database = Depends(get_db)):
    app_doc, job = _load(db, application_id)
    return _state(db, app_doc, job)


@router.put("/api/applications/{application_id}/assistant/resume")
def select_resume(
    application_id: str,
    payload: ResumeSelection,
    db: Database = Depends(get_db),
):
    app_doc, _ = _load(db, application_id)
    if payload.resume_id:
        resume_id = _main().to_oid(payload.resume_id, "Resume")
        resume_query = {"_id": resume_id}
        if app_doc.get("candidate_id"):
            resume_query["candidate_id"] = app_doc["candidate_id"]
        if not db.resumes.find_one(resume_query, {"_id": 1}):
            raise HTTPException(
                status_code=404,
                detail="Resume not found for this candidate.",
            )
    else:
        resume_id = None
    result = db.applications.update_one(
        {
            "_id": app_doc["_id"],
            "status": {"$nin": ["submitted", "interview", "offer", "rejected", "withdrawn"]},
        },
        {
            "$set": {
                "resume_id": resume_id,
                "assistant.resume_selection_cleared": resume_id is None,
                "updated_at": _main().now(),
            },
            "$inc": {"assistant.revision": 1},
        },
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=409, detail="This application is no longer being prepared.")
    if resume_id is not None:
        db.applications.update_one(
            {"_id": app_doc["_id"]},
            {"$unset": {"assistant.resume_selection_cleared": ""}},
        )
    app_doc, job = _load(db, application_id)
    _refresh_review(db, app_doc["_id"])
    app_doc, job = _load(db, application_id)
    return _state(db, app_doc, job)


@router.put("/api/applications/{application_id}/assistant/kit")
def save_kit(application_id: str, payload: KitUpdate, db: Database = Depends(get_db)):
    app_doc, job = _load(db, application_id)
    now = _main().now()
    sets: dict = {}
    for key, value in payload.facts.items():
        if key not in kit.FACT_LABELS:
            raise HTTPException(status_code=422, detail=f"Unknown kit field '{key}'.")
        sets[f"assistant.kit.facts.{key}"] = {"value": value.strip()[:kit.MAX_VALUE], "updated_at": now}
    for key, value in payload.answers.items():
        if key not in kit.ANSWER_LABELS:
            raise HTTPException(status_code=422, detail=f"Unknown question '{key}'.")
        sets[f"assistant.kit.answers.{key}"] = {"value": value.strip()[:kit.MAX_VALUE], "updated_at": now}
    stored_drafts = _stored(app_doc).get("drafts") or {}
    for key, value in payload.drafts.items():
        if key not in kit.DRAFT_LABELS:
            raise HTTPException(status_code=422, detail=f"Unknown draft '{key}'.")
        prev = stored_drafts.get(key) or {}
        new = {**prev, "text": value.strip()[:2000], "edited_at": now}
        if prev.get("source") != "user_edited":
            new["original_text"] = prev.get("text", "")
            new["original_source"] = prev.get("source", "missing")
        new["source"] = "user_edited" if value.strip() else "missing"
        new["needs_input"] = not value.strip()
        sets[f"assistant.drafts.{key}"] = new
    if payload.application_url is not None:
        sets["assistant.application_url"] = _http_url(payload.application_url)
    sets["assistant.current_step"] = "kit"
    _touch(db, app_doc["_id"], sets)
    db.applications.update_one({"_id": app_doc["_id"]}, {"$addToSet": {"assistant.visited": "kit"}})
    _refresh_review(db, app_doc["_id"])
    app_doc, job = _load(db, application_id)
    return _state(db, app_doc, job)


@router.post("/api/applications/{application_id}/assistant/materials/generate")
async def generate_materials(application_id: str, db: Database = Depends(get_db)):
    app_doc, job = _load(db, application_id)
    if app_doc["status"] in ("submitted", "interview", "offer", "rejected", "withdrawn"):
        raise HTTPException(status_code=409, detail="This application is no longer being prepared.")
    generated = await _main().generate_job_application_materials(
        str(job["_id"]), db
    )
    material_data = generated.model_dump(
        include={
            "cover_letter",
            "resume_summary",
            "skills_to_prioritize",
            "bullet_suggestions",
            "caveats",
        }
    )
    _touch(db, app_doc["_id"], {"assistant.materials": material_data})
    _refresh_review(db, app_doc["_id"])
    app_doc, job = _load(db, application_id)
    return _state(db, app_doc, job)


@router.put("/api/applications/{application_id}/assistant/materials")
def save_materials(
    application_id: str,
    payload: MaterialsUpdate,
    db: Database = Depends(get_db),
):
    app_doc, job = _load(db, application_id)
    data = payload.model_dump()
    _touch(db, app_doc["_id"], {"assistant.materials": data})
    _refresh_review(db, app_doc["_id"])
    app_doc, job = _load(db, application_id)
    return _state(db, app_doc, job)


@router.post("/api/applications/{application_id}/assistant/drafts/{key}/restore")
def restore_draft(application_id: str, key: str, db: Database = Depends(get_db)):
    app_doc, job = _load(db, application_id)
    d = (_stored(app_doc).get("drafts") or {}).get(key)
    if key not in kit.DRAFT_LABELS or not d or "original_text" not in d:
        raise HTTPException(status_code=404, detail="Nothing to restore for this draft.")
    restored = {**d, "text": d["original_text"], "source": d.get("original_source") or "rule_based",
                "needs_input": not d["original_text"]}
    restored.pop("edited_at", None)
    _touch(db, app_doc["_id"], {f"assistant.drafts.{key}": restored})
    _refresh_review(db, app_doc["_id"])
    app_doc, job = _load(db, application_id)
    return _state(db, app_doc, job)


async def _ai_drafts(job: dict, profile: dict | None) -> dict[str, str]:
    """Ask the existing OpenRouter helper. Raises on any failure so the caller falls back."""
    m = _main()
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await m._post_with_retry_and_fallback(
            client,
            messages=[
                {"role": "system", "content": kit.AI_SYSTEM_PROMPT},
                {"role": "user", "content": kit.ai_user_prompt(job, profile)},
            ],
        )
    result = response.json()
    if response.is_error or (isinstance(result, dict) and result.get("error")):
        raise RuntimeError(f"provider_error_{response.status_code}")
    content = result["choices"][0]["message"]["content"]
    cleaned = kit.clean_ai_drafts(m._parse_model_json(content))
    if not cleaned:
        raise ValueError("empty_ai_output")
    return cleaned


@router.post("/api/applications/{application_id}/assistant/drafts/generate")
async def generate_drafts(
    application_id: str, payload: GenerateRequest | None = None, db: Database = Depends(get_db)
):
    payload = payload or GenerateRequest()
    app_doc, job = _load(db, application_id)
    if app_doc["status"] in ("submitted", "interview", "offer", "rejected", "withdrawn"):
        raise HTTPException(status_code=409, detail="This application is no longer being prepared.")
    if app_doc.get("candidate_id"):
        profile = _main()._candidate_matching_data(
            str(app_doc["candidate_id"]), db, include_archived=True
        )[1]
    else:
        profile = db.profiles.find_one({})
    if profile is None:
        raise HTTPException(status_code=409, detail="Save your profile before generating drafts.")
    unknown_keys = set(payload.keys) - set(kit.DRAFT_LABELS)
    if unknown_keys:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown draft key(s): {', '.join(sorted(unknown_keys))}.",
        )

    rule = kit.rule_based_drafts(job, profile)
    ai: dict[str, str] = {}
    used, fallback_reason = "rule_based", ""
    if not settings.openrouter_api_key:
        fallback_reason = "OpenRouter API key is not configured; used built-in templates."
    else:
        try:
            ai = await _ai_drafts(job, profile)
            used = "ai"
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError, IndexError, TypeError):
            logger.warning("Assistant draft generation fell back category=ai_unavailable")
            fallback_reason = "The AI service was unavailable; used built-in templates."

    now = _main().now()
    stored = _stored(app_doc).get("drafts") or {}
    wanted = payload.keys or list(kit.DRAFT_LABELS)
    sets, kept = {}, []
    for key in wanted:
        prev = stored.get(key) or {}
        if prev.get("source") == "user_edited" and not payload.overwrite_edited:
            kept.append(key)
            continue
        if key in ai:
            new = {"text": ai[key], "source": "ai", "needs_input": False, "reason": ""}
        else:
            new = dict(rule[key])
        new["generated_at"] = now
        sets[f"assistant.drafts.{key}"] = new
    if sets:
        _touch(db, app_doc["_id"], sets)
        _refresh_review(db, app_doc["_id"])
    app_doc, job = _load(db, application_id)
    return _state(db, app_doc, job, {"used": used, "fallback_reason": fallback_reason, "kept_edited": kept})


@router.put("/api/applications/{application_id}/assistant/step")
def save_step(application_id: str, payload: StepRequest, db: Database = Depends(get_db)):
    if payload.step not in STEPS:
        raise HTTPException(status_code=422, detail=f"step must be one of: {', '.join(STEPS)}")
    app_doc, job = _load(db, application_id)
    db.applications.update_one(
        {"_id": app_doc["_id"]},
        {"$set": {"assistant.current_step": payload.step}, "$addToSet": {"assistant.visited": payload.step}},
    )
    app_doc, job = _load(db, application_id)
    return _state(db, app_doc, job)


# ---------------- browser assistance ----------------

def _fill_values(db: Database, app_doc: dict) -> dict[str, dict]:
    facts, answers, drafts = _effective(db, app_doc)
    values = {k: dict(v) for k, v in {**facts, **answers}.items()}
    for key, d in drafts.items():
        if d["text"].strip():
            values[key] = {"value": d["text"], "source": d["source"]}
    return values


def _run_in_thread(
    db: Database,
    app_id,
    run_id: str,
    target,
    values: dict,
    submission_authorized: threading.Event,
    resume_file: tuple[bytes, str] | None,
    owner_uid: str | None,
) -> None:
    m = _main()

    def save(result: dict, finished: bool) -> None:
        sets = {
            "assistant.runs.$.status": result["status"],
            "assistant.runs.$.pause_reason": result.get("pause_reason", ""),
            "assistant.runs.$.error_category": result.get("error_category", ""),
            "assistant.runs.$.fields": result.get("fields", []),
            "assistant.runs.$.blocked_writes": result.get("blocked_writes", 0),
            "assistant.runs.$.browser_open": result.get("browser_open", False),
        }
        if result.get("submission_enabled"):
            sets["assistant.runs.$.submission_enabled"] = True
        if result.get("trace_path"):
            sets["assistant.runs.$.trace_path"] = result["trace_path"]
        if result.get("screenshot_path"):
            sets["assistant.runs.$.screenshot_path"] = result["screenshot_path"]
        if finished:
            sets["assistant.runs.$.finished_at"] = m.now()
        db.applications.update_one({"_id": app_id, "assistant.runs.id": run_id}, {"$set": sets})

    try:
        artifact_dir = _make_artifact_dir(owner_uid, run_id) if owner_uid else None
        if owner_uid:
            _cleanup_old_artifacts(owner_uid)
        final = run_assistance(
            target, values,
            headless=settings.assistant_browser_headless,
            keep_open_seconds=settings.assistant_keep_open_seconds,
            browser_cdp_endpoint=(
                settings.assistant_browser_cdp_endpoint
                if target.adapter.endswith("_listing")
                else None
            ),
            on_result=lambda r: save(r, finished=False),
            automation_profile_dir=(
                settings.assistant_browser_profile_dir
                or settings.assistant_glassdoor_profile_dir
                or None
            ),
            resume_file=resume_file,
            submission_authorized=submission_authorized,
            artifact_dir=artifact_dir,
        )
        save(final, finished=True)
    except Exception:  # noqa: BLE001 - never leave a run stuck on 'running'
        logger.warning("Assistant run crashed category=unexpected_error")
        save({
            "status": "failed",
            "pause_reason": "unexpected_error",
            "error_category": "unexpected_error",
            "fields": [],
        }, finished=True)
    except BaseException:  # noqa: BLE001 - persist cancellation before releasing the run
        logger.warning("Assistant run stopped category=run_cancelled")
        save({
            "status": "failed",
            "pause_reason": "run_cancelled",
            "error_category": "run_cancelled",
            "fields": [],
        }, finished=True)
    finally:
        with ACTIVE_RUNS_LOCK:
            ACTIVE_RUNS.pop(run_id, None)


def _start_browser_run(
    db: Database,
    app_doc: dict,
    target,
    values: dict,
    *,
    resume_file: tuple[bytes, str] | None = None,
    persist_application_url: bool = True,
) -> dict:
    run_id = uuid.uuid4().hex
    now = _main().now()
    submission_authorized = threading.Event()
    owner_uid = getattr(db, "_user_id", None)
    if not isinstance(owner_uid, str) or not owner_uid:
        owner_uid = None

    with ACTIVE_RUNS_LOCK:
        if len(ACTIVE_RUNS) >= MAX_ACTIVE_RUNS:
            raise HTTPException(status_code=429, detail="Too many browser runs are active. Wait for one to finish.")
        if any(active_application_id == str(app_doc["_id"]) for active_application_id, _ in ACTIVE_RUNS.values()):
            raise HTTPException(status_code=409, detail="A browser assistance run is already in progress.")
        ACTIVE_RUNS[run_id] = (str(app_doc["_id"]), submission_authorized)

    run = {
        "id": run_id,
        "host": target.host,
        "adapter": target.adapter,
        "status": "running",
        "pause_reason": "",
        "fields": [],
        "started_at": now,
        "finished_at": None,
        "blocked_writes": 0,
        "browser_open": False,
        "submission_enabled": False,
    }
    update = {
        "$push": {"assistant.runs": run},
        "$addToSet": {"assistant.visited": "fill"},
        "$set": {"assistant.current_step": "fill"},
    }
    if persist_application_url:
        update["$set"]["assistant.application_url"] = target.url
    try:
        db.applications.update_one({"_id": app_doc["_id"]}, update)
    except Exception:
        with ACTIVE_RUNS_LOCK:
            ACTIVE_RUNS.pop(run_id, None)
        raise

    threading.Thread(
        target=_run_in_thread,
        args=(
            db,
            app_doc["_id"],
            run_id,
            target,
            values,
            submission_authorized,
            resume_file,
            owner_uid,
        ),
        daemon=True,
    ).start()
    logger.info("Assistant run started host=%s adapter=%s", target.host, target.adapter)
    return {"run_id": run_id, "status": "running", "host": target.host}


@router.post("/api/applications/{application_id}/assistant/open-listing", status_code=202)
def open_saved_job_listing(application_id: str, db: Database = Depends(get_db)):
    """Open this application's saved job URL in the shared persistent browser."""
    if settings.vercel:
        raise HTTPException(
            status_code=503,
            detail="The CareerPilot persistent browser is available only from the local backend.",
        )
    app_doc, job = _load(db, application_id)
    if app_doc["status"] in ("submitted", "interview", "offer", "rejected", "withdrawn"):
        raise HTTPException(status_code=409, detail="This application is already past the preparation stage.")
    if any(r.get("status") == "running" for r in _stored(app_doc).get("runs", [])):
        raise HTTPException(status_code=409, detail="A browser assistance run is already in progress.")
    try:
        target = check_job_listing_url(job.get("url", ""), job.get("source", ""))
    except UrlRejected as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": exc.message},
        ) from None

    return _start_browser_run(
        db,
        app_doc,
        target,
        {},
        persist_application_url=False,
    )


@router.post("/api/applications/{application_id}/assistant/automate", status_code=202)
def automate_application(
    application_id: str,
    payload: AutomateRequest | None = None,
    db: Database = Depends(get_db),
):
    """Open the job or application URL in CareerPilot's persistent browser, handle login pause/resume, fill multi-step fields, and stop at final review."""
    if settings.vercel:
        raise HTTPException(
            status_code=503,
            detail="Browser automation is available only from the local backend.",
        )
    payload = payload or AutomateRequest()
    app_doc, job = _load(db, application_id)
    if app_doc["status"] in ("submitted", "interview", "offer", "rejected", "withdrawn"):
        raise HTTPException(
            status_code=409,
            detail="This application is already past the preparation stage.",
        )
    if any(r.get("status") == "running" for r in _stored(app_doc).get("runs", [])):
        raise HTTPException(
            status_code=409,
            detail="A browser assistance run is already in progress.",
        )

    allow_local_form = (
        settings.assistant_allow_local_test_form
        and settings.app_env.lower() in ("development", "test")
    )
    stored = _stored(app_doc)
    raw_url = (
        (payload.url or "").strip()
        or (stored.get("application_url") or "").strip()
        or (job.get("application_url") or "").strip()
        or (job.get("url") or "").strip()
    )
    try:
        target = check_automate_url(
            raw_url,
            source=job.get("source", "") if raw_url == (job.get("url") or "").strip() else "",
            allow_local_form=allow_local_form,
        )
    except UrlRejected as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": exc.message},
        ) from None

    resume_file = None
    if payload.upload_resume:
        resume_id = app_doc.get("resume_id")
        if resume_id is None and not stored.get("resume_selection_cleared"):
            active = _default_resume(db, app_doc, {"_id": 1})
            resume_id = active.get("_id") if active else None
        resume_doc = db.resumes.find_one({"_id": resume_id}) if resume_id else None
        if resume_doc:
            stored_data = resume_doc.get("data")
            data = (
                bytes(stored_data)
                if isinstance(stored_data, (bytes, bytearray, memoryview))
                else b""
            )
            if (
                data.startswith(b"%PDF-")
                and len(data) <= MAX_RESUME_BYTES
                and str(resume_doc.get("filename", "")).lower().endswith(".pdf")
            ):
                resume_file = (data, safe_filename(resume_doc["filename"]))

    return _start_browser_run(
        db,
        app_doc,
        target,
        _fill_values(db, app_doc),
        resume_file=resume_file,
        persist_application_url=bool(payload.url and payload.url.strip()),
    )


@router.post("/api/applications/{application_id}/assistant/fill", status_code=202)
def start_fill(application_id: str, payload: FillRequest, db: Database = Depends(get_db)):
    if settings.vercel:
        raise HTTPException(
            status_code=503,
            detail=(
                "Browser-assisted autofill is unavailable on Vercel serverless functions. "
                "Use the Application Kit to review and copy answers into the employer form."
            ),
        )
    app_doc, job = _load(db, application_id)
    allow_local_form = (
        settings.assistant_allow_local_test_form
        and settings.app_env.lower() in ("development", "test")
    )
    try:
        target = check_fill_url(payload.url, allow_local_form=allow_local_form)
    except UrlRejected as exc:
        code = 403 if exc.code == "linkedin_blocked" else 422
        raise HTTPException(status_code=code, detail={"code": exc.code, "message": exc.message}) from None

    if app_doc["status"] in ("submitted", "interview", "offer", "rejected", "withdrawn"):
        raise HTTPException(status_code=409, detail="This application is already past the preparation stage.")
    if any(r.get("status") == "running" for r in _stored(app_doc).get("runs", [])):
        raise HTTPException(status_code=409, detail="A browser assistance run is already in progress.")

    resume_file = None
    if payload.upload_resume:
        resume_id = app_doc.get("resume_id")
        if resume_id is None and not _stored(app_doc).get("resume_selection_cleared"):
            active = _default_resume(db, app_doc, {"_id": 1})
            resume_id = active.get("_id") if active else None
        resume_doc = db.resumes.find_one({"_id": resume_id}) if resume_id else None
        if not resume_doc:
            raise HTTPException(status_code=409, detail="Choose a saved PDF resume before attaching it.")
        stored_data = resume_doc.get("data")
        data = bytes(stored_data) if isinstance(stored_data, (bytes, bytearray, memoryview)) else b""
        if (
            not data.startswith(b"%PDF-")
            or len(data) > MAX_RESUME_BYTES
            or not str(resume_doc.get("filename", "")).lower().endswith(".pdf")
        ):
            raise HTTPException(status_code=422, detail="The selected saved resume is not a valid PDF.")
        resume_file = (data, safe_filename(resume_doc["filename"]))

    return _start_browser_run(
        db,
        app_doc,
        target,
        _fill_values(db, app_doc),
        resume_file=resume_file,
    )


@router.get("/api/applications/{application_id}/assistant/runs/{run_id}")
def get_run(application_id: str, run_id: str, db: Database = Depends(get_db)):
    app_doc, _ = _load(db, application_id)
    for r in _stored(app_doc).get("runs", []):
        if r.get("id") == run_id:
            out = {k: r.get(k) for k in ("id", "host", "adapter", "status", "pause_reason",
                                          "fields", "started_at", "finished_at", "blocked_writes",
                                          "browser_open", "submission_enabled", "error_category")}
            out["has_trace"] = _artifact_is_available(db, r, run_id, "trace.zip")
            out["has_screenshot"] = _artifact_is_available(db, r, run_id, "screenshot.png")
            return out
    raise HTTPException(status_code=404, detail="Run not found.")


@router.get("/api/applications/{application_id}/assistant/runs/{run_id}/trace")
def download_run_trace(application_id: str, run_id: str, db: Database = Depends(get_db)):
    from fastapi.responses import FileResponse as _FR
    app_doc, _ = _load(db, application_id)
    run = next((r for r in _stored(app_doc).get("runs", []) if r.get("id") == run_id), None)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    p = _download_artifact_path(db, run, run_id, "trace.zip", "trace")
    return _FR(
        path=str(p),
        filename=f"trace-{run_id[:8]}.zip",
        media_type="application/zip",
    )


@router.get("/api/applications/{application_id}/assistant/runs/{run_id}/screenshot")
def download_run_screenshot(application_id: str, run_id: str, db: Database = Depends(get_db)):
    from fastapi.responses import FileResponse as _FR
    app_doc, _ = _load(db, application_id)
    run = next((r for r in _stored(app_doc).get("runs", []) if r.get("id") == run_id), None)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    p = _download_artifact_path(db, run, run_id, "screenshot.png", "screenshot")
    return _FR(
        path=str(p),
        filename=f"screenshot-{run_id[:8]}.png",
        media_type="image/png",
    )


@router.post("/api/applications/{application_id}/assistant/runs/{run_id}/authorize-submit")
def authorize_manual_submission(
    application_id: str,
    run_id: str,
    db: Database = Depends(get_db),
):
    app_doc, job = _load(db, application_id)
    if app_doc.get("status") != "approved":
        raise HTTPException(status_code=409, detail="Review and approve this application before enabling manual submission.")
    _, digest = _rows_and_hash(db, app_doc, job)
    review = _stored(app_doc).get("review") or {}
    if review.get("hash") != digest:
        raise HTTPException(status_code=409, detail="The reviewed application changed. Review it again before submission.")
    with ACTIVE_RUNS_LOCK:
        active = ACTIVE_RUNS.get(run_id)
    if not active or active[0] != str(app_doc["_id"]):
        raise HTTPException(status_code=409, detail="The application browser is no longer open. Start a new browser run.")
    run = next(
        (item for item in _stored(app_doc).get("runs", []) if item.get("id") == run_id),
        None,
    )
    if not run or run.get("status") not in ("prepared", "needs_you") or not run.get("browser_open"):
        raise HTTPException(status_code=409, detail="The application form is not ready in an open browser.")
    if run.get("adapter") == "local_test":
        raise HTTPException(status_code=403, detail="Manual submission cannot be enabled for the local test form.")
    active[1].set()
    result = db.applications.update_one(
        {"_id": app_doc["_id"], "assistant.runs.id": run_id},
        {"$set": {"assistant.runs.$.submission_enabled": True}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Browser run not found.")
    app_doc, job = _load(db, application_id)
    return _state(db, app_doc, job)


# ---------------- review and submitted ----------------

@router.post("/api/applications/{application_id}/assistant/review")
def confirm_review(application_id: str, payload: ReviewConfirm, db: Database = Depends(get_db)):
    if not payload.acknowledged:
        raise HTTPException(status_code=422, detail="Confirm that you reviewed every answer first.")
    app_doc, job = _load(db, application_id)
    _, digest = _rows_and_hash(db, app_doc, job)
    if payload.hash != digest:
        raise HTTPException(status_code=409, detail="The answers changed since this page loaded. Reload and review again.")
    now = _main().now()
    revision = (_stored(app_doc).get("revision") or 0)
    try:
        approve_reviewed(db, app_doc, digest, now, expected_revision=revision)
    except ApprovalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    app_doc, job = _load(db, application_id)
    return _state(db, app_doc, job)


@router.post("/api/applications/{application_id}/assistant/submitted")
def mark_submitted(application_id: str, payload: SubmittedRequest, db: Database = Depends(get_db)):
    if not payload.confirm:
        raise HTTPException(status_code=422, detail="Confirm that you submitted the application yourself.")
    app_doc, job = _load(db, application_id)
    _, digest = _rows_and_hash(db, app_doc, job)
    now = _main().now()
    revision = (_stored(app_doc).get("revision") or 0)
    try:
        record_submitted(db, app_doc, digest, now, expected_revision=revision)
    except ApprovalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    db.applications.update_one(
        {"_id": app_doc["_id"]},
        {"$addToSet": {"assistant.visited": "done"}},
    )
    app_doc, job = _load(db, application_id)
    return _state(db, app_doc, job)


# ---------------- local test form (not under /api) ----------------

@router.get("/assistant-test-form", response_class=HTMLResponse)
def local_test_form(variant: str = ""):
    if not settings.assistant_allow_local_test_form or settings.app_env.lower() not in ("development", "test"):
        raise HTTPException(status_code=404, detail="The local test form is disabled.")
    return HTMLResponse(test_form.render(variant))


@router.post("/assistant-test-form/submit", response_class=HTMLResponse)
def local_test_form_submit():
    if not settings.assistant_allow_local_test_form or settings.app_env.lower() not in ("development", "test"):
        raise HTTPException(status_code=404, detail="The local test form is disabled.")
    test_form.SUBMISSIONS["count"] += 1
    return HTMLResponse("<p>Test form submitted (nothing was sent anywhere).</p>")
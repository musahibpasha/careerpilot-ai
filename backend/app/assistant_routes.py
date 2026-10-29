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
import threading
import uuid
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
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
from app.assistant_urls import UrlRejected, check_fill_url, is_blocked_host
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


def _effective(db: Database, app_doc: dict) -> tuple[dict, dict, dict]:
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
        active = db.resumes.find_one({"is_active": True}, {"_id": 1})
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
    m = _main()
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
    for candidate in db.resumes.find({}, {"data": 0}):
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
        doc = db.resumes.find_one({"is_active": True}, {"data": 0})
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
            {k: r.get(k) for k in ("id", "host", "adapter", "status", "pause_reason", "fields",
                                    "started_at", "finished_at", "blocked_writes",
                                    "browser_open", "submission_enabled", "error_category")}
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
        m.create_application(m.schemas.ApplicationCreate(job_id=job_id, status="saved"), db)
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
        if not db.resumes.find_one({"_id": resume_id}, {"_id": 1}):
            raise HTTPException(status_code=404, detail="Resume not found.")
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
) -> None:
    m = _main()

    def save(result: dict, finished: bool) -> None:
        sets = {
            "assistant.runs.$.status": result["status"],
            "assistant.runs.$.pause_reason": result.get("pause_reason", ""),
            "assistant.runs.$.fields": result.get("fields", []),
            "assistant.runs.$.blocked_writes": result.get("blocked_writes", 0),
            "assistant.runs.$.browser_open": result.get("browser_open", False),
        }
        if result.get("error_category"):
            sets["assistant.runs.$.error_category"] = result["error_category"]
        if result.get("submission_enabled"):
            sets["assistant.runs.$.submission_enabled"] = True
        if finished:
            sets["assistant.runs.$.finished_at"] = m.now()
        db.applications.update_one({"_id": app_id, "assistant.runs.id": run_id}, {"$set": sets})

    try:
        final = run_assistance(
            target, values,
            headless=settings.assistant_browser_headless,
            keep_open_seconds=settings.assistant_keep_open_seconds,
            on_result=lambda r: save(r, finished=False),
            browser_path=settings.assistant_browser_path or None,
            glassdoor_profile_dir=settings.assistant_glassdoor_profile_dir or None,
            resume_file=resume_file,
            submission_authorized=submission_authorized,
        )
        save(final, finished=True)
    except Exception as exc:  # noqa: BLE001 - never leave a run stuck on 'running'
        error_category = type(exc).__name__
        logger.warning("Assistant run crashed category=%s", error_category)
        save({
            "status": "failed",
            "pause_reason": "unexpected_error",
            "error_category": error_category,
            "fields": [],
        }, finished=True)
    finally:
        with ACTIVE_RUNS_LOCK:
            ACTIVE_RUNS.pop(run_id, None)


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
            active = db.resumes.find_one({"is_active": True}, {"_id": 1})
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

    run_id = uuid.uuid4().hex
    now = _main().now()
    submission_authorized = threading.Event()
    values = _fill_values(db, app_doc)
    with ACTIVE_RUNS_LOCK:
        if len(ACTIVE_RUNS) >= MAX_ACTIVE_RUNS:
            raise HTTPException(status_code=429, detail="Too many browser runs are active. Wait for one to finish.")
        if any(active_application_id == str(app_doc["_id"]) for active_application_id, _ in ACTIVE_RUNS.values()):
            raise HTTPException(status_code=409, detail="A browser assistance run is already in progress.")
        ACTIVE_RUNS[run_id] = (str(app_doc["_id"]), submission_authorized)

    run = {"id": run_id, "host": target.host, "adapter": target.adapter, "status": "running",
           "pause_reason": "", "fields": [], "started_at": now, "finished_at": None,
           "blocked_writes": 0, "browser_open": False, "submission_enabled": False}
    try:
        db.applications.update_one(
            {"_id": app_doc["_id"]},
            {"$push": {"assistant.runs": run},
             "$set": {"assistant.application_url": target.url, "assistant.current_step": "fill"},
             "$addToSet": {"assistant.visited": "fill"}},
        )
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
        ),
        daemon=True,
    ).start()
    logger.info("Assistant run started host=%s adapter=%s", target.host, target.adapter)
    return {"run_id": run_id, "status": "running", "host": target.host}


@router.get("/api/applications/{application_id}/assistant/runs/{run_id}")
def get_run(application_id: str, run_id: str, db: Database = Depends(get_db)):
    app_doc, _ = _load(db, application_id)
    for r in _stored(app_doc).get("runs", []):
        if r.get("id") == run_id:
            return {k: r.get(k) for k in ("id", "host", "adapter", "status", "pause_reason", "fields",
                                           "started_at", "finished_at", "blocked_writes",
                                               "browser_open", "submission_enabled", "error_category")}
    raise HTTPException(status_code=404, detail="Run not found.")


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

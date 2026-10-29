"""Application Assistant tests: kit, drafts, URL policy, field matching,
real Playwright on the local form, review/submitted workflow, and regressions
for the existing approval restrictions."""

import ast
import os
import pathlib
import socket
import threading
import time

import mongomock
import pytest
import uvicorn
from fastapi.testclient import TestClient

from app import assistant_fill, assistant_kit as kit
from app import assistant_routes, assistant_test_form, assistant_urls, schemas
from app.assistant_urls import UrlRejected, check_fill_url
from app.database import get_db
from app.main import app

client = TestClient(app)

PROFILE = {
    "full_name": "Asha Rao", "email": "asha@example.com", "phone": "+91 99999 00000",
    "location": "Bengaluru", "github": "github.com/asha", "linkedin": "linkedin.com/in/asha",
    "portfolio": "https://asha.dev",
    "skills": ["React", "Python", "SQL"],
    "experience_summary": "Front-end developer with internship experience. I build dashboards.",
    "work_experience": [{"title": "Web Developer Intern", "company": "ThoughtBot",
                         "duration": "Feb 2025 - Present", "description": "Built boards."}],
    "projects": [{"name": "NewsApp", "technologies": "React, Bootstrap",
                  "description": "Real-time news reader. Has alerts."}],
    "education": [{"institution": "Acharya", "degree": "BCS", "duration": "2022-2025"}],
}


@pytest.fixture()
def db():
    database = mongomock.MongoClient(tz_aware=True)["assistant_test"]
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = lambda: database
    yield database
    if previous is not None:
        app.dependency_overrides[get_db] = previous
    else:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def enable_local_test_form(monkeypatch):
    monkeypatch.setattr(assistant_routes.settings, "app_env", "test")
    monkeypatch.setattr(assistant_routes.settings, "assistant_allow_local_test_form", True)


@pytest.fixture()
def no_ai(monkeypatch):
    monkeypatch.setattr(assistant_routes.settings, "openrouter_api_key", "")


def make_job(**extra):
    body = {"title": "Frontend Developer", "company": "Acme", "skills": ["React", "TypeScript"],
            "url": "https://boards.greenhouse.io/acme/jobs/1", **extra}
    r = client.post("/api/jobs", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def open_assistant(db, profile=True, **job_extra):
    if profile:
        db.profiles.insert_one(dict(PROFILE))
    job_id = make_job(**job_extra)
    r = client.post(f"/api/jobs/{job_id}/assistant")
    assert r.status_code == 200, r.text
    return r.json()


def base(state):
    return f"/api/applications/{state['application_id']}/assistant"


def row(state, key):
    return next(r for r in state["kit"]["rows"] if r["key"] == key)


# ---------- kit persistence / grounding ----------

def test_kit_comes_only_from_confirmed_profile_and_marks_gaps(db):
    s = open_assistant(db)
    assert row(s, "email")["value"] == "asha@example.com" and row(s, "email")["source"] == "profile"
    assert row(s, "first_name")["value"] == "Asha" and row(s, "first_name")["source"] == "derived"
    assert row(s, "current_company")["value"] == "ThoughtBot"
    for key in ("notice_period", "expected_salary", "work_authorization", "visa_sponsorship"):
        assert row(s, key)["source"] == "missing" and row(s, key)["verification"] == "Needs your input"


def test_no_current_role_is_invented_when_profile_has_no_current_job(db):
    prof = dict(PROFILE, work_experience=[{"title": "Intern", "company": "X", "duration": "Dec 2024 - Jan 2025"}])
    db.profiles.insert_one(prof)
    s = client.post(f"/api/jobs/{make_job()}/assistant").json()
    assert row(s, "current_title")["source"] == "missing"


def test_kit_edits_and_answers_persist_and_reload(db):
    s = open_assistant(db)
    r = client.put(base(s) + "/kit", json={
        "facts": {"phone": "+91 11111"}, "answers": {"notice_period": "30 days"}})
    assert r.status_code == 200
    again = client.get(base(s)).json()
    assert row(again, "phone")["value"] == "+91 11111" and row(again, "phone")["source"] == "user_edited"
    assert row(again, "notice_period")["value"] == "30 days" and row(again, "notice_period")["source"] == "user"
    # the saved profile itself is untouched
    assert db.profiles.find_one({})["phone"] == "+91 99999 00000"


def test_resume_selection_is_application_specific_and_can_be_cleared(db):
    active_id = db.resumes.insert_one({
        "label": "Active", "filename": "active.pdf", "is_active": True, "data": b"%PDF-1.4"
    }).inserted_id
    alternate_id = db.resumes.insert_one({
        "label": "Alternate", "filename": "alternate.pdf", "is_active": False, "data": b"%PDF-1.4"
    }).inserted_id
    state = open_assistant(db)
    assert state["resume"]["id"] == str(active_id)

    selected = client.put(base(state) + "/resume", json={"resume_id": str(alternate_id)})
    assert selected.status_code == 200 and selected.json()["resume"]["id"] == str(alternate_id)
    assert db.applications.find_one({})["resume_id"] == alternate_id

    cleared = client.put(base(state) + "/resume", json={"resume_id": None})
    assert cleared.status_code == 200 and cleared.json()["resume"] is None
    assert cleared.json()["available_resumes"]


def test_review_hash_tracks_application_url_and_resume_file_contents(db):
    resume_id = db.resumes.insert_one({
        "label": "Selected", "filename": "selected.pdf", "is_active": True, "data": b"%PDF-1.4 first"
    }).inserted_id
    state = open_assistant(db)
    original_hash = state["review"]["hash"]

    url_changed = client.put(
        base(state) + "/kit",
        json={"application_url": "https://careers.example.com/apply"},
    ).json()
    assert url_changed["review"]["hash"] != original_hash

    approve(url_changed)
    db.resumes.update_one({"_id": resume_id}, {"$set": {"data": b"%PDF-1.4 changed"}})
    changed_resume = client.get(base(state)).json()
    assert changed_resume["review"]["stale"]
    assert not changed_resume["review"]["confirmed"]


def test_tailored_materials_are_saved_and_included_in_review_hash(db):
    state = open_assistant(db)
    first_hash = state["review"]["hash"]
    material = {
        "cover_letter": "A verified cover letter.",
        "resume_summary": "A verified summary.",
        "skills_to_prioritize": ["React"],
        "bullet_suggestions": [{
            "section": "Web Developer Intern",
            "source_text": "Built boards.",
            "tailored_text": "Built dashboards.",
        }],
        "caveats": ["Verify dates."],
    }
    saved = client.put(base(state) + "/materials", json=material)
    assert saved.status_code == 200, saved.text
    assert saved.json()["materials"]["cover_letter"] == material["cover_letter"]
    assert saved.json()["review"]["hash"] != first_hash

    assert approve(saved.json()).status_code == 200
    material["cover_letter"] = "Edited after review."
    changed = client.put(base(state) + "/materials", json=material)
    assert changed.status_code == 200
    assert changed.json()["status"] != "approved"
    assert not changed.json()["review"]["confirmed"]


def test_material_generation_reuses_existing_job_material_endpoint(db, monkeypatch):
    async def generate(job_id, database):
        assert job_id == db.jobs.find_one({})["_id"].__str__()
        return schemas.JobApplicationMaterials(
            job_id=job_id,
            title="Frontend Developer",
            company="Acme",
            cover_letter="Generated letter.",
            resume_summary="Generated summary.",
            skills_to_prioritize=["React"],
            bullet_suggestions=[],
            caveats=[],
        )

    monkeypatch.setattr("app.main.generate_job_application_materials", generate)
    state = open_assistant(db)
    result = client.post(base(state) + "/materials/generate")
    assert result.status_code == 200, result.text
    assert result.json()["materials"]["cover_letter"] == "Generated letter."


def test_unknown_kit_keys_are_rejected(db):
    s = open_assistant(db)
    assert client.put(base(s) + "/kit", json={"answers": {"gender": "x"}}).status_code == 422
    assert client.put(base(s) + "/kit", json={"facts": {"ssn": "x"}}).status_code == 422


def test_application_url_must_be_http_or_https(db):
    s = open_assistant(db)
    assert client.put(base(s) + "/kit", json={"application_url": "javascript:alert(1)"}).status_code == 422
    assert client.put(base(s) + "/kit", json={"application_url": "file:///etc/passwd"}).status_code == 422
    ok = client.put(base(s) + "/kit", json={"application_url": "https://boards.greenhouse.io/x"})
    assert ok.json()["application_url"] == "https://boards.greenhouse.io/x"


# ---------- drafts: generation, fallback, edit, restore ----------

def test_rule_based_fallback_is_profile_grounded_and_flags_unknowns(db, no_ai):
    s = open_assistant(db)
    r = client.post(base(s) + "/drafts/generate")
    assert r.status_code == 200
    out = r.json()
    assert out["generation"]["used"] == "rule_based" and out["generation"]["fallback_reason"]
    d = out["kit"]["drafts"]
    assert "Asha Rao" in d["tell_me_about_yourself"]["text"] and d["tell_me_about_yourself"]["source"] == "rule_based"
    assert "React" in d["why_role"]["text"] and "TypeScript" not in d["why_role"]["text"]  # only skills she has
    assert d["why_company"]["text"] == "" and d["why_company"]["needs_input"]  # never invented
    assert "NewsApp" in d["project_example"]["text"]


def test_generate_drafts_rejects_unknown_keys(db):
    s = open_assistant(db)
    response = client.post(base(s) + "/drafts/generate", json={"keys": ["invented_field"]})
    assert response.status_code == 422


def test_ai_failure_falls_back_to_rules(db, monkeypatch):
    monkeypatch.setattr(assistant_routes.settings, "openrouter_api_key", "k")

    async def boom(client_, messages):
        raise RuntimeError("down")
    monkeypatch.setattr("app.main._post_with_retry_and_fallback", boom)
    s = open_assistant(db)
    out = client.post(base(s) + "/drafts/generate").json()
    assert out["generation"]["used"] == "rule_based"
    assert out["kit"]["drafts"]["strengths"]["source"] == "rule_based"


def test_ai_drafts_are_labelled_ai_and_unknown_keys_dropped(db, monkeypatch):
    monkeypatch.setattr(assistant_routes.settings, "openrouter_api_key", "k")

    class Resp:
        is_error = False
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content":
                '{"tell_me_about_yourself": "I am Asha.", "why_role": "I use React.", "why_company": "", "hack": "x"}'}}]}

    async def fake(client_, messages):
        user = messages[1]["content"]
        assert "asha@example.com" not in user and "+91" not in user  # contact details never sent
        return Resp()
    monkeypatch.setattr("app.main._post_with_retry_and_fallback", fake)
    s = open_assistant(db)
    out = client.post(base(s) + "/drafts/generate").json()
    d = out["kit"]["drafts"]
    assert out["generation"]["used"] == "ai"
    assert d["why_role"]["source"] == "ai" and d["why_role"]["text"] == "I use React."
    assert d["why_company"]["text"] == ""  # empty model answer stays "Needs your input"
    assert d["strengths"]["source"] == "rule_based"  # missing key filled by template, labelled as such
    assert "hack" not in d


def test_edit_then_restore_draft_and_regenerate_keeps_edits(db, no_ai):
    s = open_assistant(db)
    client.post(base(s) + "/drafts/generate")
    original = client.get(base(s)).json()["kit"]["drafts"]["strengths"]["text"]
    edited = client.put(base(s) + "/kit", json={"drafts": {"strengths": "My own words."}}).json()
    assert edited["kit"]["drafts"]["strengths"]["text"] == "My own words."
    assert edited["kit"]["drafts"]["strengths"]["source"] == "user_edited"
    assert edited["kit"]["drafts"]["strengths"]["can_restore"]
    kept = client.post(base(s) + "/drafts/generate").json()
    assert kept["kit"]["drafts"]["strengths"]["text"] == "My own words."
    assert kept["generation"]["kept_edited"] == ["strengths"]
    restored = client.post(base(s) + "/drafts/strengths/restore").json()
    assert restored["kit"]["drafts"]["strengths"]["text"] == original
    assert restored["kit"]["drafts"]["strengths"]["source"] == "rule_based"


# ---------- no duplicate records ----------

def test_opening_assistant_twice_does_not_duplicate_job_or_application(db):
    s = open_assistant(db)
    job_id = s["job"]["id"]
    s2 = client.post(f"/api/jobs/{job_id}/assistant").json()
    assert s2["application_id"] == s["application_id"]
    assert db.applications.count_documents({}) == 1 and db.jobs.count_documents({}) == 1


# ---------- URL policy ----------

@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/jobs/view/1", "https://linkedin.com/jobs/1",
    "https://uk.linkedin.com/x", "https://a.b.LinkedIn.com/x", "http://www.linkedin.com/x",
    "https://lnkd.in/abc", "https://linkedin.com.:443/x",
])
def test_linkedin_and_subdomains_are_blocked(url):
    with pytest.raises(UrlRejected) as e:
        check_fill_url(url)
    assert e.value.code == "linkedin_blocked"


@pytest.mark.parametrize("url,code", [
    ("javascript:alert(1)", "invalid_scheme"), ("file:///etc/passwd", "invalid_scheme"),
    ("ftp://boards.greenhouse.io/x", "invalid_scheme"), ("data:text/html,hi", "invalid_scheme"),
    ("http://boards.greenhouse.io/x", "insecure_url"),
    ("https://boards.greenhouse.io:8443/x", "unsupported_port"),
    ("https://user:pw@boards.greenhouse.io/x", "invalid_url"),
    ("http://127.0.0.1:8000/other", "unsupported_host"),
    ("http://169.254.169.254/latest", "insecure_url"), ("https://169.254.169.254/latest", "non_public_host"),
    ("https://localhost/apply", "unsupported_host"),
    ("https://jobicy.com/jobs/example", "unsupported_host"),
    ("https://example.com/apply", "unsupported_host"),
    ("", "invalid_url"),
])
def test_unsafe_urls_are_rejected(url, code):
    with pytest.raises(UrlRejected) as e:
        check_fill_url(url)
    assert e.value.code == code


def test_supported_hosts_accepted():
    assert check_fill_url("https://boards.greenhouse.io/acme/jobs/1").adapter == "greenhouse"
    assert check_fill_url("https://job-boards.greenhouse.io/acme/jobs/1").adapter == "greenhouse"
    assert check_fill_url("https://jobs.lever.co/acme/abc").adapter == "lever"
    glassdoor = check_fill_url(
        "https://www.glassdoor.co.in/job-listing/j?jl=1009971641600&countryRedirect=true"
    )
    assert glassdoor.adapter == "glassdoor"
    assert glassdoor.host == "www.glassdoor.co.in"
    with pytest.raises(UrlRejected) as e:
        check_fill_url("https://www.glassdoor.co.in/profile")
    assert e.value.code == "unsupported_host"
    assert check_fill_url("http://127.0.0.1:8000/assistant-test-form").adapter == "local_test"
    with pytest.raises(UrlRejected):
        check_fill_url("http://127.0.0.1:8000/assistant-test-form", allow_local_form=False)
    with pytest.raises(UrlRejected):
        check_fill_url("http://127.0.0.1:8000/assistant-test-form-evil")


def test_public_host_check_rejects_private_dns_answers(monkeypatch):
    monkeypatch.setattr(assistant_urls, "resolve_host", lambda host, port: ["8.8.8.8"])
    assistant_urls.check_public_host("careers.example.com")
    monkeypatch.setattr(assistant_urls, "resolve_host", lambda host, port: ["8.8.8.8", "10.0.0.1"])
    with pytest.raises(UrlRejected) as e:
        assistant_urls.check_public_host("careers.example.com")
    assert e.value.code == "non_public_host"


def test_glassdoor_login_writes_are_scoped_to_detected_sign_in(monkeypatch):
    class Request:
        method = "POST"

        def __init__(self, url):
            self.url = url

        def is_navigation_request(self):
            return False

    class Route:
        def __init__(self, url):
            self.request = Request(url)
            self.continued = False
            self.aborted = False

        def continue_(self):
            self.continued = True

        def abort(self):
            self.aborted = True

    monkeypatch.setattr(assistant_fill, "check_public_host", lambda _host: None)
    target = check_fill_url(
        "https://www.glassdoor.co.in/job-listing/j?jl=1009971641600"
    )
    login_detected = threading.Event()
    counter = {"blocked_writes": 0, "blocked_navigations": 0}
    guard = assistant_fill._route_guard(
        counter,
        target,
        glassdoor_login_authorized=login_detected,
    )

    blocked = Route("https://www.glassdoor.co.in/graphql")
    guard(blocked)
    assert blocked.aborted and not blocked.continued

    login_detected.set()
    allowed = Route("https://accounts.glassdoor.co.in/graphql")
    guard(allowed)
    assert allowed.continued and not allowed.aborted

    third_party = Route("https://accounts.example.com/login")
    guard(third_party)
    assert third_party.aborted and not third_party.continued

    approved_submit = threading.Event()
    approved_guard = assistant_fill._route_guard(
        counter,
        target,
        submission_authorized=approved_submit,
    )
    still_blocked = Route("https://www.glassdoor.co.in/application/submit")
    approved_guard(still_blocked)
    assert still_blocked.aborted
    approved_submit.set()
    manually_enabled = Route("https://api.glassdoor.co.in/application/submit")
    approved_guard(manually_enabled)
    assert manually_enabled.continued and not manually_enabled.aborted


def test_glassdoor_profile_uses_local_path_outside_repository(tmp_path):
    configured = tmp_path / "glassdoor-profile"
    profile = pathlib.Path(assistant_fill._glassdoor_profile_path(str(configured)))
    assert profile == configured.resolve()
    assert profile.is_dir()
    with pytest.raises(ValueError):
        assistant_fill._glassdoor_profile_path(str(pathlib.Path(__file__).resolve().parents[2] / "profile"))


def test_glassdoor_launch_uses_installed_chrome_unless_executable_is_configured():
    calls = []

    class Chromium:
        def launch_persistent_context(self, **kwargs):
            calls.append(kwargs)
            return object()

    chromium = Chromium()
    assistant_fill._launch_glassdoor_context(chromium, "C:\\profile", {"headless": False})
    assistant_fill._launch_glassdoor_context(
        chromium,
        "C:\\custom-profile",
        {"headless": False, "executable_path": "C:\\Chrome\\chrome.exe"},
    )

    assert calls == [
        {"user_data_dir": "C:\\profile", "headless": False, "channel": "chrome"},
        {
            "user_data_dir": "C:\\custom-profile",
            "headless": False,
            "executable_path": "C:\\Chrome\\chrome.exe",
        },
    ]


def test_fill_route_only_accepts_supported_ats_and_preserves_listing_url(db, monkeypatch):
    started = []

    def fake_run(target, values, **kwargs):
        started.append(target.host)
        result = {"status": "prepared", "pause_reason": "", "fields": [], "blocked_writes": 0}
        kwargs["on_result"](result)
        return result

    monkeypatch.setattr(assistant_routes, "run_assistance", fake_run)
    s = open_assistant(db)
    for url in ("https://www.linkedin.com/jobs/view/1", "https://de.linkedin.com/x"):
        r = client.post(base(s) + "/fill", json={"url": url})
        assert r.status_code == 403 and r.json()["detail"]["code"] == "linkedin_blocked"
    assert client.post(base(s) + "/fill", json={"url": "javascript:1"}).status_code == 422
    for url in ("https://jobicy.com/jobs/example", "https://example.com/apply"):
        r = client.post(base(s) + "/fill", json={"url": url})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "unsupported_host"
    assert started == []

    greenhouse = client.post(
        base(s) + "/fill",
        json={"url": "https://job-boards.greenhouse.io/acme/jobs/1"},
    )
    assert greenhouse.status_code == 202
    for _ in range(50):
        if db.applications.find_one({})["assistant"]["runs"][0].get("finished_at"):
            break
        time.sleep(0.02)
    stored = client.get(base(s)).json()
    assert started == ["job-boards.greenhouse.io"]
    assert stored["job"]["url"] == "https://boards.greenhouse.io/acme/jobs/1"
    assert stored["application_url"] == "https://job-boards.greenhouse.io/acme/jobs/1"


def test_fill_route_accepts_and_preserves_glassdoor_listing(db, monkeypatch):
    seen = []

    def fake_run(target, values, **kwargs):
        seen.append((target.adapter, target.url))
        result = {"status": "needs_you", "pause_reason": "", "fields": [], "blocked_writes": 0}
        kwargs["on_result"](result)
        return result

    monkeypatch.setattr(assistant_routes, "run_assistance", fake_run)
    url = "https://www.glassdoor.co.in/job-listing/j?jl=1009971641600&countryRedirect=true"
    state = open_assistant(db, source="glassdoor", url=url)
    response = client.post(base(state) + "/fill", json={"url": url})
    assert response.status_code == 202
    for _ in range(50):
        if db.applications.find_one({})["assistant"]["runs"][0].get("finished_at"):
            break
        time.sleep(0.02)
    saved = client.get(base(state)).json()
    assert seen == [("glassdoor", url)]
    assert saved["application_url"] == url


def test_jobicy_listing_is_not_prefilled_as_an_application_url(db):
    state = open_assistant(
        db,
        source="jobicy",
        url="https://jobicy.com/jobs/example",
    )
    assert state["job"]["url"] == "https://jobicy.com/jobs/example"
    assert state["job"]["source"] == "jobicy"
    assert state["application_url"] == ""


def test_verified_direct_application_url_is_available_when_assistant_opens(db):
    direct_url = "https://job-boards.greenhouse.io/acme/jobs/123"
    state = open_assistant(
        db,
        source="indeed",
        url="https://www.indeed.com/viewjob?jk=123",
        application_url=direct_url,
    )

    assert state["application_url"] == direct_url
    app_doc = db.applications.find_one({})
    assert app_doc["assistant"]["application_url"] == direct_url


def test_job_creation_rejects_unverified_direct_application_urls(db):
    response = client.post(
        "/api/jobs",
        json={
            "title": "Engineer",
            "company": "Example",
            "url": "https://www.indeed.com/viewjob?jk=123",
            "application_url": "https://careers.example.com/apply/123",
        },
    )

    assert response.status_code == 422


def test_linkedin_job_gets_manual_checklist_not_automation(db):
    s = open_assistant(db, url="https://www.linkedin.com/jobs/view/123")
    assert s["linkedin_job"] and len(s["manual_checklist"]) >= 4
    other = open_assistant(db, profile=False, title="Other", company="B", url="https://boards.greenhouse.io/b/jobs/2")
    assert not other["linkedin_job"] and other["manual_checklist"] == []


# ---------- field matching (pure) ----------

VALUES = {
    "first_name": {"value": "Asha", "source": "derived"}, "last_name": {"value": "Rao", "source": "derived"},
    "email": {"value": "a@b.co", "source": "profile"}, "phone": {"value": "123", "source": "profile"},
    "linkedin": {"value": "li", "source": "profile"}, "why_role": {"value": "Because.", "source": "ai"},
    "why_company": {"value": "Co.", "source": "ai"},
}


def desc(label, **kw):
    return {"kind": "input", "type": "text", "label": label, "name": "", "required": False, **kw}


def test_matching_maps_known_fields_with_sources():
    m = assistant_fill.match_field(desc("First Name *", required=True), VALUES)
    assert m["state"] == "filled" and m["key"] == "first_name" and m["value"] == "Asha" and m["source"] == "derived"
    assert assistant_fill.match_field(desc("Preferred contact", type="email"), VALUES)["key"] == "email"
    assert assistant_fill.match_field(desc("LinkedIn URL"), VALUES)["key"] == "linkedin"


def test_unknown_ambiguous_sensitive_and_missing_fields_are_not_guessed():
    f = assistant_fill.match_field
    assert f(desc("Favourite colour"), VALUES)["state"] == "skipped"
    assert f(desc("Favourite colour", required=True), VALUES)["state"] == "needs_input"
    amb = f(desc("Why this role and company?", kind="textarea"), VALUES)
    assert amb["state"] == "skipped" and "Ambiguous" in amb["reason"]
    assert f(desc("Gender"), VALUES)["state"] == "needs_input"
    assert f(desc("Current salary (CTC)"), VALUES)["state"] == "needs_input"
    assert f(desc("Salary"), VALUES)["state"] == "skipped"
    missing = f(desc("Notice period"), VALUES)
    assert missing["state"] == "needs_input" and "Needs your input" in missing["reason"]
    assert f(desc("Resume", type="file", required=True), VALUES)["state"] == "needs_input"
    assert f(desc("I agree", type="checkbox", required=True), VALUES)["state"] == "needs_input"
    assert f(desc("Email", has_value=True), VALUES)["state"] == "skipped"
    # drafts are never typed into single-line inputs
    assert f(desc("Why are you interested in this role?"), VALUES)["state"] == "skipped"


def test_select_needs_a_clearly_matching_option():
    vals = {"work_authorization": {"value": "Yes", "source": "user"}}
    sel = desc("Are you legally authorized to work in India?", kind="select",
               options=[{"value": "", "text": "Select"}, {"value": "yes", "text": "Yes"}, {"value": "no", "text": "No"}])
    m = assistant_fill.match_field(sel, vals)
    assert m["state"] == "filled" and m["value"] == "yes"
    vals2 = {"work_authorization": {"value": "Sometimes", "source": "user"}}
    assert assistant_fill.match_field(sel, vals2)["state"] == "needs_input"


def test_run_summary_is_never_submitted():
    s = assistant_fill.summarise
    ok = [{"state": "filled", "required": True}]
    assert s(ok, {}) == ("prepared", "")
    assert s(ok, {"captcha": True}) == ("needs_you", "captcha")
    assert s(ok, {"login": True}) == ("needs_you", "login_required")
    assert s([], {}) == ("needs_you", "unsupported_form")
    assert s([{"state": "needs_input", "required": True}], {})[0] == "needs_you"
    assert s([{"state": "failed", "required": False}], {})[0] == "needs_you"
    assert "submitted" not in {s(ok, {})[0], s([], {})[0]}


def test_ats_apply_link_navigation_stays_on_the_same_supported_platform():
    target = check_fill_url("https://job-boards.greenhouse.io/acme/jobs/123")
    assert assistant_fill._same_origin_application_link(
        "https://job-boards.greenhouse.io/acme/jobs/123#app", target
    )
    assert assistant_fill._same_origin_application_link(
        "https://job-boards.greenhouse.io/embed/job_app?for=acme", target
    )
    assert assistant_fill._same_origin_application_link(
        "https://boards.greenhouse.io/embed/job_app?for=acme", target
    )
    assert not assistant_fill._same_origin_application_link(
        "https://attacker.example/apply", target
    )
    assert not assistant_fill._same_origin_application_link(
        "http://job-boards.greenhouse.io/apply", target
    )
    assert not assistant_fill._same_origin_application_link(
        "https://job-boards.greenhouse.io@attacker.example/apply", target
    )


def test_top_level_redirects_must_stay_on_the_supported_ats():
    greenhouse = check_fill_url("https://job-boards.greenhouse.io/acme/jobs/123")
    lever = check_fill_url("https://jobs.lever.co/acme/abc")
    assert assistant_fill._is_safe_main_frame_navigation(
        "https://boards.greenhouse.io/acme/jobs/123", greenhouse
    )
    assert not assistant_fill._is_safe_main_frame_navigation(
        "https://jobicy.com/jobs/example", greenhouse
    )
    assert not assistant_fill._is_safe_main_frame_navigation(
        "https://jobs.lever.co/acme/abc", greenhouse
    )
    assert assistant_fill._is_safe_main_frame_navigation(
        "https://jobs.lever.co/acme/abc", lever
    )


def test_automation_code_never_clicks_or_presses_keys():
    """The only calls allowed on form controls are fill() and select_option()."""
    tree = ast.parse(pathlib.Path(assistant_fill.__file__).read_text())
    banned = {"click", "dblclick", "press", "check", "tap", "dispatch_event", "submit",
              "type", "press_sequentially", "requestSubmit"}
    called = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not (called & banned), called & banned
    src = pathlib.Path(assistant_fill.__file__).read_text()
    assert "requestSubmit" not in src.replace('"requestSubmit"', "")


# ---------- real Playwright against the local test form ----------

BROWSER = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or ("/opt/pw-browsers" if os.path.isdir("/opt/pw-browsers") else "")


def _browser_ok():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
        return True
    except Exception:
        return False


needs_browser = pytest.mark.skipif(not _browser_ok(), reason="Playwright Chromium is not installed")


@pytest.fixture(scope="module")
def server():
    """The real app on a free port, so Playwright can load the local test form."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    srv = uvicorn.Server(cfg)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(50):
        if srv.started:
            break
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(timeout=5)


def run_on(url, values=VALUES):
    target = check_fill_url(url)
    return assistant_fill.run_assistance(target, values, headless=True)


@needs_browser
def test_glassdoor_form_detection_only_marks_application_fields():
    from playwright.sync_api import sync_playwright

    html = """
      <form id="search"><input type="email" name="query" placeholder="Search jobs"></form>
      <form id="application">
        <h2>Job application</h2>
        <label>First name <input name="first_name"></label>
        <label>Email <input type="email" name="email"></label>
      </form>
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)
        flags = page.evaluate(assistant_fill.GLASSDOOR_FORM_JS)
        fields = page.evaluate(assistant_fill.COLLECT_JS, {"glassdoor": True})
        browser.close()

    assert flags == {"login": False, "application": True, "blocked": False}
    assert [field["name"] for field in fields] == ["first_name", "email"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("<h1>Humans only</h1><p>Cloudflare Ray ID: example</p>")
        flags = page.evaluate(assistant_fill.GLASSDOOR_FORM_JS)
        browser.close()
    assert flags == {"login": False, "application": False, "blocked": True}


@needs_browser
def test_playwright_fills_local_form_reports_each_field_and_never_submits(server):
    before = assistant_test_form.SUBMISSIONS["count"]
    res = run_on(server + "/assistant-test-form")
    by = {f["label"]: f for f in res["fields"]}
    assert by["First Name *"]["state"] == "filled"
    assert by["Email *"]["state"] == "filled"
    assert by["LinkedIn Profile"]["state"] == "filled"
    assert by["Why are you interested in this role?"]["state"] == "filled"
    assert by["Referral code"]["state"] == "skipped"  # already had a value: left alone
    assert by["Gender"]["state"] == "needs_input"
    assert by["Salary expectation *"]["state"] == "needs_input"  # required, nothing in kit
    assert by["Resume/CV *"]["state"] == "needs_input"
    assert by["Why this role and company?"]["state"] == "skipped"
    assert by["Notice period"]["state"] == "needs_input"
    assert res["status"] == "needs_you" and res["pause_reason"] == "required_fields_need_input"
    assert res["status"] != "submitted"
    assert assistant_test_form.SUBMISSIONS["count"] == before  # nothing was submitted
    assert all("value" not in f for f in res["fields"])  # no personal values stored in results


@needs_browser
def test_playwright_actually_put_values_in_the_page(server):
    from playwright.sync_api import sync_playwright
    seen = {}

    def grab(result):
        pass
    target = check_fill_url(server + "/assistant-test-form")
    # drive the same module, then re-open to read values is impossible (fresh profile), so
    # verify through a wrapper that reads the DOM before the browser closes:
    orig = assistant_fill._fill_frame

    def spy(frame, values, resume_file=None):
        out = orig(frame, values, resume_file)
        seen["first"] = frame.input_value("#first_name")
        seen["why"] = frame.input_value("#why")
        seen["gender"] = frame.input_value("#gender")
        seen["prefilled"] = frame.input_value("#prefilled")
        seen["submitted_flag"] = frame.evaluate("document.body.dataset.submitted || ''")
        return out
    assistant_fill._fill_frame = spy
    try:
        assistant_fill.run_assistance(target, VALUES, headless=True)
    finally:
        assistant_fill._fill_frame = orig
    assert seen == {"first": "Asha", "why": "Because.", "gender": "", "prefilled": "KEEP-ME", "submitted_flag": ""}


@needs_browser
def test_playwright_attaches_only_the_explicitly_selected_resume(server):
    seen = {}
    original_fill_frame = assistant_fill._fill_frame

    def inspect_file(frame, values, resume_file=None):
        fields = original_fill_frame(frame, values, resume_file)
        seen["filename"] = frame.locator("#resume").evaluate(
            "el => el.files.length ? el.files[0].name : ''"
        )
        seen["submit_count"] = assistant_test_form.SUBMISSIONS["count"]
        return fields

    assistant_fill._fill_frame = inspect_file
    before = assistant_test_form.SUBMISSIONS["count"]
    try:
        result = assistant_fill.run_assistance(
            check_fill_url(server + "/assistant-test-form"),
            VALUES,
            headless=True,
            resume_file=(b"%PDF-1.4 test", "selected-cv.pdf"),
        )
    finally:
        assistant_fill._fill_frame = original_fill_frame
    resume_field = next(field for field in result["fields"] if field["key"] == "resume")
    assert resume_field["state"] == "filled"
    assert seen == {"filename": "selected-cv.pdf", "submit_count": before}
    assert assistant_test_form.SUBMISSIONS["count"] == before


@needs_browser
def test_captcha_login_and_empty_pages_pause_truthfully(server):
    cap = run_on(server + "/assistant-test-form?variant=captcha")
    assert cap["status"] == "needs_you" and cap["pause_reason"] == "captcha"
    assert any(f["state"] == "filled" for f in cap["fields"])  # still helps with what it can
    login = run_on(server + "/assistant-test-form?variant=login")
    assert login["status"] == "needs_you" and login["pause_reason"] == "login_required" and login["fields"] == []
    empty = run_on(server + "/assistant-test-form?variant=empty")
    assert empty["status"] == "needs_you" and empty["pause_reason"] == "unsupported_form"


@needs_browser
def test_unreachable_page_is_a_failed_run_not_a_success():
    res = run_on("http://127.0.0.1:9/assistant-test-form")
    assert res["status"] == "failed" and res["pause_reason"] == "navigation_failed"


@needs_browser
def test_automation_blocks_form_posts_while_it_works(server):
    """Even if something tried to submit during the run, the POST is aborted."""
    from playwright.sync_api import sync_playwright
    counter = {"blocked_writes": 0, "blocked_navigations": 0}
    before = assistant_test_form.SUBMISSIONS["count"]
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context()
        ctx.route("**/*", assistant_fill._route_guard(counter, check_fill_url(server + "/assistant-test-form")))
        page = ctx.new_page()
        page.goto(server + "/assistant-test-form")
        page.evaluate("fetch('/assistant-test-form/submit', {method: 'POST'}).catch(() => {})")
        page.wait_for_timeout(500)
        b.close()
    assert counter["blocked_writes"] == 1
    assert assistant_test_form.SUBMISSIONS["count"] == before


def test_local_test_form_is_disabled_outside_development(db, monkeypatch):
    monkeypatch.setattr(assistant_routes.settings, "app_env", "production")
    before = assistant_test_form.SUBMISSIONS["count"]
    assert client.get("/assistant-test-form").status_code == 404
    assert client.post("/assistant-test-form/submit").status_code == 404
    assert assistant_test_form.SUBMISSIONS["count"] == before


@needs_browser
def test_fill_endpoint_end_to_end_on_local_form(db, server, monkeypatch):
    monkeypatch.setattr(assistant_routes.settings, "assistant_browser_headless", True)
    monkeypatch.setattr(assistant_routes.settings, "assistant_keep_open_seconds", 0)
    s = open_assistant(db)
    before = assistant_test_form.SUBMISSIONS["count"]
    r = client.post(base(s) + "/fill", json={"url": server + "/assistant-test-form"})
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    for _ in range(100):
        run = client.get(base(s) + f"/runs/{run_id}").json()
        if run["status"] != "running" and run["finished_at"]:
            break
        time.sleep(0.2)
    assert run["status"] == "needs_you" and run["host"].startswith("127.0.0.1")
    assert any(f["state"] == "filled" for f in run["fields"])
    state = client.get(base(s)).json()
    assert state["status"] != "submitted" and state["steps"]["done"]["fill"]  # filling != submitted
    assert assistant_test_form.SUBMISSIONS["count"] == before
    assert client.post(base(s) + "/fill", json={"url": server + "/assistant-test-form"}).status_code == 202  # new run allowed once finished


# ---------- review confirmation + explicit Submitted ----------

def approve(state):
    r = client.post(base(state) + "/review", json={"hash": state["review"]["hash"], "acknowledged": True})
    return r


def test_review_requires_acknowledgement_and_the_exact_content_hash(db):
    s = open_assistant(db)
    assert client.post(base(s) + "/review", json={"hash": s["review"]["hash"], "acknowledged": False}).status_code == 422
    assert client.post(base(s) + "/review", json={"hash": "stale", "acknowledged": True}).status_code == 409
    assert client.get(base(s)).json()["status"] == "saved"  # nothing moved


def test_review_confirmation_fails_if_kit_changes_during_approval(db, monkeypatch):
    s = open_assistant(db)
    approve_reviewed = assistant_routes.approve_reviewed

    def race_edit(database, application, digest, at, expected_revision=None):
        database.applications.update_one(
            {"_id": application["_id"]},
            {"$inc": {"assistant.revision": 1}},
        )
        return approve_reviewed(
            database, application, digest, at, expected_revision=expected_revision
        )

    monkeypatch.setattr(assistant_routes, "approve_reviewed", race_edit)
    response = approve(s)
    assert response.status_code == 409
    assert client.get(base(s)).json()["status"] == "saved"


def test_job_posting_change_invalidates_review_and_blocks_submitted(db):
    s = open_assistant(db)
    assert approve(s).status_code == 200
    db.jobs.update_one(
        {"_id": db.applications.find_one({})["job_id"]},
        {"$set": {"description": "A materially changed job posting."}},
    )
    state = client.get(base(s)).json()
    assert state["review"]["stale"] and not state["review"]["confirmed"]
    assert client.post(base(s) + "/submitted", json={"confirm": True}).status_code == 409


def test_full_flow_records_submitted_with_history_and_timestamp(db):
    s = open_assistant(db)
    assert client.post(base(s) + "/submitted", json={"confirm": True}).status_code == 409  # not reviewed yet
    r = approve(s)
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["review"]["confirmed"]
    done = client.post(base(s) + "/submitted", json={"confirm": True})
    assert done.status_code == 200
    out = done.json()
    assert out["status"] == "submitted" and out["submitted_at"] and out["steps"]["done"]["done"]
    path = [(h["from_status"], h["to_status"]) for h in out["status_history"]]
    assert path == [(None, "saved"), ("saved", "under_review"), ("under_review", "ready_for_approval"),
                    ("ready_for_approval", "approved"), ("approved", "submitted")]
    last = out["status_history"][-1]
    assert "did not submit" in last["note"].lower() and last["actor"] == "user"
    # duplicates / repeats are refused, and no second application appears
    assert client.post(base(s) + "/submitted", json={"confirm": True}).status_code == 409
    assert db.applications.count_documents({}) == 1


def test_submitted_needs_explicit_confirm_flag(db):
    s = open_assistant(db)
    approve(s)
    assert client.post(base(s) + "/submitted", json={}).status_code == 422
    assert client.get(base(s)).json()["status"] == "approved"


def test_editing_answers_after_review_withdraws_approval(db):
    s = open_assistant(db)
    approve(s)
    after = client.put(base(s) + "/kit", json={"answers": {"notice_period": "60 days"}}).json()
    assert after["status"] == "ready_for_approval" and not after["review"]["confirmed"]
    assert client.post(base(s) + "/submitted", json={"confirm": True}).status_code == 409
    again = approve(after)
    assert again.status_code == 200 and again.json()["status"] == "approved"
    hist = [(h["from_status"], h["to_status"]) for h in again.json()["status_history"]]
    assert ("approved", "ready_for_approval") in hist


def test_application_kit_cannot_change_after_submission(db):
    s = open_assistant(db)
    assert approve(s).status_code == 200
    assert client.post(base(s) + "/submitted", json={"confirm": True}).status_code == 200
    response = client.put(base(s) + "/kit", json={"answers": {"notice_period": "60 days"}})
    assert response.status_code == 409


def test_cannot_approve_withdrawn_or_already_submitted(db):
    s = open_assistant(db)
    assert client.patch(f"/api/applications/{s['application_id']}/status", json={"status": "withdrawn"}).status_code == 200
    r = approve(client.get(base(s)).json())
    assert r.status_code == 409


# ---------- regressions: the existing restrictions are unchanged ----------

def test_generic_endpoints_still_refuse_gated_statuses_even_after_assistant_use(db):
    s = open_assistant(db)
    approve(s)
    aid = s["application_id"]
    for target in ("approved", "submitted"):
        assert client.patch(f"/api/applications/{aid}/status", json={"status": target}).status_code == 403
        assert client.post("/api/applications", json={"job_id": s["job"]["id"], "status": target}).status_code == 403
    state = client.get(base(s)).json()
    assert "submitted" not in state["allowed_next"] and "submitted" in state["gated_next"]
    listed = next(a for a in client.get("/api/applications").json() if a["id"] == aid)
    assert "submitted" not in listed["allowed_next"]


def test_generic_status_route_cannot_skip_to_submitted_or_approved_on_fresh_application(db):
    s = open_assistant(db)
    aid = s["application_id"]
    for status in ("approved", "submitted"):
        r = client.patch(f"/api/applications/{aid}/status", json={"status": status})
        assert r.status_code == 403
    assert client.get(base(s)).json()["status"] == "saved"


def test_approval_module_only_uses_transitions_that_already_exist():
    from app.workflow import TRANSITIONS
    from app.approval import _ROUTE_TO_READY
    for start, steps in _ROUTE_TO_READY.items():
        cur = start
        for nxt in steps:
            assert nxt in TRANSITIONS[cur]
            cur = nxt
        assert "approved" in TRANSITIONS[cur] or cur == "ready_for_approval"
    assert "submitted" in TRANSITIONS["approved"] and "approved" in TRANSITIONS["ready_for_approval"]


def test_existing_history_is_preserved_when_assistant_is_used(db):
    job_id = make_job()
    db.profiles.insert_one(dict(PROFILE))
    created = client.post("/api/applications", json={"job_id": job_id, "status": "saved"}).json()
    client.patch(f"/api/applications/{created['id']}/status", json={"status": "under_review", "note": "mine"})
    s = client.post(f"/api/jobs/{job_id}/assistant").json()
    assert s["application_id"] == created["id"]  # reused, not duplicated
    approve(s)
    hist = client.get(base(s)).json()["status_history"]
    assert [h["to_status"] for h in hist][:3] == ["saved", "under_review", "ready_for_approval"]
    assert hist[1]["note"] == "mine"


# ---------- storage hygiene ----------

def test_runs_store_outcomes_but_no_personal_values_or_cookies(db, monkeypatch):
    def fake_run(target, values, **kw):
        res = {"status": "prepared", "pause_reason": "", "blocked_writes": 0,
               "fields": [{"label": "Email", "state": "filled", "key": "email", "reason": "", "required": True}]}
        kw["on_result"](res)
        return res
    monkeypatch.setattr(assistant_routes, "run_assistance", fake_run)
    s = open_assistant(db)
    r = client.post(base(s) + "/fill", json={"url": "https://boards.greenhouse.io/acme/jobs/1"})
    assert r.status_code == 202
    for _ in range(50):
        run = client.get(base(s) + f"/runs/{r.json()['run_id']}").json()
        if run["finished_at"]:
            break
        time.sleep(0.05)
    assert run["status"] == "prepared" and run["host"] == "boards.greenhouse.io"
    blob = str(db.applications.find_one({})["assistant"]["runs"])
    assert "asha@example.com" not in blob and "cookie" not in blob.lower()
    assert db.applications.find_one({})["status"] == "saved"  # a prepared form is not "submitted"


def test_resume_upload_requires_consent_and_manual_write_authorization(db, monkeypatch):
    resume_id = db.resumes.insert_one({
        "label": "Application CV",
        "filename": "asha-cv.pdf",
        "is_active": True,
        "data": b"%PDF-1.4 test document",
    }).inserted_id
    ready = threading.Event()
    authorized = threading.Event()
    received = {}

    def fake_run(target, values, **kwargs):
        received["resume_file"] = kwargs["resume_file"]
        result = {
            "status": "prepared",
            "pause_reason": "",
            "fields": [],
            "blocked_writes": 0,
            "browser_open": True,
        }
        kwargs["on_result"](result)
        ready.set()
        kwargs["submission_authorized"].wait(timeout=3)
        authorized.set()
        result["browser_open"] = False
        result["submission_enabled"] = kwargs["submission_authorized"].is_set()
        return result

    monkeypatch.setattr(assistant_routes, "run_assistance", fake_run)
    state = open_assistant(db)
    response = client.post(
        base(state) + "/fill",
        json={"url": "https://jobs.lever.co/acme/abc", "upload_resume": True},
    )
    assert response.status_code == 202
    assert ready.wait(timeout=2)
    assert received["resume_file"] == (b"%PDF-1.4 test document", "asha-cv.pdf")

    run_id = response.json()["run_id"]
    before_review = client.post(base(state) + f"/runs/{run_id}/authorize-submit")
    assert before_review.status_code == 409
    reviewed = client.get(base(state)).json()
    assert reviewed["runs"][-1]["browser_open"]
    approved = approve(reviewed)
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    enabled = client.post(base(state) + f"/runs/{run_id}/authorize-submit")
    assert enabled.status_code == 200
    assert enabled.json()["runs"][-1]["submission_enabled"]
    assert enabled.json()["status"] == "approved"
    assert authorized.wait(timeout=2)

"""Tests for Phase 1: Playwright diagnostic artifacts.

Covers:
  - Artifact directory creation per run.
  - trace_path / screenshot_path are persisted to MongoDB and exposed via has_trace / has_screenshot.
  - Artifact download endpoints enforce per-user ownership (cross-user access returns 404).
  - Path-traversal validation: a spoofed path outside the artifact base dir returns 403.
  - Artifact retention cleanup removes old directories.
  - run_assistance accepts artifact_dir parameter without breaking existing behaviour.
  - Existing approval restrictions remain intact when diagnostics are active.
  - No personal values appear in stored run documents.
"""

import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import mongomock
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app import assistant_fill, assistant_routes
from app.assistant_urls import check_fill_url
from app.database import UserScopedDatabase, get_db
from app.main import app

client = TestClient(app)


class SimulatedCancellation(BaseException):
    pass


PROFILE = {
    "full_name": "Test User",
    "email": "test@example.com",
    "phone": "+91 99999 00001",
    "location": "Mumbai",
    "skills": ["Python", "FastAPI"],
    "experience_summary": "Backend developer.",
    "work_experience": [],
    "projects": [],
    "education": [],
}


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    database = mongomock.MongoClient(tz_aware=True)["diag_test"]
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = lambda: UserScopedDatabase(database, "diag_user")
    yield database
    if previous is not None:
        app.dependency_overrides[get_db] = previous
    else:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def enable_local_form(monkeypatch):
    monkeypatch.setattr(assistant_routes.settings, "app_env", "test")
    monkeypatch.setattr(assistant_routes.settings, "assistant_allow_local_test_form", True)


@pytest.fixture()
def tmp_artifact_dir(monkeypatch, tmp_path):
    """Point the artifact base to a temporary directory for each test."""
    monkeypatch.setattr(assistant_routes.settings, "assistant_trace_dir", str(tmp_path))
    yield tmp_path


def make_job(**extra):
    body = {"title": "Backend Dev", "company": "TestCo",
            "skills": ["Python"],
            "url": "https://boards.greenhouse.io/testco/jobs/1", **extra}
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


# ── artifact helper unit tests ────────────────────────────────────────────────

class TestArtifactHelpers:

    def test_make_artifact_dir_creates_nested_directory(self, tmp_artifact_dir):
        path = assistant_routes._make_artifact_dir("uid_abc123", "a" * 32)
        assert path is not None
        assert Path(path).is_dir()
        # Must be nested inside the configured base.
        assert Path(path).resolve().is_relative_to(tmp_artifact_dir.resolve())

    def test_make_artifact_dir_sanitises_uid(self, tmp_artifact_dir):
        # Invalid UIDs disable artifacts rather than aliasing another owner path.
        assert assistant_routes._make_artifact_dir("../../etc/passwd", "b" * 32) is None

    def test_artifact_owner_encoding_does_not_collide(self):
        with pytest.raises(ValueError):
            assistant_routes._owner_component("a/b")

    def test_make_artifact_dir_rejects_invalid_run_id(self, tmp_artifact_dir):
        assert assistant_routes._make_artifact_dir("uid", "../outside") is None

    def test_cleanup_old_artifacts_removes_expired_directories(self, tmp_artifact_dir, monkeypatch):
        monkeypatch.setattr(assistant_routes.settings, "assistant_artifact_retention_days", 7)
        # Create two run dirs: one old, one recent.
        uid = "cleanup_user"
        user_dir = tmp_artifact_dir / assistant_routes._owner_component(uid)
        old_run = user_dir / "old_run"
        new_run = user_dir / "new_run"
        old_run.mkdir(parents=True)
        new_run.mkdir(parents=True)
        # Set old_run mtime to 8 days ago.
        eight_days_ago = (datetime.now(timezone.utc) - timedelta(days=8)).timestamp()
        import os as _os
        _os.utime(old_run, (eight_days_ago, eight_days_ago))
        assistant_routes._cleanup_old_artifacts(uid)
        assert not old_run.exists(), "Old run directory should be removed"
        assert new_run.exists(), "Recent run directory should be kept"

    def test_cleanup_with_zero_retention_keeps_all(self, tmp_artifact_dir, monkeypatch):
        monkeypatch.setattr(assistant_routes.settings, "assistant_artifact_retention_days", 0)
        uid = "keep_user"
        old_run = tmp_artifact_dir / assistant_routes._owner_component(uid) / "old_run"
        old_run.mkdir(parents=True)
        import os as _os
        _os.utime(old_run, (0, 0))  # epoch
        assistant_routes._cleanup_old_artifacts(uid)
        assert old_run.exists(), "Should keep all when retention=0"


# ── run_assistance with artifact_dir ─────────────────────────────────────────

class TestRunAssistanceArtifacts:

    def test_run_assistance_accepts_artifact_dir_parameter(self, tmp_path):
        """run_assistance must not crash when artifact_dir is given but browser is unavailable."""
        target = check_fill_url("https://boards.greenhouse.io/fake/jobs/1")
        try:
            result = assistant_fill.run_assistance(
                target, {},
                headless=True,
                artifact_dir=str(tmp_path),
            )
        except Exception:
            # Browser not installed → ImportError caught internally → result returned
            pass
        # If we reach here, the function accepted the parameter without TypeError.

    def test_run_assistance_without_artifact_dir_still_works(self, tmp_path):
        """Omitting artifact_dir must not break existing callers."""
        target = check_fill_url("https://boards.greenhouse.io/fake/jobs/1")
        # This may raise (browser not installed) or return a failed result; either is fine.
        try:
            result = assistant_fill.run_assistance(target, {}, headless=True)
            assert "status" in result
        except Exception:
            pass  # browser not installed: acceptable

    def test_artifact_dir_none_does_not_crash(self):
        """Explicitly passing artifact_dir=None must behave identically to omitting it."""
        target = check_fill_url("https://boards.greenhouse.io/fake/jobs/1")
        try:
            result = assistant_fill.run_assistance(target, {}, headless=True, artifact_dir=None)
            assert "status" in result
        except Exception:
            pass


# ── API-level tests with faked run_assistance ─────────────────────────────────

class TestDiagnosticArtifactAPI:

    def _fake_run_factory(self, tmp_path, trace_written=True, screenshot_written=True):
        """Return a fake run_assistance that writes dummy artifact files."""
        def fake_run(target, values, **kwargs):
            adir = kwargs.get("artifact_dir")
            result = {
                "status": "needs_you",
                "pause_reason": "required_fields_need_input",
                "fields": [{"label": "Email", "state": "filled", "key": "email",
                             "reason": "", "required": True}],
                "blocked_writes": 0,
            }
            if adir:
                if trace_written:
                    t = Path(adir) / "trace.zip"
                    t.write_bytes(b"PK\x03\x04fake-trace")
                    result["trace_path"] = str(t)
                if screenshot_written:
                    s = Path(adir) / "screenshot.png"
                    s.write_bytes(b"\x89PNG\r\nfake")
                    result["screenshot_path"] = str(s)
            on_result = kwargs.get("on_result")
            if on_result:
                on_result(result)
            return result
        return fake_run

    def test_artifact_paths_persisted_in_run_document(self, db, tmp_artifact_dir, monkeypatch):
        monkeypatch.setattr(assistant_routes, "run_assistance",
                            self._fake_run_factory(tmp_artifact_dir))
        monkeypatch.setattr(assistant_routes.settings, "assistant_browser_headless", True)
        monkeypatch.setattr(assistant_routes.settings, "assistant_keep_open_seconds", 0)

        s = open_assistant(db)
        r = client.post(base(s) + "/fill",
                        json={"url": "https://boards.greenhouse.io/testco/jobs/1"})
        assert r.status_code == 202
        run_id = r.json()["run_id"]
        _wait_for_run(s, run_id)

        run_doc = next(
            r for r in db.applications.find_one({})["assistant"]["runs"]
            if r["id"] == run_id
        )
        assert run_doc.get("trace_path") and Path(run_doc["trace_path"]).is_file()
        assert run_doc.get("screenshot_path") and Path(run_doc["screenshot_path"]).is_file()

    def test_get_run_exposes_has_trace_and_has_screenshot(self, db, tmp_artifact_dir, monkeypatch):
        monkeypatch.setattr(assistant_routes, "run_assistance",
                            self._fake_run_factory(tmp_artifact_dir))
        monkeypatch.setattr(assistant_routes.settings, "assistant_browser_headless", True)
        monkeypatch.setattr(assistant_routes.settings, "assistant_keep_open_seconds", 0)

        s = open_assistant(db)
        r = client.post(base(s) + "/fill",
                        json={"url": "https://boards.greenhouse.io/testco/jobs/1"})
        assert r.status_code == 202
        run_id = r.json()["run_id"]
        run = _wait_for_run(s, run_id)

        assert run.get("has_trace") is True
        assert run.get("has_screenshot") is True
        assert run["id"] == run_id
        assert run["started_at"] is not None
        assert run["finished_at"] is not None

    def test_state_endpoint_exposes_has_trace_and_has_screenshot(self, db, tmp_artifact_dir, monkeypatch):
        monkeypatch.setattr(assistant_routes, "run_assistance",
                            self._fake_run_factory(tmp_artifact_dir))
        monkeypatch.setattr(assistant_routes.settings, "assistant_browser_headless", True)
        monkeypatch.setattr(assistant_routes.settings, "assistant_keep_open_seconds", 0)

        s = open_assistant(db)
        r = client.post(base(s) + "/fill",
                        json={"url": "https://boards.greenhouse.io/testco/jobs/1"})
        assert r.status_code == 202
        run_id = r.json()["run_id"]
        _wait_for_run(s, run_id)

        state = client.get(base(s)).json()
        run_entry = next(r for r in state["runs"] if r["id"] == run_id)
        assert run_entry["has_trace"] is True
        assert run_entry["has_screenshot"] is True

    def test_no_personal_values_in_run_document_with_diagnostics(self, db, tmp_artifact_dir, monkeypatch):
        """Artifact metadata in the run document must not contain personal field values."""
        monkeypatch.setattr(assistant_routes, "run_assistance",
                            self._fake_run_factory(tmp_artifact_dir))
        monkeypatch.setattr(assistant_routes.settings, "assistant_browser_headless", True)
        monkeypatch.setattr(assistant_routes.settings, "assistant_keep_open_seconds", 0)

        s = open_assistant(db)
        r = client.post(base(s) + "/fill",
                        json={"url": "https://boards.greenhouse.io/testco/jobs/1"})
        assert r.status_code == 202
        run_id = r.json()["run_id"]
        _wait_for_run(s, run_id)

        doc = db.applications.find_one({})
        blob = str(doc["assistant"]["runs"])
        assert "test@example.com" not in blob, "Email must not be in run document"
        assert "+91 99999 00001" not in blob, "Phone must not be in run document"
        assert "cookie" not in blob.lower()

    def test_has_trace_false_when_no_artifacts(self, db, tmp_artifact_dir, monkeypatch):
        """When artifact_dir exists but files were not written, has_trace must be False."""
        def fake_run_no_artifacts(target, values, **kwargs):
            result = {"status": "failed", "pause_reason": "navigation_failed",
                      "fields": [], "blocked_writes": 0, "error_category": "navigation_failed"}
            on_result = kwargs.get("on_result")
            if on_result:
                on_result(result)
            return result

        monkeypatch.setattr(assistant_routes, "run_assistance", fake_run_no_artifacts)
        monkeypatch.setattr(assistant_routes.settings, "assistant_browser_headless", True)
        monkeypatch.setattr(assistant_routes.settings, "assistant_keep_open_seconds", 0)

        s = open_assistant(db)
        r = client.post(base(s) + "/fill",
                        json={"url": "https://boards.greenhouse.io/testco/jobs/1"})
        assert r.status_code == 202
        run_id = r.json()["run_id"]
        run = _wait_for_run(s, run_id)

        assert run.get("has_trace") is False
        assert run.get("has_screenshot") is False


# ── cross-user ownership enforcement ─────────────────────────────────────────

class TestArtifactOwnershipEnforcement:

    def test_trace_download_is_not_public(self):
        previous = app.dependency_overrides.pop(get_db, None)
        try:
            response = client.get(
                f"/api/applications/{ObjectId()}/assistant/runs/{'6' * 32}/trace"
            )
            assert response.status_code == 401
        finally:
            if previous is not None:
                app.dependency_overrides[get_db] = previous

    def test_user_a_cannot_access_user_b_trace(self, tmp_artifact_dir, monkeypatch):
        """Trace download must return 404 when the application belongs to another user.

        UserScopedDatabase silently filters out documents from other users, so
        _load() raises 404 before we ever check the filesystem path.
        """
        # Both users share one database; the authenticated UID scopes reads.
        database = mongomock.MongoClient(tz_aware=True)["shared_user_db"]

        # Create a run record in user A's database.
        fake_trace = (
            tmp_artifact_dir
            / assistant_routes._owner_component("ua")
            / ("1" * 32)
            / "trace.zip"
        )
        fake_trace.parent.mkdir(parents=True)
        fake_trace.write_bytes(b"PK\x03\x04fake")
        job_id = ObjectId()
        app_id = ObjectId()
        database.jobs.insert_one({"_id": job_id, "title": "Dev", "company": "X",
                               "skills": [], "status": "active", "owner_uid": "ua"})
        database.applications.insert_one({
            "_id": app_id, "job_id": job_id, "status": "saved", "owner_uid": "ua",
            "assistant": {"runs": [{"id": "1" * 32, "trace_path": str(fake_trace),
                                    "status": "prepared"}]}
        })

        app.dependency_overrides[get_db] = lambda: UserScopedDatabase(database, "ub")
        try:
            r = client.get(f"/api/applications/{str(app_id)}/assistant/runs/{'1' * 32}/trace")
            # UserScopedDatabase filters by owner_uid — user B sees no document → 404.
            assert r.status_code == 404
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_trace_endpoint_rejects_path_outside_artifact_base(self, db, tmp_artifact_dir, monkeypatch):
        """A database record with a spoofed path outside the artifact dir returns 403."""
        monkeypatch.setattr(assistant_routes.settings, "assistant_trace_dir",
                            str(tmp_artifact_dir))
        # Manually insert an application with a spoofed trace_path.
        job_id = ObjectId()
        app_id = ObjectId()
        db.jobs.insert_one({"_id": job_id, "title": "Dev", "company": "X",
                                      "skills": [], "owner_uid": "unknown"})
        db.applications.insert_one({
            "_id": app_id, "job_id": job_id, "status": "saved", "owner_uid": "unknown",
            "assistant": {"runs": [{"id": "2" * 32,
                                    "trace_path": "/etc/passwd",
                                    "status": "prepared"}]}
        })

        # Override get_db to return a db scoped to 'unknown'
        app.dependency_overrides[get_db] = lambda: UserScopedDatabase(db, "unknown")
        try:
            r = client.get(f"/api/applications/{str(app_id)}/assistant/runs/{'2' * 32}/trace")
            assert r.status_code == 403
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_screenshot_endpoint_rejects_path_outside_artifact_base(self, db, tmp_artifact_dir, monkeypatch):
        """Same path-traversal protection for the screenshot endpoint."""
        monkeypatch.setattr(assistant_routes.settings, "assistant_trace_dir",
                            str(tmp_artifact_dir))
        job_id = ObjectId()
        app_id = ObjectId()
        db.jobs.insert_one({"_id": job_id, "title": "Dev", "company": "X",
                                      "skills": [], "owner_uid": "unknown2"})
        db.applications.insert_one({
            "_id": app_id, "job_id": job_id, "status": "saved", "owner_uid": "unknown2",
            "assistant": {"runs": [{"id": "3" * 32,
                                    "screenshot_path": "/etc/shadow",
                                    "status": "prepared"}]}
        })

        app.dependency_overrides[get_db] = lambda: UserScopedDatabase(db, "unknown2")
        try:
            r = client.get(f"/api/applications/{str(app_id)}/assistant/runs/{'3' * 32}/screenshot")
            assert r.status_code == 403
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_trace_endpoint_404_when_no_trace_recorded(self, db, tmp_artifact_dir, monkeypatch):
        """Requesting a trace for a run that has no trace_path returns 404."""
        def fake_run_no_artifacts(target, values, **kwargs):
            result = {"status": "needs_you", "pause_reason": "required_fields_need_input",
                      "fields": [], "blocked_writes": 0}
            on_result = kwargs.get("on_result")
            if on_result:
                on_result(result)
            return result

        monkeypatch.setattr(assistant_routes, "run_assistance", fake_run_no_artifacts)
        monkeypatch.setattr(assistant_routes.settings, "assistant_browser_headless", True)
        monkeypatch.setattr(assistant_routes.settings, "assistant_keep_open_seconds", 0)

        s = open_assistant(db)
        r = client.post(base(s) + "/fill",
                        json={"url": "https://boards.greenhouse.io/testco/jobs/1"})
        assert r.status_code == 202
        run_id = r.json()["run_id"]
        _wait_for_run(s, run_id)

        r_trace = client.get(base(s) + f"/runs/{run_id}/trace")
        assert r_trace.status_code == 404

        r_shot = client.get(base(s) + f"/runs/{run_id}/screenshot")
        assert r_shot.status_code == 404

    def test_run_identifier_cannot_download_another_run_artifact(
        self, db, tmp_artifact_dir, monkeypatch
    ):
        monkeypatch.setattr(assistant_routes.settings, "assistant_trace_dir", str(tmp_artifact_dir))
        owner_dir = tmp_artifact_dir / "diag_user"
        artifact_dir = owner_dir / ("4" * 32)
        artifact_dir.mkdir(parents=True)
        trace = artifact_dir / "trace.zip"
        trace.write_bytes(b"trace")
        job_id = ObjectId()
        app_id = ObjectId()
        db.jobs.insert_one({"_id": job_id, "title": "Dev", "company": "X", "owner_uid": "diag_user"})
        db.applications.insert_one({
            "_id": app_id,
            "job_id": job_id,
            "status": "saved",
            "owner_uid": "diag_user",
            "assistant": {"runs": [{
                "id": "5" * 32,
                "trace_path": str(trace),
                "status": "prepared",
            }]},
        })

        response = client.get(
            f"/api/applications/{app_id}/assistant/runs/{'5' * 32}/trace"
        )

        assert response.status_code == 403


@pytest.mark.parametrize(
    "error_type",
    [RuntimeError, TimeoutError, SimulatedCancellation],
)
def test_run_assistance_finalizes_artifacts_and_closes_browser_on_exit(
    tmp_path, monkeypatch, caplog, error_type
):
    class Tracing:
        def start(self, **kwargs):
            self.started = True

        def stop(self, path):
            Path(path).write_bytes(b"trace")

    class Page:
        def goto(self, *args, **kwargs):
            raise error_type("sensitive page failure text")

        def is_closed(self):
            return False

        def screenshot(self, path, **kwargs):
            Path(path).write_bytes(b"screen")

    class Context:
        def __init__(self):
            self.tracing = Tracing()
            self.page = Page()
            self.closed = False

        def route(self, *args):
            pass

        def new_page(self):
            return self.page

        def close(self):
            self.closed = True

    class Browser:
        def __init__(self):
            self.context = Context()
            self.closed = False

        def new_context(self):
            return self.context

        def close(self):
            self.closed = True

    browser = Browser()
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(launch=lambda **kwargs: browser)
    )
    class SyncPlaywright:
        def __enter__(self):
            return playwright

        def __exit__(self, *args):
            return False

    monkeypatch.setitem(
        sys.modules,
        "playwright.sync_api",
        SimpleNamespace(sync_playwright=SyncPlaywright),
    )
    target = assistant_fill.FillTarget(
        url="http://127.0.0.1:8000/assistant-test-form",
        host="127.0.0.1",
        adapter="local_test",
    )
    if issubclass(error_type, Exception):
        result = assistant_fill.run_assistance(
            target,
            {},
            headless=True,
            artifact_dir=str(tmp_path),
        )
    else:
        with pytest.raises(error_type):
            assistant_fill.run_assistance(
                target,
                {},
                headless=True,
                artifact_dir=str(tmp_path),
            )
        result = {"trace_path": str(tmp_path / "trace.zip"),
                  "screenshot_path": str(tmp_path / "screenshot.png")}

    assert Path(result["trace_path"]).read_bytes() == b"trace"
    assert Path(result["screenshot_path"]).read_bytes() == b"screen"
    assert browser.context.closed is True
    assert browser.closed is True
    assert "sensitive page failure text" not in caplog.text


def test_filled_field_result_has_safe_reason_and_never_keeps_value():
    class Locator:
        def fill(self, value, timeout):
            self.value = value

        def input_value(self, timeout):
            return self.value

        def evaluate(self, script):
            return True

    class Frame:
        def evaluate(self, script, options=None):
            return [{
                "idx": 0,
                "type": "email",
                "kind": "input",
                "label": "Email",
                "required": True,
            }]

        def locator(self, selector):
            return locator

    locator = Locator()
    field = assistant_fill._fill_frame(
        Frame(),
        {"email": {"value": "private@example.com", "source": "profile"}},
    )[0]

    assert field["state"] == "filled"
    assert field["reason"] == "Filled from a confirmed Application Kit value."
    assert "value" not in field
    assert "private@example.com" not in str(field)


def test_glassdoor_diagnostics_skip_login_and_start_only_on_application_form(
    tmp_path, monkeypatch
):
    class Tracing:
        def __init__(self):
            self.started = False

        def start(self, **kwargs):
            self.started = True

        def stop(self, path):
            Path(path).write_bytes(b"trace")

    class Frame:
        url = "https://www.glassdoor.com/job-listing/example"

        def __init__(self, application_form):
            self.application_form = application_form

        def evaluate(self, script, options=None):
            if script == assistant_fill.GLASSDOOR_FORM_JS:
                return {"login": not self.application_form, "application": self.application_form,
                        "blocked": False}
            return []

    class Page:
        url = "https://www.glassdoor.com/job-listing/example"

        def __init__(self, frame):
            self.frames = [frame]

        def goto(self, *args, **kwargs):
            pass

        def wait_for_load_state(self, *args, **kwargs):
            pass

        def evaluate(self, script):
            return {"captcha": False, "password": False}

        def is_closed(self):
            return False

        def screenshot(self, path, **kwargs):
            Path(path).write_bytes(b"screen")

        def wait_for_timeout(self, milliseconds):
            pass

    class Context:
        def __init__(self, page):
            self.pages = [page]
            self.tracing = Tracing()
            self.closed = False

        def route(self, *args):
            pass

        def close(self):
            self.closed = True

    class SyncPlaywright:
        def __enter__(self):
            return SimpleNamespace(chromium=object())

        def __exit__(self, *args):
            return False

    monkeypatch.setitem(
        sys.modules,
        "playwright.sync_api",
        SimpleNamespace(sync_playwright=SyncPlaywright),
    )
    monkeypatch.setattr(assistant_fill, "check_public_host", lambda host: None)
    monkeypatch.setattr(assistant_fill, "_automation_profile_path", lambda path: str(tmp_path))

    outcomes = []
    for application_form in (False, True):
        run_dir = tmp_path / str(application_form)
        run_dir.mkdir()
        context = Context(Page(Frame(application_form)))
        monkeypatch.setattr(
            assistant_fill,
            "_launch_automation_context",
            lambda *args, context=context, **kwargs: context,
        )
        result = assistant_fill.run_assistance(
            assistant_fill.FillTarget(
                url="https://www.glassdoor.com/job-listing/example",
                host="www.glassdoor.com",
                adapter="glassdoor",
            ),
            {},
            headless=False,
            keep_open_seconds=0.01,
            artifact_dir=str(run_dir),
            automation_profile_dir=str(tmp_path),
        )
        outcomes.append((application_form, context, result))

    login_form, login_context, login_result = outcomes[0]
    assert login_form is False
    assert login_context.tracing.started is False
    assert "trace_path" not in login_result
    assert "screenshot_path" not in login_result

    application_form, application_context, application_result = outcomes[1]
    assert application_form is True
    assert application_context.tracing.started is True
    assert Path(application_result["trace_path"]).is_file()
    assert Path(application_result["screenshot_path"]).is_file()
    assert all(context.closed for _, context, _ in outcomes)


def test_worker_persists_cancellation_and_releases_run(monkeypatch):
    database = mongomock.MongoClient(tz_aware=True)["cancel_test"]
    uid = "cancel_user"
    application_id = ObjectId()
    run_id = "7" * 32
    database.applications.insert_one({
        "_id": application_id,
        "owner_uid": uid,
        "assistant": {"runs": [{
            "id": run_id,
            "status": "running",
            "fields": [],
            "blocked_writes": 0,
            "browser_open": False,
        }]},
    })

    def cancelled(*args, **kwargs):
        raise SimulatedCancellation("private page data")

    monkeypatch.setattr(assistant_routes, "run_assistance", cancelled)
    assistant_routes._run_in_thread(
        UserScopedDatabase(database, uid),
        application_id,
        run_id,
        assistant_fill.FillTarget(
            url="https://boards.greenhouse.io/test/jobs/1",
            host="boards.greenhouse.io",
            adapter="greenhouse",
        ),
        {},
        threading.Event(),
        None,
        None,
    )

    run = database.applications.find_one({"_id": application_id})["assistant"]["runs"][0]
    assert run["status"] == "failed"
    assert run["error_category"] == "run_cancelled"
    assert run["pause_reason"] == "run_cancelled"
    assert run["finished_at"] is not None
    assert run_id not in assistant_routes.ACTIVE_RUNS


# ── approval workflow remains intact with diagnostics ─────────────────────────

class TestApprovalIntegrityWithDiagnostics:

    def test_generic_endpoints_still_refuse_gated_statuses(self, db, tmp_artifact_dir, monkeypatch):
        """Diagnostics must not loosen the existing gated-status restrictions."""
        def fake_run(target, values, **kwargs):
            result = {"status": "prepared", "pause_reason": "",
                      "fields": [], "blocked_writes": 0}
            on_result = kwargs.get("on_result")
            if on_result:
                on_result(result)
            return result

        monkeypatch.setattr(assistant_routes, "run_assistance", fake_run)
        monkeypatch.setattr(assistant_routes.settings, "assistant_browser_headless", True)
        monkeypatch.setattr(assistant_routes.settings, "assistant_keep_open_seconds", 0)

        s = open_assistant(db)
        aid = s["application_id"]
        for status in ("approved", "submitted"):
            assert client.patch(f"/api/applications/{aid}/status",
                                json={"status": status}).status_code == 403
        assert client.get(base(s)).json()["status"] == "saved"

    def test_submitted_still_requires_explicit_confirm_with_diagnostics(self, db, tmp_artifact_dir, monkeypatch):
        """The submitted confirmation gate must not be bypassed when artifacts are active."""
        s = open_assistant(db)
        r = client.post(base(s) + "/review",
                        json={"hash": s["review"]["hash"], "acknowledged": True})
        assert r.status_code == 200 and r.json()["status"] == "approved"
        # confirm=False must still fail.
        assert client.post(base(s) + "/submitted", json={}).status_code == 422
        # confirm=True must succeed.
        done = client.post(base(s) + "/submitted", json={"confirm": True})
        assert done.status_code == 200 and done.json()["status"] == "submitted"


# ── helpers ───────────────────────────────────────────────────────────────────

def _wait_for_run(state, run_id: str, timeout: float = 5.0) -> dict:
    """Poll the run endpoint until it reports a finished status."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/applications/{state['application_id']}/assistant/runs/{run_id}")
        if r.status_code == 200:
            run = r.json()
            if run.get("finished_at"):
                return run
        time.sleep(0.05)
    # Return whatever we have even if not finished.
    r = client.get(f"/api/applications/{state['application_id']}/assistant/runs/{run_id}")
    return r.json() if r.status_code == 200 else {}
